#!/usr/bin/env python3
import argparse
import signal
import threading
import time
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Tuple

import cv2
import numpy as np

from hailo_platform import (
    ConfigureParams,
    FormatType,
    HailoStreamInterface,
    HEF,
    InferVStreams,
    InputVStreamParams,
    OutputVStreamParams,
    VDevice,
)


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    x = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(x)
    return e / np.sum(e, axis=axis, keepdims=True)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float, max_det: int) -> List[int]:
    if len(boxes) == 0:
        return []

    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0 and len(keep) < max_det:
        i = int(order[0])
        keep.append(i)

        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h

        union = areas[i] + areas[order[1:]] - inter
        iou = np.where(union > 0, inter / union, 0.0)

        inds = np.where(iou <= iou_threshold)[0]
        order = order[inds + 1]

    return keep


class HailoDetector:
    def __init__(
        self,
        hef_path: str,
        score_thr: float,
        iou_thr: float,
        max_det: int,
        rotate_180: bool = False,
        draw_boxes: bool = True,
        box_color: Tuple[int, int, int] = (0, 255, 0),
        label_color: Tuple[int, int, int] = (0, 255, 0),
        box_thickness: int = 2,
        show_labels: bool = True,
    ):
        self.hef_path = hef_path
        self.score_thr = score_thr
        self.iou_thr = iou_thr
        self.max_det = max_det
        self.rotate_180 = rotate_180
        self.draw_boxes = draw_boxes
        self.box_color = box_color
        self.label_color = label_color
        self.box_thickness = int(max(1, box_thickness))
        self.show_labels = show_labels

        self.stack = ExitStack()
        self.vdevice = None
        self.network_group = None
        self.infer_pipeline = None
        self.input_name = None
        self.model_h = None
        self.model_w = None

        self.reg_by_stride: Dict[int, Tuple[str, float, float]] = {}
        self.cls_by_stride: Dict[int, Tuple[str, float, float]] = {}

        self._init_hailo()

    def _init_hailo(self):
        hef = HEF(self.hef_path)
        input_infos = hef.get_input_vstream_infos()
        output_infos = hef.get_output_vstream_infos()

        if len(input_infos) != 1:
            raise RuntimeError("Dieses Skript erwartet genau 1 Input-VStream im HEF.")

        in_info = input_infos[0]
        self.input_name = in_info.name
        self.model_h, self.model_w, _ = in_info.shape

        # Erwartet 3 Scale-Outputs: pro Scale eine Class-Map (C=1) und eine Reg-Map (C=64)
        for info in output_infos:
            h, w, c = info.shape
            if h <= 0 or w <= 0:
                continue
            stride_h = self.model_h // h
            stride_w = self.model_w // w
            if stride_h != stride_w:
                continue
            stride = stride_h

            qp_scale = float(info.quant_info.qp_scale)
            qp_zp = float(info.quant_info.qp_zp)

            if c == 1:
                self.cls_by_stride[stride] = (info.name, qp_scale, qp_zp)
            elif c == 64:
                self.reg_by_stride[stride] = (info.name, qp_scale, qp_zp)

        common_strides = sorted(set(self.cls_by_stride.keys()) & set(self.reg_by_stride.keys()))
        if not common_strides:
            raise RuntimeError("Konnte keine passenden cls/reg Output-Paare im HEF finden.")

        # Auf gemeinsame Strides begrenzen
        self.cls_by_stride = {s: self.cls_by_stride[s] for s in common_strides}
        self.reg_by_stride = {s: self.reg_by_stride[s] for s in common_strides}

        self.vdevice = self.stack.enter_context(VDevice())
        configure_params = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
        self.network_group = self.vdevice.configure(hef, configure_params)[0]

        input_params = InputVStreamParams.make_from_network_group(self.network_group, format_type=FormatType.UINT8)
        output_params = OutputVStreamParams.make_from_network_group(self.network_group, format_type=FormatType.UINT8)

        self.infer_pipeline = self.stack.enter_context(InferVStreams(self.network_group, input_params, output_params))
        self.stack.enter_context(self.network_group.activate(self.network_group.create_params()))

    @staticmethod
    def _center_crop_to_square(frame: np.ndarray) -> Tuple[np.ndarray, Tuple[int, int, int]]:
        h, w = frame.shape[:2]
        side = min(h, w)
        x0 = (w - side) // 2
        y0 = (h - side) // 2
        crop = frame[y0:y0 + side, x0:x0 + side]
        return crop, (x0, y0, side)

    def _decode_scale(
        self,
        reg: np.ndarray,
        cls: np.ndarray,
        stride: int,
        reg_scale: float,
        reg_zp: float,
        cls_scale: float,
        cls_zp: float,
    ) -> Tuple[np.ndarray, np.ndarray]:
        # reg: HxWx64, cls: HxWx1
        h, w, _ = reg.shape

        cls_f = (cls.astype(np.float32) - cls_zp) * cls_scale
        scores = sigmoid(cls_f[..., 0])

        ys, xs = np.where(scores >= self.score_thr)
        if ys.size == 0:
            return np.empty((0, 4), dtype=np.float32), np.empty((0,), dtype=np.float32)

        reg_sel = reg[ys, xs, :].astype(np.float32)
        reg_sel = (reg_sel - reg_zp) * reg_scale
        reg_sel = reg_sel.reshape(-1, 4, 16)

        probs = softmax(reg_sel, axis=2)
        bins = np.arange(16, dtype=np.float32).reshape(1, 1, 16)
        dists = np.sum(probs * bins, axis=2) * float(stride)  # Nx4 [l,t,r,b]

        cx = (xs.astype(np.float32) + 0.5) * float(stride)
        cy = (ys.astype(np.float32) + 0.5) * float(stride)

        x1 = cx - dists[:, 0]
        y1 = cy - dists[:, 1]
        x2 = cx + dists[:, 2]
        y2 = cy + dists[:, 3]

        boxes = np.stack([x1, y1, x2, y2], axis=1)
        sc = scores[ys, xs].astype(np.float32)
        return boxes, sc

    def infer(self, frame_bgr: np.ndarray) -> Tuple[np.ndarray, List[Tuple[float, float, float, float, float]]]:
        if self.rotate_180:
            frame_bgr = cv2.rotate(frame_bgr, cv2.ROTATE_180)

        crop, (x0, y0, side) = self._center_crop_to_square(frame_bgr)
        inp = cv2.resize(crop, (self.model_w, self.model_h), interpolation=cv2.INTER_LINEAR)
        inp = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB)

        data = np.expand_dims(inp.astype(np.uint8), axis=0)
        outputs = self.infer_pipeline.infer({self.input_name: data})

        all_boxes = []
        all_scores = []

        for stride in sorted(self.cls_by_stride.keys()):
            reg_name, reg_scale, reg_zp = self.reg_by_stride[stride]
            cls_name, cls_scale, cls_zp = self.cls_by_stride[stride]

            reg = outputs[reg_name][0]
            cls = outputs[cls_name][0]

            # Falls FCR intern anders geliefert wird, in HWC bringen
            if reg.ndim == 3 and reg.shape[-1] != 64 and reg.shape[0] == 64:
                reg = np.transpose(reg, (1, 2, 0))
            if cls.ndim == 3 and cls.shape[-1] != 1 and cls.shape[0] == 1:
                cls = np.transpose(cls, (1, 2, 0))

            boxes, scores = self._decode_scale(reg, cls, stride, reg_scale, reg_zp, cls_scale, cls_zp)
            if len(boxes) > 0:
                all_boxes.append(boxes)
                all_scores.append(scores)

        if not all_boxes:
            return frame_bgr, []

        boxes = np.concatenate(all_boxes, axis=0)
        scores = np.concatenate(all_scores, axis=0)

        boxes[:, 0::2] = np.clip(boxes[:, 0::2], 0, self.model_w - 1)
        boxes[:, 1::2] = np.clip(boxes[:, 1::2], 0, self.model_h - 1)

        keep = nms(boxes, scores, self.iou_thr, self.max_det)
        boxes = boxes[keep]
        scores = scores[keep]

        out = frame_bgr.copy()
        detections: List[Tuple[float, float, float, float, float]] = []

        scale = float(side) / float(self.model_w)
        for b, s in zip(boxes, scores):
            x1 = int(b[0] * scale + x0)
            y1 = int(b[1] * scale + y0)
            x2 = int(b[2] * scale + x0)
            y2 = int(b[3] * scale + y0)

            x1 = max(0, min(x1, out.shape[1] - 1))
            x2 = max(0, min(x2, out.shape[1] - 1))
            y1 = max(0, min(y1, out.shape[0] - 1))
            y2 = max(0, min(y2, out.shape[0] - 1))

            detections.append((float(x1), float(y1), float(x2), float(y2), float(s)))

            if self.draw_boxes:
                cv2.rectangle(out, (x1, y1), (x2, y2), self.box_color, self.box_thickness)
                if self.show_labels:
                    label = f"obj {s:.2f}"
                    cv2.putText(
                        out,
                        label,
                        (x1, max(0, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        self.label_color,
                        max(1, self.box_thickness),
                    )

        return out, detections

    def close(self):
        self.stack.close()


class SharedFrame:
    def __init__(self):
        self._lock = threading.Lock()
        self._jpg = None

    def set(self, jpg_bytes: bytes):
        with self._lock:
            self._jpg = jpg_bytes

    def get(self):
        with self._lock:
            return self._jpg


class StreamHandler(BaseHTTPRequestHandler):
    shared = None

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>Hailo Kamera Stream</title>"
                "<style>body{margin:0;background:#101418;color:#e8eef4;font-family:Arial,sans-serif;}"
                ".bar{padding:12px 16px;background:#18222d;font-weight:600;}"
                ".wrap{display:flex;justify-content:center;padding:12px;}"
                "img{max-width:96vw;max-height:88vh;border:2px solid #31485f;border-radius:8px;}</style>"
                "</head><body><div class='bar'>Hailo Detection Stream</div>"
                "<div class='wrap'><img src='/stream.mjpg'></div></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        if self.path in ("/stream", "/stream.mjpg"):
            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    frame = self.shared.get()
                    if frame is None:
                        time.sleep(0.02)
                        continue
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        self.send_error(404)

    def log_message(self, fmt, *args):
        # Ruhiger Server-Output
        return


def make_status_image(width: int, height: int, lines: List[str]) -> np.ndarray:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (20, 24, 28)
    y = 48
    for i, line in enumerate(lines):
        scale = 0.8 if i == 0 else 0.6
        color = (80, 220, 255) if i == 0 else (220, 220, 220)
        cv2.putText(img, line, (24, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)
        y += 34
    return img


def _open_capture_v4l2(index: int, width: int, height: int):
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, 30)
    return cap


def _open_capture_gst(width: int, height: int):
    pipeline = (
        f"libcamerasrc ! video/x-raw,width={width},height={height},framerate=30/1,format=RGB ! "
        "videoconvert ! video/x-raw,format=BGR ! appsink drop=true max-buffers=1 sync=false"
    )
    cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
    if not cap.isOpened():
        return None
    return cap


def open_capture(source: str, width: int, height: int):
    # source examples:
    #   auto
    #   0
    #   v4l2:0
    #   gst:<gstreamer pipeline>
    #   file:/path/video.mp4
    if source == "auto":
        # On Raspberry Pi 5 CSI cameras, libcamera pipeline is the preferred path.
        cap = _open_capture_gst(width, height)
        if cap is not None:
            return cap, "gst:libcamerasrc"
        cap = _open_capture_v4l2(0, width, height)
        if cap is not None:
            return cap, "v4l2:0"
        raise RuntimeError("Keine Kamera gefunden (v4l2 und libcamerasrc fehlgeschlagen).")

    if source.startswith("v4l2:"):
        index = int(source.split(":", 1)[1])
        cap = _open_capture_v4l2(index, width, height)
        if cap is None:
            raise RuntimeError(f"V4L2 Kamera konnte nicht geöffnet werden (index={index}).")
        return cap, f"v4l2:{index}"

    if source.isdigit():
        index = int(source)
        cap = _open_capture_v4l2(index, width, height)
        if cap is None:
            raise RuntimeError(f"V4L2 Kamera konnte nicht geöffnet werden (index={index}).")
        return cap, f"v4l2:{index}"

    if source.startswith("gst:"):
        pipeline = source.split(":", 1)[1]
        cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if not cap.isOpened():
            raise RuntimeError("GStreamer-Pipeline konnte nicht geöffnet werden.")
        return cap, "gst:custom"

    if source.startswith("file:"):
        path = source.split(":", 1)[1]
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise RuntimeError(f"Video-Datei konnte nicht geöffnet werden: {path}")
        return cap, f"file:{path}"

    raise RuntimeError(f"Unbekannte --source Angabe: {source}")


def run_capture_loop(
    stop_event: threading.Event,
    shared: SharedFrame,
    hef_path: str,
    source: str,
    width: int,
    height: int,
    jpeg_quality: int,
    score_thr: float,
    iou_thr: float,
    max_det: int,
    rotate_180: bool,
):
    detector = None
    cap = None
    fps_hist = []
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)]

    try:
        detector = HailoDetector(
            hef_path,
            score_thr=score_thr,
            iou_thr=iou_thr,
            max_det=max_det,
            rotate_180=rotate_180,
        )
        cap, opened_source = open_capture(source, width, height)
        print(f"Kameraquelle geöffnet: {opened_source}", flush=True)

        fail_reads = 0
        while not stop_event.is_set():
            t0 = time.time()
            ok, frame = cap.read()
            if not ok:
                fail_reads += 1
                if fail_reads % 50 == 0:
                    print("Warnung: Kamera liefert keine Frames.", flush=True)
                status = make_status_image(
                    960, 540,
                    [
                        "Kamera liefert keine Frames",
                        f"source={source}",
                        "Prüfe Kameraanschluss / Quelle",
                    ],
                )
                ok_enc, jpg = cv2.imencode(".jpg", status, encode_param)
                if ok_enc:
                    shared.set(jpg.tobytes())
                time.sleep(0.05)
                continue
            fail_reads = 0

            vis, detections = detector.infer(frame)

            dt = max(1e-6, time.time() - t0)
            fps = 1.0 / dt
            fps_hist.append(fps)
            if len(fps_hist) > 30:
                fps_hist.pop(0)
            fps_avg = sum(fps_hist) / len(fps_hist)

            cv2.putText(vis, f"FPS {fps_avg:.1f} | det {len(detections)}", (12, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)

            ok_enc, jpg = cv2.imencode('.jpg', vis, encode_param)
            if ok_enc:
                shared.set(jpg.tobytes())

    except Exception as e:
        print(f"Capture-Loop Fehler: {e}", flush=True)
        while not stop_event.is_set():
            status = make_status_image(
                960, 540,
                [
                    "Capture/Inference Fehler",
                    str(e),
                    "Starte mit anderer --source erneut",
                ],
            )
            ok_enc, jpg = cv2.imencode(".jpg", status, encode_param)
            if ok_enc:
                shared.set(jpg.tobytes())
            time.sleep(0.3)

    finally:
        if cap is not None:
            cap.release()
        if detector is not None:
            detector.close()


def main():
    parser = argparse.ArgumentParser(description="Lokaler Hailo Kamera-Webserver mit Box-Overlay")
    parser.add_argument("--hef", default="/home/gast/model.hef", help="Pfad zur HEF-Datei")
    parser.add_argument("--host", default="0.0.0.0", help="Bind-Adresse")
    parser.add_argument("--port", type=int, default=8080, help="HTTP-Port")
    parser.add_argument(
        "--source",
        default="auto",
        help="Kameraquelle: auto | 0 | v4l2:0 | gst:<pipeline> | file:/pfad/video.mp4",
    )
    parser.add_argument("--width", type=int, default=1280, help="Capture-Breite")
    parser.add_argument("--height", type=int, default=720, help="Capture-Höhe")
    parser.add_argument("--jpeg-quality", type=int, default=80, help="JPEG-Qualität 1..100")
    parser.add_argument("--score-thr", type=float, default=0.45, help="Score-Schwelle")
    parser.add_argument("--iou-thr", type=float, default=0.45, help="NMS IoU-Schwelle")
    parser.add_argument("--max-det", type=int, default=100, help="Max. Detections pro Frame")
    parser.add_argument(
        "--rotate-180",
        dest="rotate_180",
        action="store_true",
        help="Kamerabild vor Inferenz um 180 Grad drehen.",
    )
    parser.add_argument(
        "--no-rotate-180",
        dest="rotate_180",
        action="store_false",
        help="180-Grad-Rotation deaktivieren.",
    )
    parser.set_defaults(rotate_180=True)
    args = parser.parse_args()

    shared = SharedFrame()
    StreamHandler.shared = shared
    stop_event = threading.Event()

    worker = threading.Thread(
        target=run_capture_loop,
        args=(
            stop_event,
            shared,
            args.hef,
            args.source,
            args.width,
            args.height,
            args.jpeg_quality,
            args.score_thr,
            args.iou_thr,
            args.max_det,
            args.rotate_180,
        ),
        daemon=True,
    )
    worker.start()

    server = ThreadingHTTPServer((args.host, args.port), StreamHandler)

    def _shutdown(*_):
        stop_event.set()
        # shutdown() must not run from the serve_forever thread itself.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"Server läuft: http://{args.host}:{args.port}")
    print(f"HEF: {args.hef}")
    print(f"Source: {args.source}")
    print(f"Rotate180: {args.rotate_180}")
    print("Zum Beenden: Ctrl+C")

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        stop_event.set()
        worker.join(timeout=2.0)
        server.server_close()


if __name__ == "__main__":
    main()

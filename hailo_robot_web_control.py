#!/usr/bin/env python3
"""Unified Hailo web UI: live detection stream + EV3 robot start/stop + config editor."""

import argparse
import json
import os
import pwd
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse


def _prepend_env_path(var_name: str, value: str) -> None:
    current = os.environ.get(var_name, "")
    if not current:
        os.environ[var_name] = value
        return
    parts = [p for p in current.split(":") if p]
    if value in parts:
        return
    os.environ[var_name] = f"{value}:{current}"


def _configure_local_libcamera_runtime() -> None:
    lc_prefix = os.environ.get("LC_PREFIX", "/home/gast/.local/libcamera-rpi")
    lib_dir = os.path.join(lc_prefix, "lib", "aarch64-linux-gnu")
    gst_dir = os.path.join(lib_dir, "gstreamer-1.0")
    ipa_dir = os.path.join(lib_dir, "libcamera", "ipa")
    proxy_dir = os.path.join(lc_prefix, "libexec", "libcamera")

    if os.path.isdir(lib_dir):
        _prepend_env_path("LD_LIBRARY_PATH", lib_dir)
    if os.path.isdir(gst_dir):
        _prepend_env_path("GST_PLUGIN_PATH", gst_dir)
    if os.path.isdir(ipa_dir) and "LIBCAMERA_IPA_MODULE_PATH" not in os.environ:
        os.environ["LIBCAMERA_IPA_MODULE_PATH"] = ipa_dir
    if os.path.isdir(proxy_dir) and "LIBCAMERA_IPA_PROXY_PATH" not in os.environ:
        os.environ["LIBCAMERA_IPA_PROXY_PATH"] = proxy_dir


def _prepare_user_site_for_sudo() -> None:
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:
        return
    try:
        sudo_home = pwd.getpwnam(sudo_user).pw_dir
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        user_site = os.path.join(sudo_home, ".local", "lib", f"python{py_ver}", "site-packages")
        if os.path.isdir(user_site) and user_site not in sys.path:
            sys.path.insert(0, user_site)
    except Exception:
        pass


_prepare_user_site_for_sudo()
_configure_local_libcamera_runtime()

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from hailo_web_detect_server import open_capture  # noqa: E402


def ts_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


DEFAULT_CONFIG: Dict[str, Any] = {
    "hef": "/home/gast/model.hef",
    "source": "auto",
    "width": 1280,
    "height": 720,
    "jpeg_quality": 80,
    "score_thr": 0.45,
    "iou_thr": 0.45,
    "max_det": 100,
    "rotate_180": True,
    "box_color": "#ff0000",
    "label_color": "#ff0000",
    "box_thickness": 2,
    "show_labels": True,
    "robot_source_mode": "shared",
    "robot_source_manual": "auto",
    "butter_thr": 0.75,
    "track_thr": 0.55,
    "confirm_frames": 2,
    "left_port": "A",
    "right_port": "D",
    "lift_port": "C",
    "search_turn_speed": 8.0,
    "search_min_turn_sec": 0.22,
    "search_max_turn_sec": 0.45,
    "search_pause_min_sec": 0.35,
    "search_pause_max_sec": 0.80,
    "search_forward_speed": 20.0,
    "search_min_forward_sec": 0.25,
    "search_max_forward_sec": 0.8,
    "approach_speed": 24.0,
    "turn_kp": 40.0,
    "turn_max_delta": 14.0,
    "near_y_thr": 0.72,
    "lost_frames_after_near": 3,
    "lost_track_frames": 8,
    "push_speed": 25.0,
    "push_rotations": 2.0,
    "lift_down_speed": 20.0,
    "lift_up_speed": 30.0,
    "lift_up_rotations": 3.0,
    "lift_down_max_rotations": 20.0,
    "lift_down_sign": -1,
    "lift_stop_port": "INPUT_1",
    "lift_touch_active_state": "pressed",
    "lift_stop_debounce_ms": 20,
    "lift_stop_max_sec": 3.0,
    "ev3_host": "10.42.0.3",
    "ev3_port": 18812,
    "rpc_timeout": 10.0,
    "speak_text": "butter",
    "dry_run": False,
    "telemetry_json": "/tmp/hailo_robot_telemetry.json",
    "telemetry_timeout_sec": 1.5,
}

# Auto-applied at each server start (can be changed later in the web UI).
BOOT_AUTO_CONFIG: Dict[str, Any] = {
    "hef": "/home/gast/model.hef",
    "source": "auto",
    "robot_source_mode": "shared",
    "robot_source_manual": "auto",
    "box_color": "#ff0000",
    "label_color": "#ff0000",
    "box_thickness": 2,
    "show_labels": True,
    "rotate_180": True,
    "left_port": "A",
    "right_port": "D",
    "lift_port": "C",
    "search_turn_speed": 8.0,
    "search_min_turn_sec": 0.22,
    "search_max_turn_sec": 0.45,
    "search_pause_min_sec": 0.35,
    "search_pause_max_sec": 0.80,
    "lift_stop_port": "INPUT_1",
    "lift_touch_active_state": "pressed",
    "ev3_host": "10.42.0.3",
    "ev3_port": 18812,
    "telemetry_json": "/tmp/hailo_robot_telemetry.json",
}


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"1", "true", "yes", "on"}:
            return True
        if s in {"0", "false", "no", "off"}:
            return False
    return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_str(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value).strip()


def normalize_config(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = deepcopy(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        for key, default in DEFAULT_CONFIG.items():
            if key not in raw:
                continue
            value = raw[key]
            if isinstance(default, bool):
                cfg[key] = _to_bool(value, default)
            elif isinstance(default, int):
                cfg[key] = _to_int(value, default)
            elif isinstance(default, float):
                cfg[key] = _to_float(value, default)
            else:
                cfg[key] = _to_str(value, default)

    cfg["width"] = max(160, min(3840, int(cfg["width"])))
    cfg["height"] = max(120, min(2160, int(cfg["height"])))
    cfg["jpeg_quality"] = max(20, min(100, int(cfg["jpeg_quality"])))
    cfg["score_thr"] = max(0.0, min(1.0, float(cfg["score_thr"])))
    cfg["iou_thr"] = max(0.0, min(1.0, float(cfg["iou_thr"])))
    cfg["max_det"] = max(1, min(500, int(cfg["max_det"])))
    cfg["box_thickness"] = max(1, min(8, int(cfg["box_thickness"])))
    cfg["butter_thr"] = max(0.0, min(1.0, float(cfg["butter_thr"])))
    cfg["track_thr"] = max(0.0, min(1.0, float(cfg["track_thr"])))
    cfg["confirm_frames"] = max(1, int(cfg["confirm_frames"]))
    cfg["lift_stop_debounce_ms"] = max(0, int(cfg["lift_stop_debounce_ms"]))
    cfg["lift_stop_max_sec"] = max(0.1, float(cfg["lift_stop_max_sec"]))
    cfg["ev3_port"] = max(1, min(65535, int(cfg["ev3_port"])))
    cfg["rpc_timeout"] = max(1.0, float(cfg["rpc_timeout"]))
    cfg["telemetry_timeout_sec"] = max(0.2, min(10.0, float(cfg["telemetry_timeout_sec"])))
    cfg["lift_down_sign"] = -1 if int(cfg["lift_down_sign"]) < 0 else 1
    cfg["search_turn_speed"] = max(1.0, min(40.0, float(cfg["search_turn_speed"])))
    cfg["search_min_turn_sec"] = max(0.05, float(cfg["search_min_turn_sec"]))
    cfg["search_max_turn_sec"] = max(cfg["search_min_turn_sec"], float(cfg["search_max_turn_sec"]))
    cfg["search_pause_min_sec"] = max(0.05, float(cfg["search_pause_min_sec"]))
    cfg["search_pause_max_sec"] = max(cfg["search_pause_min_sec"], float(cfg["search_pause_max_sec"]))

    if cfg["lift_touch_active_state"] not in {"pressed", "released"}:
        cfg["lift_touch_active_state"] = "pressed"
    if cfg["robot_source_mode"] not in {"shared", "manual"}:
        cfg["robot_source_mode"] = "shared"

    for name in ("left_port", "right_port", "lift_port"):
        val = _to_str(cfg[name], "").upper()
        if len(val) == 1 and val in "ABCD":
            cfg[name] = val
        elif val.startswith("OUTPUT_") and len(val) == 8 and val[-1] in "ABCD":
            cfg[name] = val
        else:
            cfg[name] = DEFAULT_CONFIG[name]

    stop_port = _to_str(cfg["lift_stop_port"], "").upper()
    if len(stop_port) == 1 and stop_port in "1234":
        stop_port = f"INPUT_{stop_port}"
    if stop_port not in {"INPUT_1", "INPUT_2", "INPUT_3", "INPUT_4"}:
        stop_port = "INPUT_1"
    cfg["lift_stop_port"] = stop_port

    for ckey in ("box_color", "label_color"):
        cval = _to_str(cfg[ckey], "#ff0000")
        if not _is_hex_color(cval):
            cval = "#ff0000"
        cfg[ckey] = cval.lower()

    return cfg


def _is_hex_color(value: str) -> bool:
    if len(value) != 7 or not value.startswith("#"):
        return False
    for ch in value[1:]:
        if ch not in "0123456789abcdefABCDEF":
            return False
    return True


def _hex_to_bgr(value: str, fallback: Tuple[int, int, int]) -> Tuple[int, int, int]:
    if not _is_hex_color(value):
        return fallback
    try:
        r = int(value[1:3], 16)
        g = int(value[3:5], 16)
        b = int(value[5:7], 16)
        return (b, g, r)
    except Exception:
        return fallback


def apply_boot_auto_config(cfg_store: "ConfigStore") -> Dict[str, Any]:
    base = cfg_store.get()
    merged = dict(base)
    merged.update(BOOT_AUTO_CONFIG)

    # Optional env overrides for quick deployment changes.
    env_ev3_host = os.environ.get("EV3_HOST")
    env_ev3_port = os.environ.get("EV3_PORT")
    env_dry_run = os.environ.get("ROBOT_DRY_RUN")
    env_hef = os.environ.get("HEF_PATH")
    env_source = os.environ.get("SOURCE")

    if env_ev3_host:
        merged["ev3_host"] = env_ev3_host
    if env_ev3_port:
        merged["ev3_port"] = env_ev3_port
    if env_hef:
        merged["hef"] = env_hef
    if env_source:
        merged["source"] = env_source

    # Default: real robot mode. Set ROBOT_DRY_RUN=1 to force dry-run at boot.
    merged["dry_run"] = _to_bool(env_dry_run, False)

    cfg = cfg_store.update(merged)
    cfg_store.save()
    return cfg


class ConfigStore:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.cfg = deepcopy(DEFAULT_CONFIG)

    def load(self) -> None:
        raw = None
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        with self.lock:
            self.cfg = normalize_config(raw)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp_path = f"{self.path}.tmp"
        with self.lock:
            payload = deepcopy(self.cfg)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=True)
            f.write("\n")
        os.replace(tmp_path, self.path)

    def get(self) -> Dict[str, Any]:
        with self.lock:
            return deepcopy(self.cfg)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            merged = deepcopy(self.cfg)
            if isinstance(patch, dict):
                merged.update(patch)
            self.cfg = normalize_config(merged)
            return deepcopy(self.cfg)


class SharedFrames:
    def __init__(self):
        self.lock = threading.Lock()
        self.overlay_jpg: Optional[bytes] = None
        self.raw_jpg: Optional[bytes] = None
        self.meta: Dict[str, Any] = {
            "det_count": 0,
            "fps": 0.0,
            "source": "",
            "last_frame_ts": 0.0,
            "error": "",
        }

    def set_frames(self, raw_jpg: bytes, overlay_jpg: bytes, meta: Dict[str, Any]) -> None:
        with self.lock:
            self.raw_jpg = raw_jpg
            self.overlay_jpg = overlay_jpg
            self.meta = dict(meta)

    def set_error_frame(self, jpg: bytes, message: str) -> None:
        with self.lock:
            self.raw_jpg = jpg
            self.overlay_jpg = jpg
            self.meta = {
                "det_count": 0,
                "fps": 0.0,
                "source": "",
                "last_frame_ts": time.time(),
                "error": message,
            }

    def get_frame(self, raw: bool = False) -> Optional[bytes]:
        with self.lock:
            return self.raw_jpg if raw else self.overlay_jpg

    def get_meta(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.meta)


def make_status_image(width: int, height: int, lines: List[str]) -> np.ndarray:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (20, 24, 28)
    y = 46
    for i, line in enumerate(lines):
        scale = 0.82 if i == 0 else 0.6
        color = (80, 220, 255) if i == 0 else (220, 220, 220)
        cv2.putText(img, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)
        y += 34
    return img


class CaptureWorker:
    def __init__(self, shared: SharedFrames, get_config, add_log):
        self.shared = shared
        self.get_config = get_config
        self.add_log = add_log
        self.stop_event = threading.Event()
        self.reload_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.telemetry_cache: Dict[str, Any] = {
            "ts": 0.0,
            "state": "IDLE",
            "frame_w": 0,
            "frame_h": 0,
            "rotate_180": False,
            "det_count": 0,
            "detections": [],
        }

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.reload_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def request_reload(self) -> None:
        self.reload_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        self.reload_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)

    def _draw_overlay(
        self,
        base_frame: np.ndarray,
        telemetry: Dict[str, Any],
        fps_avg: float,
        cfg: Dict[str, Any],
    ) -> Tuple[np.ndarray, int, str, bool]:
        out = base_frame.copy()
        box_color = _hex_to_bgr(cfg.get("box_color", "#ff0000"), (0, 0, 255))
        label_color = _hex_to_bgr(cfg.get("label_color", "#ff0000"), box_color)
        thickness = max(1, int(cfg.get("box_thickness", 2)))
        show_labels = bool(cfg.get("show_labels", True))
        telemetry_timeout = float(cfg.get("telemetry_timeout_sec", 1.5))

        tele_ts = float(telemetry.get("ts", 0.0) or 0.0)
        is_fresh = (time.time() - tele_ts) <= telemetry_timeout
        tele_state = str(telemetry.get("state", "IDLE"))
        detections = telemetry.get("detections", []) if is_fresh else []
        det_count = int(telemetry.get("det_count", len(detections))) if is_fresh else 0

        frame_h, frame_w = out.shape[:2]
        tele_w = float(telemetry.get("frame_w", frame_w) or frame_w)
        tele_h = float(telemetry.get("frame_h", frame_h) or frame_h)
        tele_rotated = bool(telemetry.get("rotate_180", False))

        for det in detections:
            if not isinstance(det, (list, tuple)) or len(det) < 5:
                continue
            x1, y1, x2, y2, score = [float(det[i]) for i in range(5)]

            # Robot telemetry coordinates are in the robot inference frame. If robot
            # used rotate_180, map those boxes back to the non-rotated preview frame.
            if tele_rotated:
                x1, y1, x2, y2 = (tele_w - 1.0 - x2, tele_h - 1.0 - y2, tele_w - 1.0 - x1, tele_h - 1.0 - y1)

            x1 = max(0, min(int(x1), frame_w - 1))
            y1 = max(0, min(int(y1), frame_h - 1))
            x2 = max(0, min(int(x2), frame_w - 1))
            y2 = max(0, min(int(y2), frame_h - 1))
            if x2 <= x1 or y2 <= y1:
                continue
            p1 = (int(x1), int(y1))
            p2 = (int(x2), int(y2))
            # High-contrast box: black outer stroke + colored inner stroke.
            cv2.rectangle(out, p1, p2, (0, 0, 0), thickness + 2)
            cv2.rectangle(out, p1, p2, box_color, thickness)
            if show_labels:
                label = f"butter {float(score) * 100.0:.0f}%"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
                tx = p1[0]
                ty = max(th + 8, p1[1] - 8)
                bx1 = max(0, tx - 3)
                by1 = max(0, ty - th - 6)
                bx2 = min(frame_w - 1, tx + tw + 5)
                by2 = min(frame_h - 1, ty + 4)
                cv2.rectangle(out, (bx1, by1), (bx2, by2), (0, 0, 0), thickness=-1)
                cv2.putText(
                    out,
                    label,
                    (tx, ty),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (255, 255, 255),
                    2,
                )
                cv2.putText(
                    out,
                    label,
                    (tx, ty),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    label_color,
                    1,
                )

        hud_state = tele_state if is_fresh else "no-telemetry"
        hud = f"FPS {fps_avg:.1f} | det {det_count} | {hud_state}"
        cv2.putText(out, hud, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
        return out, det_count, tele_state, is_fresh

    def _load_telemetry(self, path: str) -> Dict[str, Any]:
        if not path:
            return self.telemetry_cache
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.telemetry_cache = data
        except Exception:
            pass
        return self.telemetry_cache

    def _publish_status_frame(self, message: str) -> None:
        status = make_status_image(
            960,
            540,
            ["Capture Status", message, "Pruefe Kamera oder Source-Settings"],
        )
        ok, jpg = cv2.imencode(".jpg", status, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ok:
            self.shared.set_error_frame(jpg.tobytes(), message)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            cfg = self.get_config()
            cap = None
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), int(cfg.get("jpeg_quality", 80))]

            try:
                cap, opened_source = open_capture(cfg["source"], int(cfg["width"]), int(cfg["height"]))
                self.add_log("CAP", f"Kameraquelle geoeffnet: {opened_source}")
            except Exception as exc:
                msg = f"Init-Fehler: {exc}"
                self.add_log("CAP", msg)
                self._publish_status_frame(msg)
                for _ in range(10):
                    if self.stop_event.is_set() or self.reload_event.is_set():
                        break
                    time.sleep(0.2)
                self.reload_event.clear()
                continue

            fps_hist: List[float] = []
            fail_reads = 0

            try:
                while not self.stop_event.is_set() and not self.reload_event.is_set():
                    t0 = time.time()
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        fail_reads += 1
                        msg = "Kamera liefert keine Frames"
                        if fail_reads % 40 == 1:
                            self.add_log("CAP", msg)
                        status = make_status_image(
                            960,
                            540,
                            [msg, f"source={cfg['source']}", "Kameraanschluss/Quelle pruefen"],
                        )
                        ok_j, jpg = cv2.imencode(".jpg", status, encode_param)
                        if ok_j:
                            self.shared.set_error_frame(jpg.tobytes(), msg)
                        time.sleep(0.05)
                        continue

                    fail_reads = 0

                    dt = max(1e-6, time.time() - t0)
                    fps = 1.0 / dt
                    fps_hist.append(fps)
                    if len(fps_hist) > 30:
                        fps_hist.pop(0)
                    fps_avg = sum(fps_hist) / len(fps_hist)

                    cfg_now = self.get_config()
                    telemetry = self._load_telemetry(str(cfg_now.get("telemetry_json", "")))
                    overlay, det_count, tele_state, tele_fresh = self._draw_overlay(frame, telemetry, fps_avg, cfg_now)

                    ok_raw, jpg_raw = cv2.imencode(".jpg", frame, encode_param)
                    ok_ovr, jpg_ovr = cv2.imencode(".jpg", overlay, encode_param)
                    if ok_raw and ok_ovr:
                        self.shared.set_frames(
                            jpg_raw.tobytes(),
                            jpg_ovr.tobytes(),
                            {
                                "det_count": det_count,
                                "fps": round(fps_avg, 2),
                                "source": cfg["source"],
                                "last_frame_ts": time.time(),
                                "error": "",
                                "telemetry_state": tele_state,
                                "telemetry_fresh": tele_fresh,
                            },
                        )

            except Exception as exc:
                msg = f"Capture-Loop Fehler: {exc}"
                self.add_log("CAP", msg)
                self._publish_status_frame(msg)
                time.sleep(0.3)

            finally:
                try:
                    if cap is not None:
                        cap.release()
                except Exception:
                    pass
                self.reload_event.clear()


def _normalize_motor_port(value: str, fallback: str) -> str:
    v = str(value).strip().upper()
    if len(v) == 1 and v in "ABCD":
        return v
    if v.startswith("OUTPUT_") and len(v) == 8 and v[-1] in "ABCD":
        return v[-1]
    return fallback


def build_robot_command(cfg: Dict[str, Any], web_port: int) -> List[str]:
    if cfg.get("robot_source_mode") == "manual":
        source = str(cfg.get("robot_source_manual", "auto") or "auto")
    else:
        source = f"http://127.0.0.1:{web_port}/raw.mjpg"

    left = _normalize_motor_port(cfg.get("left_port", "A"), "A")
    right = _normalize_motor_port(cfg.get("right_port", "D"), "D")
    lift = _normalize_motor_port(cfg.get("lift_port", "C"), "C")

    cmd = [
        sys.executable,
        "/home/gast/hailo_butter_ev3_alert.py",
        "--hef",
        str(cfg["hef"]),
        "--source",
        source,
        "--width",
        str(cfg["width"]),
        "--height",
        str(cfg["height"]),
        "--score-thr",
        str(cfg["score_thr"]),
        "--iou-thr",
        str(cfg["iou_thr"]),
        "--max-det",
        str(cfg["max_det"]),
        "--butter-thr",
        str(cfg["butter_thr"]),
        "--track-thr",
        str(cfg["track_thr"]),
        "--confirm-frames",
        str(cfg["confirm_frames"]),
        "--left-port",
        left,
        "--right-port",
        right,
        "--lift-port",
        lift,
        "--search-turn-speed",
        str(cfg["search_turn_speed"]),
        "--search-min-turn-sec",
        str(cfg["search_min_turn_sec"]),
        "--search-max-turn-sec",
        str(cfg["search_max_turn_sec"]),
        "--search-pause-min-sec",
        str(cfg["search_pause_min_sec"]),
        "--search-pause-max-sec",
        str(cfg["search_pause_max_sec"]),
        "--search-forward-speed",
        str(cfg["search_forward_speed"]),
        "--search-min-forward-sec",
        str(cfg["search_min_forward_sec"]),
        "--search-max-forward-sec",
        str(cfg["search_max_forward_sec"]),
        "--approach-speed",
        str(cfg["approach_speed"]),
        "--turn-kp",
        str(cfg["turn_kp"]),
        "--turn-max-delta",
        str(cfg["turn_max_delta"]),
        "--near-y-thr",
        str(cfg["near_y_thr"]),
        "--lost-frames-after-near",
        str(cfg["lost_frames_after_near"]),
        "--lost-track-frames",
        str(cfg["lost_track_frames"]),
        "--push-speed",
        str(cfg["push_speed"]),
        "--push-rotations",
        str(cfg["push_rotations"]),
        "--lift-down-speed",
        str(cfg["lift_down_speed"]),
        "--lift-up-speed",
        str(cfg["lift_up_speed"]),
        "--lift-up-rotations",
        str(cfg["lift_up_rotations"]),
        "--lift-down-max-rotations",
        str(cfg["lift_down_max_rotations"]),
        "--lift-down-sign",
        str(cfg["lift_down_sign"]),
        "--lift-stop-sensor",
        "touch",
        "--lift-stop-port",
        str(cfg["lift_stop_port"]),
        "--lift-touch-active-state",
        str(cfg["lift_touch_active_state"]),
        "--lift-stop-debounce-ms",
        str(cfg["lift_stop_debounce_ms"]),
        "--lift-stop-max-sec",
        str(cfg["lift_stop_max_sec"]),
        "--lift-stop-required",
        "--no-lift-software-fallback",
        "--ev3-host",
        str(cfg["ev3_host"]),
        "--ev3-port",
        str(cfg["ev3_port"]),
        "--rpc-timeout",
        str(cfg["rpc_timeout"]),
        "--speak-text",
        str(cfg["speak_text"]),
        "--telemetry-json",
        str(cfg["telemetry_json"]),
        "--telemetry-interval-ms",
        "100",
    ]
    if bool(cfg.get("dry_run", False)):
        cmd.append("--dry-run")

    if cfg.get("rotate_180", True):
        cmd.append("--rotate-180")
    else:
        cmd.append("--no-rotate-180")

    return cmd


class RobotProcessManager:
    def __init__(self, get_config, web_port: int, add_log):
        self.get_config = get_config
        self.web_port = web_port
        self.add_log = add_log

        self.lock = threading.RLock()
        self.proc: Optional[subprocess.Popen] = None
        self.state = "idle"
        self.last_error = ""
        self.started_at = 0.0
        self.stop_requested = False

    def _running_locked(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> Dict[str, Any]:
        with self.lock:
            running = self._running_locked()
            return {
                "state": self.state,
                "running": running,
                "pid": self.proc.pid if running and self.proc is not None else None,
                "uptime_sec": round(max(0.0, time.time() - self.started_at), 1) if running else 0.0,
                "last_error": self.last_error,
                "stop_requested": self.stop_requested,
            }

    def _reader_worker(self, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                text = line.rstrip("\r\n")
                if text:
                    self.add_log("ROBOT", text)
        except Exception as exc:
            self.add_log("ROBOT", f"Log-Leser Fehler: {exc}")

    def _waiter_worker(self, proc: subprocess.Popen) -> None:
        rc = proc.wait()
        with self.lock:
            if self.proc is None or self.proc.pid != proc.pid:
                return
            self.proc = None
            if self.stop_requested:
                self.state = "idle"
                self.last_error = ""
                self.stop_requested = False
                self.add_log("ROBOT", f"Prozess sauber gestoppt (rc={rc})")
                return

            if rc == 0:
                self.state = "idle"
                self.last_error = ""
                self.add_log("ROBOT", "Prozess beendet (rc=0)")
            else:
                self.state = "error"
                self.last_error = f"Robot-Prozess beendet mit rc={rc}"
                self.add_log("ROBOT", self.last_error)

    def start(self) -> Tuple[bool, str]:
        with self.lock:
            if self._running_locked():
                return False, "Robot läuft bereits"

            cfg = self.get_config()
            cmd = build_robot_command(cfg, self.web_port)
            self.add_log("ROBOT", f"Starte: {' '.join(cmd)}")

            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd="/home/gast",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                    env=os.environ.copy(),
                )
            except Exception as exc:
                self.state = "error"
                self.last_error = f"Start fehlgeschlagen: {exc}"
                self.add_log("ROBOT", self.last_error)
                return False, self.last_error

            self.proc = proc
            self.state = "running"
            self.stop_requested = False
            self.last_error = ""
            self.started_at = time.time()

            threading.Thread(target=self._reader_worker, args=(proc,), daemon=True).start()
            threading.Thread(target=self._waiter_worker, args=(proc,), daemon=True).start()

        # If process crashes immediately, surface this as start failure to UI.
        time.sleep(0.7)
        with self.lock:
            still_same_proc = self.proc is not None and self.proc.pid == proc.pid
            alive = proc.poll() is None
            if still_same_proc and alive:
                return True, f"Robot gestartet (pid={proc.pid})"
            if self.last_error:
                return False, self.last_error
            rc = proc.returncode if proc.returncode is not None else -1
            return False, f"Robot-Start fehlgeschlagen (rc={rc})"

    def stop(self, timeout_sec: float = 6.0) -> Tuple[bool, str]:
        with self.lock:
            if not self._running_locked():
                self.state = "idle"
                self.stop_requested = False
                self.last_error = ""
                return True, "Robot ist bereits gestoppt"
            assert self.proc is not None
            proc = self.proc
            self.stop_requested = True
            self.state = "stopping"

        self.add_log("ROBOT", f"Stop angefordert (pid={proc.pid})")

        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except Exception as exc:
            self.add_log("ROBOT", f"SIGTERM senden fehlgeschlagen: {exc}")

        try:
            proc.wait(timeout=timeout_sec)
            return True, "Robot gestoppt"
        except subprocess.TimeoutExpired:
            self.add_log("ROBOT", "Stop-Timeout, sende SIGKILL")
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except Exception as exc:
                self.add_log("ROBOT", f"SIGKILL fehlgeschlagen: {exc}")
            return True, "Robot hart gestoppt"


class AppController:
    def __init__(self, cfg_store: ConfigStore, web_port: int):
        self.cfg_store = cfg_store
        self.logs = deque(maxlen=3000)
        self.log_lock = threading.Lock()

        self.shared_frames = SharedFrames()
        self.capture = CaptureWorker(self.shared_frames, self.cfg_store.get, self.add_log)
        self.robot = RobotProcessManager(self.cfg_store.get, web_port, self.add_log)

    def add_log(self, source: str, msg: str) -> None:
        line = f"[{ts_now()}] [{source}] {msg}"
        with self.log_lock:
            self.logs.append(line)
        print(line, flush=True)

    def get_logs(self, tail: int) -> List[str]:
        n = max(1, min(2000, int(tail)))
        with self.log_lock:
            return list(self.logs)[-n:]

    def get_config(self) -> Dict[str, Any]:
        return self.cfg_store.get()

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        cfg = self.cfg_store.update(patch)
        self.cfg_store.save()
        self.capture.request_reload()
        self.add_log("CFG", "Konfiguration gespeichert und Stream-Reload angefordert")
        return cfg

    def _auto_start_robot(self) -> None:
        # Start once at boot; keeps UI minimal because no manual start button is needed.
        for attempt in (1, 2, 3):
            status = self.robot.status()
            if bool(status.get("running")):
                return
            ok, msg = self.robot.start()
            self.add_log("AUTO", f"Robot-Autostart attempt={attempt} ok={ok} msg={msg}")
            if ok:
                return
            time.sleep(1.5)

    def start(self) -> None:
        self.capture.start()
        threading.Thread(target=self._auto_start_robot, daemon=True).start()

    def stop(self) -> None:
        try:
            self.robot.stop(timeout_sec=2.0)
        except Exception:
            pass
        self.capture.stop()

    def status(self) -> Dict[str, Any]:
        return {
            "robot": self.robot.status(),
            "capture": self.shared_frames.get_meta(),
            "time": ts_now(),
        }


class ControlHandler(BaseHTTPRequestHandler):
    server_version = "HailoRobotWeb/1.0"

    @property
    def app(self) -> AppController:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        return

    def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str = "/") -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        obj = json.loads(raw.decode("utf-8"))
        if isinstance(obj, dict):
            return obj
        return {}

    def _serve_index(self) -> None:
        html = INDEX_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _stream_mjpeg(self, raw: bool) -> None:
        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        try:
            while True:
                frame = self.app.shared_frames.get_frame(raw=raw)
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
            return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/robot/start":
            ok, msg = self.app.robot.start()
            self.app.add_log("WEB", f"Fallback /robot/start -> ok={ok} msg={msg}")
            self._redirect("/")
            return

        if path == "/robot/stop":
            ok, msg = self.app.robot.stop()
            self.app.add_log("WEB", f"Fallback /robot/stop -> ok={ok} msg={msg}")
            self._redirect("/")
            return

        if path in {"/", "/index.html"}:
            self._serve_index()
            return

        if path in {"/stream", "/stream.mjpg"}:
            self._stream_mjpeg(raw=False)
            return

        if path in {"/raw", "/raw.mjpg"}:
            self._stream_mjpeg(raw=True)
            return

        if path == "/api/config":
            self._send_json(200, {"ok": True, "config": self.app.get_config()})
            return

        if path == "/api/status":
            self._send_json(200, {"ok": True, "status": self.app.status()})
            return

        if path == "/api/logs":
            qs = parse_qs(parsed.query or "")
            tail = int(qs.get("tail", [200])[0])
            self._send_json(200, {"ok": True, "lines": self.app.get_logs(tail)})
            return

        if path == "/healthz":
            self._send_json(200, {"ok": True})
            return

        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            try:
                patch = self._read_json_body()
                cfg = self.app.update_config(patch)
                self._send_json(200, {"ok": True, "config": cfg})
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/robot/start":
            ok, msg = self.app.robot.start()
            self._send_json(200 if ok else 409, {"ok": ok, "message": msg, "status": self.app.status()})
            return

        if path == "/api/robot/stop":
            ok, msg = self.app.robot.stop()
            self._send_json(200 if ok else 409, {"ok": ok, "message": msg, "status": self.app.status()})
            return

        self._send_json(404, {"ok": False, "error": "not found"})


INDEX_HTML = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Live Feed</title>
  <style>
    html, body { margin: 0; background: #06090c; }
    .wrap {
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: radial-gradient(1200px 700px at 20% -10%, #1b2d3a 0%, #06090c 45%);
    }
    img {
      width: min(98vw, 1600px);
      max-height: 96vh;
      object-fit: contain;
      border: 2px solid #314a5d;
      border-radius: 10px;
      background: #000;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <img src="/stream.mjpg" alt="Live detection feed" />
  </div>
</body>
</html>
"""


class AppServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class, app: AppController):
        super().__init__(server_address, handler_class)
        self.app = app


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Unified Hailo Web Control (Stream + Robot Start/Stop + Config)")
    p.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"), help="Bind-Adresse")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")), help="HTTP-Port")
    p.add_argument(
        "--config",
        default="/home/gast/.config/hailo_robot_web/config.json",
        help="Pfad zur JSON-Konfiguration",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    cfg_store = ConfigStore(args.config)
    try:
        cfg_store.load()
    except Exception as exc:
        print(f"[{ts_now()}] [CFG] Laden fehlgeschlagen, nutze Defaults: {exc}", flush=True)
    try:
        boot_cfg = apply_boot_auto_config(cfg_store)
        print(
            (
                f"[{ts_now()}] [CFG] Auto-Config gesetzt: "
                f"ev3={boot_cfg.get('ev3_host')}:{boot_cfg.get('ev3_port')} "
                f"source={boot_cfg.get('source')} dry_run={boot_cfg.get('dry_run')}"
            ),
            flush=True,
        )
    except Exception as exc:
        print(f"[{ts_now()}] [CFG] Auto-Config konnte nicht gesetzt werden: {exc}", flush=True)

    app = AppController(cfg_store, web_port=int(args.port))
    app.start()
    app.add_log("SRV", f"Server start auf http://{args.host}:{args.port}")
    app.add_log("SRV", f"Config-Datei: {args.config}")

    server = AppServer((args.host, int(args.port)), ControlHandler, app)

    def _shutdown(*_):
        app.add_log("SRV", "Shutdown angefordert")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        server.serve_forever(poll_interval=0.2)
        return 0
    finally:
        app.stop()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())

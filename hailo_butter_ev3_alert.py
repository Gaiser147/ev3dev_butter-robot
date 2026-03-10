#!/usr/bin/env python3
"""Autonomous butter hunter: search, approach, pick, lift, and announce on EV3."""

import argparse
import json
import os
import pwd
import random
import signal
import sys
import time
from typing import Dict, List, Optional, Tuple


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
    # Mirror /home/gast/start_hailo_webserver.sh behavior so camera runtime is identical.
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
import rpyc  # noqa: E402

from hailo_web_detect_server import HailoDetector, open_capture  # noqa: E402


def log(level: str, msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def _write_json_atomic(path: str, payload: Dict) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True)
        f.write("\n")
    os.replace(tmp, path)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _source_candidates(source: str) -> List[str]:
    if source != "auto":
        return [source]
    return ["auto"] + [f"v4l2:{i}" for i in range(8)] + [f"/dev/video{i}" for i in range(0, 12)]


def _open_one_source(source: str, width: int, height: int):
    if source.startswith("/dev/video"):
        cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise RuntimeError(f"Kamera-Device konnte nicht geöffnet werden: {source}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, 30)
        return cap, source

    if source.startswith("http://") or source.startswith("https://") or source.startswith("rtsp://"):
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise RuntimeError(f"URL-Quelle konnte nicht geöffnet werden: {source}")
        return cap, source

    return open_capture(source, width, height)


def _open_capture_robust(
    source: str,
    width: int,
    height: int,
    start_idx: int = 0,
):
    candidates = _source_candidates(source)
    errors = []
    for off in range(len(candidates)):
        idx = (start_idx + off) % len(candidates)
        cand = candidates[idx]
        try:
            cap, opened_source = _open_one_source(cand, width, height)
            return cap, opened_source, idx
        except Exception as exc:
            errors.append(f"{cand}: {exc}")

    short_err = " | ".join(errors[-3:]) if errors else "unbekannt"
    raise RuntimeError(f"Keine Kameraquelle öffnet erfolgreich ({short_err})")


class EV3Robot:
    def __init__(
        self,
        host: str,
        port: int,
        timeout: float,
        left_port: str,
        right_port: str,
        lift_port: str,
        lift_stop_sensor: str,
        lift_stop_port: str,
        lift_stop_required: bool,
        lift_touch_active_state: str,
        lift_stop_debounce_ms: int,
        lift_software_fallback: bool,
        enabled: bool,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.left_port_name = left_port
        self.right_port_name = right_port
        self.lift_port_name = lift_port
        self.lift_stop_sensor = lift_stop_sensor
        self.lift_stop_port_name = lift_stop_port
        self.lift_stop_required = lift_stop_required
        self.lift_touch_active_state = lift_touch_active_state
        self.lift_stop_debounce_ms = lift_stop_debounce_ms
        self.lift_software_fallback = lift_software_fallback
        self.enabled = enabled

        self.conn = None
        self.motor_module = None
        self.sensor_module = None
        self.sensor_lego_module = None
        self.sound_module = None
        self.SpeedPercent = None
        self.tank = None
        self.lift = None
        self.lift_touch = None

    @staticmethod
    def _normalize_port_name(name: str) -> str:
        clean = name.strip().upper()
        if len(clean) == 1 and clean in "ABCD":
            return f"OUTPUT_{clean}"
        return clean

    def _resolve_output_port(self, name: str):
        key = self._normalize_port_name(name)
        if not hasattr(self.motor_module, key):
            raise RuntimeError(
                f"Ungültiger Motor-Port '{name}'. Verwende z. B. A/B/C/D oder OUTPUT_A/OUTPUT_B/..."
            )
        return getattr(self.motor_module, key)

    def _resolve_input_port(self, name: str):
        key = name.strip().upper()
        if len(key) == 1 and key in "1234":
            key = f"INPUT_{key}"
        if not hasattr(self.sensor_module, key):
            raise RuntimeError(
                f"Ungültiger Sensor-Port '{name}'. Verwende z. B. 1/2/3/4 oder INPUT_1/INPUT_2/..."
            )
        return getattr(self.sensor_module, key)

    def _speed(self, value: float):
        if self.SpeedPercent is None:
            return value
        return self.SpeedPercent(float(value))

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None
        self.motor_module = None
        self.sensor_module = None
        self.sensor_lego_module = None
        self.sound_module = None
        self.SpeedPercent = None
        self.tank = None
        self.lift = None
        self.lift_touch = None

    def _init_lift_touch_sensor(self) -> None:
        self.lift_touch = None
        if self.lift_stop_sensor == "none":
            return

        if self.lift_stop_sensor != "touch":
            raise RuntimeError(f"Unbekannter Lift-Stop-Sensor: {self.lift_stop_sensor}")

        self.sensor_module = self.conn.modules["ev3dev2.sensor"]
        self.sensor_lego_module = self.conn.modules["ev3dev2.sensor.lego"]
        input_port = self._resolve_input_port(self.lift_stop_port_name)
        TouchSensor = self.sensor_lego_module.TouchSensor

        sensor_obj = None
        errors = []
        for ctor in (
            lambda: TouchSensor(input_port),
            lambda: TouchSensor(address=input_port),
            lambda: TouchSensor(),
        ):
            try:
                sensor_obj = ctor()
                _ = bool(getattr(sensor_obj, "is_pressed"))
                break
            except Exception as exc:
                errors.append(str(exc))
                sensor_obj = None

        if sensor_obj is None:
            joined = " | ".join(errors[-2:]) if errors else "unbekannt"
            raise RuntimeError(f"TouchSensor auf {self.lift_stop_port_name} nicht verfügbar ({joined})")

        self.lift_touch = sensor_obj

    def _touch_pressed(self) -> Optional[bool]:
        if self.lift_touch is None:
            return None
        pressed = bool(getattr(self.lift_touch, "is_pressed"))
        if self.lift_touch_active_state == "pressed":
            return pressed
        return not pressed

    def _ensure_connected(self) -> None:
        if not self.enabled:
            return
        if self.conn is not None:
            return

        self.conn = rpyc.classic.connect(self.host, port=self.port, keepalive=True)
        self.conn._config["sync_request_timeout"] = self.timeout
        _ = self.conn.modules["sys"].version

        self.motor_module = self.conn.modules["ev3dev2.motor"]
        self.sound_module = self.conn.modules["ev3dev2.sound"]
        self.SpeedPercent = getattr(self.motor_module, "SpeedPercent", None)

        left_port = self._resolve_output_port(self.left_port_name)
        right_port = self._resolve_output_port(self.right_port_name)
        lift_port = self._resolve_output_port(self.lift_port_name)

        self.tank = self.motor_module.MoveTank(left_port, right_port)
        self.lift = self.motor_module.MediumMotor(lift_port)
        try:
            self.lift.stop_action = "brake"
        except Exception:
            pass

        touch_init_error = None
        try:
            self._init_lift_touch_sensor()
        except Exception as exc:
            touch_init_error = exc
            self.lift_touch = None

        if touch_init_error is not None:
            if self.lift_stop_required:
                raise RuntimeError(str(touch_init_error))
            log("WARN", f"Lift-Stop-Sensor nicht verfügbar, nutze Fallback: {touch_init_error}")

        log(
            "INFO",
            (
                "EV3 verbunden | "
                f"tank=({self._normalize_port_name(self.left_port_name)},{self._normalize_port_name(self.right_port_name)}) "
                f"lift={self._normalize_port_name(self.lift_port_name)} "
                f"stop_sensor={self.lift_stop_sensor}@{self.lift_stop_port_name} "
                f"required={self.lift_stop_required} fallback={self.lift_software_fallback}"
            ),
        )

    def _call(self, fn):
        if not self.enabled:
            return None
        for attempt in (1, 2):
            try:
                self._ensure_connected()
                return fn()
            except Exception as exc:
                log("WARN", f"EV3 call fehlgeschlagen (attempt {attempt}/2): {exc}")
                self.close()
                time.sleep(0.2)
        raise RuntimeError("EV3 call wiederholt fehlgeschlagen")

    def tank_off(self, brake: bool = True) -> None:
        if not self.enabled:
            return
        self._call(lambda: self.tank.off(brake=brake))

    def tank_on(self, left_speed: float, right_speed: float, brake: bool = False, block: bool = False) -> None:
        if not self.enabled:
            log("DEBUG", f"[dry-run] tank_on l={left_speed:.1f} r={right_speed:.1f}")
            return
        l = self._speed(_clamp(left_speed, -100.0, 100.0))
        r = self._speed(_clamp(right_speed, -100.0, 100.0))
        # MoveTank.on() on older ev3dev2 only accepts (left_speed, right_speed).
        self._call(lambda: self.tank.on(l, r))

    def tank_on_for_seconds(
        self,
        left_speed: float,
        right_speed: float,
        seconds: float,
        brake: bool = True,
        block: bool = True,
    ) -> None:
        if not self.enabled:
            log(
                "DEBUG",
                f"[dry-run] tank_on_for_seconds l={left_speed:.1f} r={right_speed:.1f} s={seconds:.2f}",
            )
            time.sleep(max(0.0, seconds))
            return
        l = self._speed(_clamp(left_speed, -100.0, 100.0))
        r = self._speed(_clamp(right_speed, -100.0, 100.0))
        self._call(lambda: self.tank.on_for_seconds(l, r, float(seconds), brake=brake, block=block))

    def tank_on_for_rotations(
        self,
        left_speed: float,
        right_speed: float,
        rotations: float,
        brake: bool = True,
        block: bool = True,
    ) -> None:
        if not self.enabled:
            log(
                "DEBUG",
                (
                    "[dry-run] tank_on_for_rotations "
                    f"l={left_speed:.1f} r={right_speed:.1f} rot={rotations:.2f}"
                ),
            )
            time.sleep(0.5)
            return
        l = self._speed(_clamp(left_speed, -100.0, 100.0))
        r = self._speed(_clamp(right_speed, -100.0, 100.0))
        self._call(lambda: self.tank.on_for_rotations(l, r, float(rotations), brake=brake, block=block))

    def lift_up_for_rotations(
        self,
        up_speed_pct: float,
        up_rotations: float,
        down_sign: int,
        brake: bool = True,
        block: bool = True,
    ) -> None:
        speed = -1.0 * float(down_sign) * abs(float(up_speed_pct))
        if not self.enabled:
            log(
                "DEBUG",
                f"[dry-run] lift_up_for_rotations speed={speed:.1f} rot={up_rotations:.2f}",
            )
            time.sleep(0.5)
            return
        lift_speed = self._speed(_clamp(speed, -100.0, 100.0))
        self._call(
            lambda: self.lift.on_for_rotations(
                lift_speed,
                float(up_rotations),
                brake=brake,
                block=block,
            )
        )

    def lift_down_until_resistance(
        self,
        down_speed_pct: float,
        down_sign: int,
        max_down_rotations: float,
        pos_delta_thr_deg: float,
        speed_thr_deg_s: float,
        confirm_cycles: int,
        poll_interval_s: float,
        min_run_sec: float,
        stop_max_sec: float,
        use_touch_sensor: bool,
        touch_debounce_ms: int,
        use_software_fallback: bool,
    ) -> Dict[str, float]:
        if not self.enabled:
            log("DEBUG", "[dry-run] lift_down_until_resistance")
            time.sleep(0.5)
            return {"reason": "dry-run", "moved_deg": 0.0, "cycles": 0}

        self._ensure_connected()
        speed = float(down_sign) * abs(float(down_speed_pct))

        start_pos = float(getattr(self.lift, "position", 0.0))
        prev_pos = start_pos
        immobile_count = 0
        slow_count = 0
        touch_count = 0
        cycles = 0
        reason = "unknown"

        lift_speed = self._speed(_clamp(speed, -100.0, 100.0))
        self._call(lambda: self.lift.on(lift_speed, brake=False, block=False))

        start_ts = time.monotonic()
        try:
            while True:
                time.sleep(max(0.01, float(poll_interval_s)))
                cycles += 1
                elapsed = time.monotonic() - start_ts

                pos = float(getattr(self.lift, "position", prev_pos))
                moved_total = abs(pos - start_pos)
                moved_step = abs(pos - prev_pos)
                prev_pos = pos

                raw_speed = getattr(self.lift, "speed", 0.0)
                speed_now = abs(float(raw_speed)) if raw_speed is not None else 0.0

                if use_touch_sensor and (self.lift_touch is not None):
                    pressed = self._touch_pressed()
                    if pressed:
                        poll_ms = max(1.0, float(poll_interval_s) * 1000.0)
                        debounce_cycles = max(1, int((float(touch_debounce_ms) + poll_ms - 1.0) // poll_ms))
                        touch_count += 1
                        if touch_count >= debounce_cycles:
                            reason = "touch_sensor"
                            break
                    else:
                        touch_count = 0

                # Kurzer Anlaufpuffer gegen Fehltrigger direkt nach dem Start.
                if elapsed < float(min_run_sec):
                    continue

                if use_software_fallback:
                    is_stalled = False
                    try:
                        is_stalled = bool(getattr(self.lift, "is_stalled"))
                    except Exception:
                        is_stalled = False
                    if is_stalled:
                        reason = "is_stalled"
                        break

                    if moved_step < float(pos_delta_thr_deg):
                        immobile_count += 1
                    else:
                        immobile_count = 0

                    if speed_now < float(speed_thr_deg_s):
                        slow_count += 1
                    else:
                        slow_count = 0

                    if immobile_count >= int(confirm_cycles):
                        reason = "position_delta"
                        break

                    if slow_count >= int(confirm_cycles):
                        reason = "speed_threshold"
                        break

                if moved_total >= float(max_down_rotations) * 360.0:
                    reason = "max_down_rotations"
                    break
                if elapsed >= float(stop_max_sec):
                    reason = "max_down_time"
                    break
        finally:
            try:
                self._call(lambda: self.lift.off(brake=True))
            except Exception:
                try:
                    self._call(lambda: self.lift.stop())
                except Exception:
                    pass

        moved_deg = abs(float(getattr(self.lift, "position", start_pos)) - start_pos)
        elapsed_ms = (time.monotonic() - start_ts) * 1000.0
        return {
            "reason": reason,
            "moved_deg": moved_deg,
            "cycles": cycles,
            "elapsed_ms": elapsed_ms,
            "touch_sensor_used": bool(use_touch_sensor and (self.lift_touch is not None)),
        }

    def speak(self, text: str) -> None:
        if not self.enabled:
            log("INFO", f"[dry-run] EV3 würde sagen: {text}")
            return

        def _speak_call():
            Sound = self.sound_module.Sound
            sound = Sound()
            try:
                sound.speak(text, play_type=Sound.PLAY_NO_WAIT_FOR_COMPLETE)
            except Exception:
                sound.speak(text)

        self._call(_speak_call)


def _best_detection(
    detections: List[Tuple[float, float, float, float, float]]
) -> Optional[Tuple[float, float, float, float, float]]:
    if not detections:
        return None
    return max(detections, key=lambda d: d[4])


def parse_args():
    p = argparse.ArgumentParser(
        description="Autonomer Butter-Sucher: sucht, fährt hin, hebt an und ruft 'butter'."
    )
    p.add_argument("--hef", default="/home/gast/model.hef", help="Pfad zur HEF-Datei")
    p.add_argument(
        "--source",
        default="auto",
        help=(
            "Kameraquelle: auto | 0 | v4l2:0 | /dev/videoX | "
            "gst:<pipeline> | file:/path/video.mp4 | http(s)://... | rtsp://..."
        ),
    )
    p.add_argument("--width", type=int, default=1280, help="Capture-Breite")
    p.add_argument("--height", type=int, default=720, help="Capture-Höhe")
    p.add_argument("--score-thr", type=float, default=0.40, help="Detektor-Score-Schwelle")
    p.add_argument("--iou-thr", type=float, default=0.45, help="NMS-IoU-Schwelle")
    p.add_argument("--max-det", type=int, default=100, help="Max. Detections pro Frame")
    p.add_argument(
        "--rotate-180",
        dest="rotate_180",
        action="store_true",
        help="Kamerabild vor Inferenz um 180 Grad drehen.",
    )
    p.add_argument(
        "--no-rotate-180",
        dest="rotate_180",
        action="store_false",
        help="180-Grad-Rotation deaktivieren.",
    )
    p.set_defaults(rotate_180=True)

    p.add_argument("--butter-thr", type=float, default=0.75, help="Butter-Score-Schwelle (0..1)")
    p.add_argument(
        "--track-thr",
        type=float,
        default=0.55,
        help="Score-Schwelle fuer Tracking/Lenken nach initialem Lock",
    )
    p.add_argument(
        "--confirm-frames",
        type=int,
        default=2,
        help="Anzahl aufeinanderfolgender Frames für Butter-Lock",
    )

    p.add_argument("--left-port", default="OUTPUT_A", help="Linker Tank-Motor-Port (A/B/C/D oder OUTPUT_*)")
    p.add_argument("--right-port", default="OUTPUT_D", help="Rechter Tank-Motor-Port (A/B/C/D oder OUTPUT_*)")
    p.add_argument("--lift-port", default="OUTPUT_C", help="Medium-Motor/Lift-Port (A/B/C/D oder OUTPUT_*)")

    p.add_argument("--search-turn-speed", type=float, default=8.0, help="Drehgeschwindigkeit im Suchmodus")
    p.add_argument("--search-min-turn-sec", type=float, default=0.22, help="Min. Drehdauer")
    p.add_argument("--search-max-turn-sec", type=float, default=0.45, help="Max. Drehdauer")
    p.add_argument("--search-pause-min-sec", type=float, default=0.35, help="Min. Haltedauer im Suchmodus")
    p.add_argument("--search-pause-max-sec", type=float, default=0.80, help="Max. Haltedauer im Suchmodus")
    p.add_argument("--search-forward-speed", type=float, default=20.0, help="Vorwärts-Speed im Suchmodus")
    p.add_argument("--search-min-forward-sec", type=float, default=0.25, help="Min. Vorwärtsdauer")
    p.add_argument("--search-max-forward-sec", type=float, default=0.8, help="Max. Vorwärtsdauer")

    p.add_argument("--approach-speed", type=float, default=24.0, help="Basis-Speed beim Anfahren")
    p.add_argument("--turn-kp", type=float, default=40.0, help="Lenk-Kp auf x-Fehler")
    p.add_argument("--turn-max-delta", type=float, default=14.0, help="Max. Lenkanteil")
    p.add_argument("--near-y-thr", type=float, default=0.72, help="Nahe-Schwelle (normierte y-Position)")
    p.add_argument(
        "--lost-frames-after-near",
        type=int,
        default=3,
        help="Frames ohne Detection nach 'nahe' bis Pick-Sequenz startet",
    )
    p.add_argument(
        "--lost-track-frames",
        type=int,
        default=8,
        help="Frames ohne Detection (ohne near) bis zurück in Suche",
    )

    p.add_argument("--push-speed", type=float, default=25.0, help="Tank-Speed beim Schieben")
    p.add_argument("--push-rotations", type=float, default=2.0, help="Schiebe-Rotationen vorwärts")

    p.add_argument("--lift-down-speed", type=float, default=20.0, help="Lift-Speed beim Absenken")
    p.add_argument("--lift-up-speed", type=float, default=30.0, help="Lift-Speed beim Anheben")
    p.add_argument("--lift-up-rotations", type=float, default=3.0, help="Rotationen nach oben")
    p.add_argument(
        "--lift-down-max-rotations",
        type=float,
        default=20.0,
        help="Max. Absenkweg (Sicherheitslimit, regulaer bis Widerstand)",
    )
    p.add_argument(
        "--lift-down-sign",
        type=int,
        default=-1,
        choices=[-1, 1],
        help="Richtung fuer DOWN (+1 oder -1, je nach Mechanik)",
    )
    p.add_argument(
        "--lift-stop-sensor",
        default="touch",
        choices=["touch", "none"],
        help="Harte Stop-Bedingung beim Absenken: touch|none",
    )
    p.add_argument(
        "--lift-stop-port",
        default="INPUT_1",
        help="Sensor-Port fuer Lift-Stop (1/2/3/4 oder INPUT_*)",
    )
    p.add_argument(
        "--lift-stop-required",
        dest="lift_stop_required",
        action="store_true",
        help="Start abbrechen, wenn Lift-Stop-Sensor nicht verfügbar ist.",
    )
    p.add_argument(
        "--no-lift-stop-required",
        dest="lift_stop_required",
        action="store_false",
        help="Bei fehlendem Lift-Stop-Sensor mit Software-Fallback weiterlaufen.",
    )
    p.set_defaults(lift_stop_required=True)
    p.add_argument(
        "--lift-touch-active-state",
        default="pressed",
        choices=["pressed", "released"],
        help="Welcher Touch-Zustand als 'Endlage erreicht' gilt.",
    )
    p.add_argument(
        "--lift-stop-debounce-ms",
        type=int,
        default=20,
        help="Entprellzeit fuer Touch-Trigger in Millisekunden.",
    )
    p.add_argument(
        "--lift-stop-max-sec",
        type=float,
        default=3.0,
        help="Maximale Absenkzeit als Sicherheitslimit.",
    )
    p.add_argument(
        "--lift-software-fallback",
        dest="lift_software_fallback",
        action="store_true",
        help="Software-Stall-Fallback aktivieren.",
    )
    p.add_argument(
        "--no-lift-software-fallback",
        dest="lift_software_fallback",
        action="store_false",
        help="Software-Stall-Fallback deaktivieren (nur Sensor + Hardlimits).",
    )
    p.set_defaults(lift_software_fallback=True)

    p.add_argument("--stall-pos-delta", type=float, default=1.8, help="Pos-Delta-Schwelle in Grad")
    p.add_argument(
        "--stall-speed-thr",
        type=float,
        default=28.0,
        help="Speed-Schwelle in deg/s (groesser = sensibler)",
    )
    p.add_argument(
        "--stall-confirm-cycles",
        type=int,
        default=1,
        help="Benoetigte Zyklen fuer Stall (kleiner = sensibler)",
    )
    p.add_argument(
        "--stall-poll-sec",
        type=float,
        default=0.02,
        help="Polling-Intervall Stall-Erkennung (kleiner = schneller Reaktion)",
    )
    p.add_argument(
        "--stall-min-run-sec",
        type=float,
        default=0.12,
        help="Minimaler Lauf vor Stall-Pruefung (Sekunden)",
    )

    p.add_argument("--ev3-host", default="10.42.0.3", help="EV3 IP")
    p.add_argument("--ev3-port", type=int, default=18812, help="EV3 RPyC Port")
    p.add_argument("--rpc-timeout", type=float, default=10.0, help="RPyC Request-Timeout in Sekunden")
    p.add_argument("--speak-text", default="butter", help="Text, den der EV3 aussprechen soll")
    p.add_argument(
        "--telemetry-json",
        default="",
        help="Optionaler Pfad fuer Laufzeit-Telemetrie (JSON) fuer externes Monitoring/Web-Overlay.",
    )
    p.add_argument(
        "--telemetry-interval-ms",
        type=int,
        default=120,
        help="Mindestintervall fuer Telemetrie-Updates in Millisekunden.",
    )

    p.add_argument("--dry-run", action="store_true", help="Keine EV3-Motor/Sound-Kommandos ausführen")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not (0.0 <= args.butter_thr <= 1.0):
        log("ERROR", "--butter-thr muss im Bereich 0..1 liegen.")
        return 2
    if not (0.0 <= args.track_thr <= 1.0):
        log("ERROR", "--track-thr muss im Bereich 0..1 liegen.")
        return 2
    if args.confirm_frames < 1:
        log("ERROR", "--confirm-frames muss >= 1 sein.")
        return 2
    if args.search_min_turn_sec <= 0 or args.search_max_turn_sec < args.search_min_turn_sec:
        log("ERROR", "Ungültige Such-Drehzeiten.")
        return 2
    if args.search_pause_min_sec <= 0 or args.search_pause_max_sec < args.search_pause_min_sec:
        log("ERROR", "Ungültige Such-Pausenzeiten.")
        return 2
    if args.search_min_forward_sec <= 0 or args.search_max_forward_sec < args.search_min_forward_sec:
        log("ERROR", "Ungültige Such-Vorwärtszeiten.")
        return 2
    if args.lost_frames_after_near < 1:
        log("ERROR", "--lost-frames-after-near muss >= 1 sein.")
        return 2
    if args.stall_confirm_cycles < 1:
        log("ERROR", "--stall-confirm-cycles muss >= 1 sein.")
        return 2
    if args.stall_poll_sec <= 0:
        log("ERROR", "--stall-poll-sec muss > 0 sein.")
        return 2
    if args.stall_min_run_sec < 0:
        log("ERROR", "--stall-min-run-sec muss >= 0 sein.")
        return 2
    if args.lift_stop_debounce_ms < 0:
        log("ERROR", "--lift-stop-debounce-ms muss >= 0 sein.")
        return 2
    if args.lift_stop_max_sec <= 0:
        log("ERROR", "--lift-stop-max-sec muss > 0 sein.")
        return 2
    if args.telemetry_interval_ms < 20:
        log("ERROR", "--telemetry-interval-ms muss >= 20 sein.")
        return 2
    if args.lift_stop_sensor != "touch":
        log("ERROR", "Dieses Skript ist auf Lift-Stop nur per TouchSensor festgelegt (--lift-stop-sensor touch).")
        return 2

    # Harte Vorgabe: kein softwarebasierter Widerstands-/Stall-Stop mehr.
    args.lift_stop_required = True
    args.lift_software_fallback = False

    stop_flag = [False]

    def on_signal(sig, _frame):
        stop_flag[0] = True
        log("INFO", f"Signal {sig} empfangen, stoppe...")

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    log(
        "INFO",
        (
            "Starte Autonomie | "
            f"butter-thr={args.butter_thr:.2f} confirm={args.confirm_frames} "
            f"track-thr={args.track_thr:.2f} ports=({args.left_port},{args.right_port}) lift={args.lift_port} "
            f"rotate180={args.rotate_180} "
            f"lift_stop={args.lift_stop_sensor}@{args.lift_stop_port} "
            f"required={args.lift_stop_required} fallback={args.lift_software_fallback} (touch-only)"
        ),
    )

    detector = None
    robot = None
    cap = None

    frame_idx = 0
    no_frame_count = 0
    source_idx = 0

    state = "SEARCH_RANDOM"
    track_confirm = 0
    near_seen = False
    lost_after_near = 0
    lost_track = 0
    search_phase = "PAUSE"
    search_until_ts = time.monotonic() + random.uniform(args.search_pause_min_sec, args.search_pause_max_sec)
    next_telemetry_ts = 0.0

    try:
        detector = HailoDetector(
            hef_path=args.hef,
            score_thr=args.score_thr,
            iou_thr=args.iou_thr,
            max_det=args.max_det,
            rotate_180=args.rotate_180,
        )

        robot = EV3Robot(
            host=args.ev3_host,
            port=args.ev3_port,
            timeout=args.rpc_timeout,
            left_port=args.left_port,
            right_port=args.right_port,
            lift_port=args.lift_port,
            lift_stop_sensor=args.lift_stop_sensor,
            lift_stop_port=args.lift_stop_port,
            lift_stop_required=args.lift_stop_required,
            lift_touch_active_state=args.lift_touch_active_state,
            lift_stop_debounce_ms=args.lift_stop_debounce_ms,
            lift_software_fallback=args.lift_software_fallback,
            enabled=not args.dry_run,
        )

        while not stop_flag[0]:
            if cap is None:
                try:
                    cap, opened_source, source_idx = _open_capture_robust(
                        args.source, args.width, args.height, start_idx=source_idx
                    )
                    log("INFO", f"Kameraquelle geöffnet: {opened_source}")
                    no_frame_count = 0
                except Exception as exc:
                    log("WARN", f"Kamera noch nicht verfügbar: {exc}")
                    time.sleep(1.0)
                    continue

            ok, frame = cap.read()
            if not ok or frame is None:
                no_frame_count += 1
                if no_frame_count % 20 == 1:
                    log("WARN", "Kamera liefert kein Frame.")
                if no_frame_count >= 140:
                    log("WARN", "Zu viele fehlende Frames, wechsle Quelle...")
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    source_idx = (source_idx + 1) % len(_source_candidates(args.source))
                    no_frame_count = 0
                    time.sleep(0.2)
                else:
                    time.sleep(0.05)
                continue
            no_frame_count = 0

            frame_idx += 1
            _vis, detections = detector.infer(frame)
            best = _best_detection(detections)

            has_butter = False
            has_track = False
            best_score = 0.0
            cx = cy = 0.0
            if best is not None:
                x1, y1, x2, y2, score = best
                best_score = float(score)
                cx = 0.5 * (x1 + x2)
                cy = 0.5 * (y1 + y2)
                has_butter = best_score >= args.butter_thr
                has_track = best_score >= args.track_thr

            if args.telemetry_json:
                now = time.monotonic()
                if now >= next_telemetry_ts:
                    next_telemetry_ts = now + (float(args.telemetry_interval_ms) / 1000.0)
                    try:
                        frame_h = int(frame.shape[0])
                        frame_w = int(frame.shape[1])
                        top = sorted(detections, key=lambda d: d[4], reverse=True)[:30]
                        serializable_det = [
                            [float(d[0]), float(d[1]), float(d[2]), float(d[3]), float(d[4])] for d in top
                        ]
                        _write_json_atomic(
                            args.telemetry_json,
                            {
                                "ts": time.time(),
                                "state": state,
                                "frame_w": frame_w,
                                "frame_h": frame_h,
                                "rotate_180": bool(args.rotate_180),
                                "det_count": len(detections),
                                "best_score": float(best_score),
                                "has_butter": bool(has_butter),
                                "near_seen": bool(near_seen),
                                "detections": serializable_det,
                            },
                        )
                    except Exception as exc:
                        if frame_idx % 100 == 1:
                            log("WARN", f"Telemetry-Write fehlgeschlagen: {exc}")

            if frame_idx % 30 == 0:
                log(
                    "INFO",
                    (
                        f"state={state} frame={frame_idx} det={len(detections)} "
                        f"best={best_score:.3f} near={near_seen} lost_near={lost_after_near}"
                    ),
                )
            if best_score >= 0.50 and not has_butter and frame_idx % 10 == 0:
                log(
                    "INFO",
                    (
                        f"Butter knapp unter --butter-thr: best={best_score:.3f} "
                        f"(thr={args.butter_thr:.2f}, track-thr={args.track_thr:.2f})"
                    ),
                )

            if state == "SEARCH_RANDOM":
                if has_butter:
                    track_confirm += 1
                    # Bei erstem Treffer nicht weiter random fahren, sonst verlieren wir
                    # die Box vor der zweiten Bestätigung.
                    robot.tank_off(brake=True)
                else:
                    track_confirm = 0

                if track_confirm >= args.confirm_frames:
                    state = "APPROACH_BUTTER"
                    near_seen = False
                    lost_after_near = 0
                    lost_track = 0
                    robot.tank_off(brake=True)
                    log("INFO", f"State -> {state} (butter lock, score={best_score:.3f})")
                    continue
                if track_confirm > 0:
                    continue

                now_ts = time.monotonic()
                if now_ts >= search_until_ts:
                    if search_phase == "PAUSE":
                        direction = random.choice([-1, 1])
                        ts = abs(float(args.search_turn_speed))
                        if direction < 0:
                            l_turn, r_turn = -ts, ts
                        else:
                            l_turn, r_turn = ts, -ts
                        robot.tank_on(l_turn, r_turn, brake=False, block=False)
                        search_phase = "TURN"
                        search_until_ts = now_ts + random.uniform(args.search_min_turn_sec, args.search_max_turn_sec)
                    else:
                        robot.tank_off(brake=True)
                        search_phase = "PAUSE"
                        search_until_ts = now_ts + random.uniform(args.search_pause_min_sec, args.search_pause_max_sec)
                continue

            if state == "APPROACH_BUTTER":
                if has_track:
                    lost_track = 0
                    frame_w = float(frame.shape[1])
                    frame_h = float(frame.shape[0])

                    err_x = (cx - (frame_w * 0.5)) / frame_w
                    turn = _clamp(args.turn_kp * err_x, -args.turn_max_delta, args.turn_max_delta)
                    left_speed = _clamp(args.approach_speed + turn, -100.0, 100.0)
                    right_speed = _clamp(args.approach_speed - turn, -100.0, 100.0)

                    robot.tank_on(left_speed, right_speed, brake=False, block=False)

                    y_norm = cy / frame_h
                    if y_norm >= args.near_y_thr:
                        if not near_seen:
                            log("INFO", f"Butter nah erkannt (y_norm={y_norm:.3f})")
                        near_seen = True
                        lost_after_near = 0
                else:
                    robot.tank_off(brake=True)
                    lost_track += 1

                    if near_seen:
                        lost_after_near += 1
                        if lost_after_near >= args.lost_frames_after_near:
                            state = "PICK_SEQUENCE"
                            log("INFO", f"State -> {state} (butter near+lost)")
                    elif lost_track >= args.lost_track_frames:
                        state = "SEARCH_RANDOM"
                        track_confirm = 0
                        near_seen = False
                        lost_after_near = 0
                        lost_track = 0
                        search_phase = "PAUSE"
                        search_until_ts = time.monotonic() + random.uniform(
                            args.search_pause_min_sec, args.search_pause_max_sec
                        )
                        robot.tank_off(brake=True)
                        log("INFO", "State -> SEARCH_RANDOM (track verloren)")
                continue

            if state == "PICK_SEQUENCE":
                robot.tank_off(brake=True)

                lower = robot.lift_down_until_resistance(
                    down_speed_pct=args.lift_down_speed,
                    down_sign=args.lift_down_sign,
                    max_down_rotations=args.lift_down_max_rotations,
                    pos_delta_thr_deg=args.stall_pos_delta,
                    speed_thr_deg_s=args.stall_speed_thr,
                    confirm_cycles=args.stall_confirm_cycles,
                    poll_interval_s=args.stall_poll_sec,
                    min_run_sec=args.stall_min_run_sec,
                    stop_max_sec=args.lift_stop_max_sec,
                    use_touch_sensor=(args.lift_stop_sensor == "touch"),
                    touch_debounce_ms=args.lift_stop_debounce_ms,
                    use_software_fallback=args.lift_software_fallback,
                )
                log(
                    "INFO",
                    (
                        "Lift down stop | "
                        f"reason={lower.get('reason')} moved_deg={lower.get('moved_deg', 0.0):.1f} "
                        f"cycles={int(lower.get('cycles', 0))} "
                        f"elapsed_ms={float(lower.get('elapsed_ms', 0.0)):.1f} "
                        f"touch_used={bool(lower.get('touch_sensor_used', False))}"
                    ),
                )

                robot.tank_on_for_rotations(
                    args.push_speed,
                    args.push_speed,
                    args.push_rotations,
                    brake=True,
                    block=True,
                )

                robot.lift_up_for_rotations(
                    up_speed_pct=args.lift_up_speed,
                    up_rotations=args.lift_up_rotations,
                    down_sign=args.lift_down_sign,
                    brake=True,
                    block=True,
                )

                robot.speak(args.speak_text)
                robot.tank_off(brake=True)

                state = "DONE_STOP"
                log("INFO", "State -> DONE_STOP")
                continue

            if state == "DONE_STOP":
                break

        return 0

    except Exception as exc:
        log("ERROR", f"Laufzeitfehler: {exc}")
        return 1

    finally:
        if cap is not None:
            cap.release()
        if detector is not None:
            detector.close()
        if robot is not None:
            try:
                robot.tank_off(brake=True)
            except Exception:
                pass
            robot.close()
        if args.telemetry_json:
            try:
                _write_json_atomic(
                    args.telemetry_json,
                    {
                        "ts": time.time(),
                        "state": "STOPPED",
                        "frame_w": 0,
                        "frame_h": 0,
                        "rotate_180": bool(args.rotate_180),
                        "det_count": 0,
                        "best_score": 0.0,
                        "has_butter": False,
                        "near_seen": False,
                        "detections": [],
                    },
                )
            except Exception:
                pass
        log("INFO", "Beendet.")


if __name__ == "__main__":
    raise SystemExit(main())

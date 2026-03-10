#!/usr/bin/env python3
"""Startet manuell einen RPyC-Classic-Server auf EV3dev und spielt Startsound."""

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from typing import Optional


def log(level: str, msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def find_rpyc_module() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("rpyc_classic") is not None
    except Exception:
        return False


def play_start_sound(text: str, require_sound: bool) -> None:
    try:
        from ev3dev2.sound import Sound  # type: ignore

        sound = Sound()
        sound.speak(text)
        log("INFO", "EV3 Startsound abgespielt.")
    except Exception as exc:
        if require_sound:
            raise RuntimeError(f"Sound konnte nicht abgespielt werden: {exc}") from exc
        log("WARN", f"Sound konnte nicht abgespielt werden: {exc}")


def run_server(host: str, port: int, verbose: bool) -> int:
    cmd = [sys.executable, "-m", "rpyc_classic", "--host", host, "--port", str(port)]
    if verbose:
        log("INFO", f"Starte RPyC-Server: {' '.join(cmd)}")
    else:
        log("INFO", f"Starte RPyC-Server auf {host}:{port}")

    proc = subprocess.Popen(cmd)
    stop = False

    def on_signal(signum, _frame):
        nonlocal stop
        stop = True
        log("INFO", f"Signal {signum} empfangen, stoppe RPyC-Server...")

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    while True:
        rc = proc.poll()
        if rc is not None:
            if rc == 0:
                log("INFO", "RPyC-Server sauber beendet.")
            else:
                log("ERROR", f"RPyC-Server beendet mit Exit-Code {rc}.")
            return rc

        if stop:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log("WARN", "RPyC-Server reagiert nicht auf terminate, sende kill.")
                proc.kill()
                proc.wait(timeout=2)
            return 0

        time.sleep(0.2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manuelles EV3-Skript: Sound spielen und rpyc_classic starten."
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind-Adresse (Default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=18812, help="RPyC-Port (Default: 18812)")
    parser.add_argument(
        "--sound-text",
        default="RPyC server started",
        help="Text, der als EV3-Sound gesprochen wird",
    )
    parser.add_argument(
        "--no-sound",
        action="store_true",
        help="Startsound deaktivieren",
    )
    parser.add_argument(
        "--require-sound",
        action="store_true",
        help="Bei Sound-Fehler abbrechen (statt nur Warnung).",
    )
    parser.add_argument("--verbose", action="store_true", help="Mehr Log-Ausgabe")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not find_rpyc_module():
        log("ERROR", "Modul 'rpyc_classic' nicht gefunden. Bitte auf EV3 installieren: pip3 install rpyc")
        return 2

    if not args.no_sound:
        try:
            play_start_sound(args.sound_text, require_sound=args.require_sound)
        except Exception as exc:
            log("ERROR", str(exc))
            return 3

    return run_server(args.host, args.port, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())


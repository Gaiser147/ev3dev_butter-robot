#!/usr/bin/env python3
"""Robuster RPyC-USB-Client für Raspberry Pi -> EV3dev."""

import argparse
import ipaddress
import os
import pwd
import random
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple


def log(level: str, msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Dieses Skript muss mit Root-Rechten laufen (z. B. 'sudo ...').")


def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def iface_exists(iface: str) -> bool:
    return os.path.isdir(f"/sys/class/net/{iface}")


def get_iface_names() -> List[str]:
    names = []
    for name in os.listdir("/sys/class/net"):
        if name != "lo":
            names.append(name)
    return sorted(names)


def score_interface(iface: str) -> Tuple[int, Dict[str, str]]:
    score = 0
    details: Dict[str, str] = {}

    lower = iface.lower()
    if lower.startswith("usb") or lower.startswith("enx"):
        score += 50
        details["name_hint"] = "usb-like"

    dev_link = os.path.realpath(f"/sys/class/net/{iface}/device")
    if "/usb" in dev_link:
        score += 40
        details["dev_path"] = "contains /usb"

    uevent = read_text(f"/sys/class/net/{iface}/device/uevent")
    for token in ("DRIVER=cdc_ether", "DRIVER=cdc_ncm", "DRIVER=rndis_host", "DRIVER=usbnet"):
        if token in uevent:
            score += 70
            details["driver"] = token.replace("DRIVER=", "")
            break

    carrier = read_text(f"/sys/class/net/{iface}/carrier")
    if carrier == "1":
        score += 10
        details["carrier"] = "up"

    operstate = read_text(f"/sys/class/net/{iface}/operstate")
    if operstate in ("up", "unknown"):
        score += 5
        details["operstate"] = operstate

    return score, details


def discover_usb_interface(verbose: bool) -> str:
    candidates = []
    for iface in get_iface_names():
        score, details = score_interface(iface)
        candidates.append((score, iface, details))

    candidates.sort(reverse=True, key=lambda x: x[0])
    if verbose:
        for score, iface, details in candidates:
            log("DEBUG", f"Interface {iface}: score={score}, details={details}")

    if not candidates:
        raise RuntimeError("Keine Netzwerkinterfaces gefunden.")

    best_score, best_iface, _ = candidates[0]
    if best_score < 30:
        raise RuntimeError(
            "Kein plausibles USB-Netzinterface gefunden. "
            "Prüfe EV3 USB-Kabel/USB-Modus. (Bei Kernel error -71 oft Kabel/Signalproblem)"
        )
    return best_iface


def set_interface_address(iface: str, pi_cidr: str, verbose: bool) -> None:
    if verbose:
        log("DEBUG", f"Konfiguriere Interface {iface} mit {pi_cidr}")

    run_cmd(["ip", "link", "set", "dev", iface, "up"], check=True)
    run_cmd(["ip", "-4", "addr", "flush", "dev", iface], check=True)
    run_cmd(["ip", "-4", "addr", "add", pi_cidr, "dev", iface], check=True)


def ping_host(host: str, timeout_s: int = 1) -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", str(timeout_s), host],
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


def wait_for_tcp(host: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


@dataclass
class RetryState:
    current_sleep: float
    initial: float
    max_sleep: float

    def reset(self) -> None:
        self.current_sleep = self.initial

    def next_sleep(self) -> float:
        base = self.current_sleep
        jitter = random.uniform(0.0, min(0.5, base * 0.25))
        sleep_time = base + jitter
        self.current_sleep = min(self.current_sleep * 2.0, self.max_sleep)
        return sleep_time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Robuste USB-RPyC-Verbindung Pi -> EV3dev")
    parser.add_argument("--iface", default="auto", help="USB-Netzinterface (auto|usb0|enx...)")
    parser.add_argument("--pi-ip", default="10.42.0.1/24", help="Pi USB-IP im CIDR-Format")
    parser.add_argument("--ev3-ip", default="10.42.0.3", help="EV3 USB-IP")
    parser.add_argument("--port", type=int, default=18812, help="RPyC-Port (Default: 18812)")
    parser.add_argument("--connect-timeout", type=float, default=5.0, help="TCP connect timeout")
    parser.add_argument("--rpc-timeout", type=float, default=10.0, help="RPyC request timeout")
    parser.add_argument("--retry-initial", type=float, default=1.0, help="Initiales Retry-Intervall")
    parser.add_argument("--retry-max", type=float, default=15.0, help="Maximales Retry-Intervall")
    parser.add_argument("--ping-interval", type=float, default=3.0, help="Healthcheck-Intervall")
    parser.add_argument("--oneshot", action="store_true", help="Einmal verbinden/testen und beenden")
    parser.add_argument("--verbose", action="store_true", help="Debug-Logs aktivieren")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    ipaddress.ip_interface(args.pi_ip)
    ipaddress.ip_address(args.ev3_ip)
    if args.port <= 0 or args.port > 65535:
        raise ValueError("Ungültiger Port.")
    if args.retry_initial <= 0 or args.retry_max <= 0 or args.retry_initial > args.retry_max:
        raise ValueError("Ungültige Retry-Werte.")
    if args.ping_interval <= 0:
        raise ValueError("ping-interval muss > 0 sein.")


def import_rpyc():
    # Wenn das Skript mit sudo gestartet wurde, liegt rpyc oft nur im
    # User-Site-Packages des aufrufenden Users (SUDO_USER).
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            sudo_home = pwd.getpwnam(sudo_user).pw_dir
            py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
            user_site = os.path.join(
                sudo_home, ".local", "lib", f"python{py_ver}", "site-packages"
            )
            if os.path.isdir(user_site) and user_site not in sys.path:
                sys.path.insert(0, user_site)
        except Exception:
            pass

    try:
        import rpyc  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Python-Modul 'rpyc' fehlt auf dem Pi. Installiere z. B.: "
            "python3 -m pip install --user --break-system-packages rpyc"
        ) from exc

    version = getattr(rpyc, "__version__", "unknown")
    major = None
    if isinstance(version, (tuple, list)) and version:
        major = int(version[0])
        version_str = ".".join(str(v) for v in version)
    elif isinstance(version, str):
        version_str = version
        try:
            major = int(version.split(".")[0])
        except Exception:
            major = None
    else:
        version_str = str(version)

    # EV3dev läuft in der Praxis meist mit rpyc 4.x/5.x.
    # rpyc 6.x ist protokoll-inkompatibel zu diesen Servern und führt zu
    # "invalid message type: 18".
    if major is not None and major >= 6:
        raise RuntimeError(
            f"Inkompatible rpyc-Version auf dem Pi erkannt ({version_str}). "
            "Für EV3dev bitte rpyc<6 nutzen, z. B.: "
            "python3 -m pip install --user --break-system-packages 'rpyc<6'"
        )

    return rpyc


def choose_interface(iface_arg: str, verbose: bool) -> str:
    if iface_arg != "auto":
        if not iface_exists(iface_arg):
            raise RuntimeError(f"Angefordertes Interface '{iface_arg}' existiert nicht.")
        return iface_arg
    return discover_usb_interface(verbose=verbose)


def verify_classic_connection(conn) -> None:
    # Prüft, ob es tatsächlich ein Classic-Server ist (modules vorhanden).
    _ = conn.modules["sys"].version


def monitor_connection(conn, ping_interval: float, stop_flag: List[bool]) -> None:
    while not stop_flag[0]:
        conn.ping()
        time.sleep(ping_interval)


def main() -> int:
    try:
        args = parse_args()
        validate_args(args)
        require_root()
        rpyc = import_rpyc()
    except PermissionError as exc:
        log("ERROR", str(exc))
        return 2
    except ValueError as exc:
        log("ERROR", f"Ungültige Parameter: {exc}")
        return 3
    except RuntimeError as exc:
        log("ERROR", str(exc))
        return 4

    stop_flag = [False]

    def on_signal(signum, _frame):
        stop_flag[0] = True
        log("INFO", f"Signal {signum} empfangen, stoppe...")

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    retry = RetryState(
        current_sleep=args.retry_initial,
        initial=args.retry_initial,
        max_sleep=args.retry_max,
    )

    conn = None
    current_iface = None

    while not stop_flag[0]:
        try:
            current_iface = choose_interface(args.iface, verbose=args.verbose)
            log("INFO", f"Nutze Interface: {current_iface}")

            set_interface_address(current_iface, args.pi_ip, verbose=args.verbose)
            if ping_host(args.ev3_ip, timeout_s=1):
                log("INFO", f"EV3 ({args.ev3_ip}) antwortet auf Ping.")
            else:
                log("WARN", f"EV3 ({args.ev3_ip}) antwortet nicht auf Ping (noch).")

            if not wait_for_tcp(args.ev3_ip, args.port, timeout_s=args.connect_timeout):
                raise ConnectionError(
                    f"TCP {args.ev3_ip}:{args.port} nicht erreichbar. "
                    "Läuft rpyc_classic auf dem EV3?"
                )

            log("INFO", f"Verbinde RPyC zu {args.ev3_ip}:{args.port} ...")
            conn = rpyc.classic.connect(
                args.ev3_ip,
                port=args.port,
                keepalive=True,
            )
            conn._config["sync_request_timeout"] = args.rpc_timeout
            verify_classic_connection(conn)
            log("INFO", "RPyC-Verbindung steht (Classic-Server bestätigt).")
            retry.reset()

            if args.oneshot:
                conn.ping()
                log("INFO", "Oneshot erfolgreich.")
                conn.close()
                return 0

            monitor_connection(conn, args.ping_interval, stop_flag)
            if stop_flag[0]:
                break

        except Exception as exc:
            log("ERROR", f"Verbindungsproblem: {exc}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None

        if stop_flag[0]:
            break

        sleep_time = retry.next_sleep()
        log("INFO", f"Reconnect in {sleep_time:.1f}s ...")
        time.sleep(sleep_time)

    log("INFO", "Client beendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Butter Robot (Raspberry Pi 5 + Hailo + EV3dev)

Autonomer "Butter-Roboter" mit:
- Raspberry Pi 5 (Ubuntu)
- Hailo AI HAT+ (26 TOPS)
- Raspberry Pi Kamera (libcamera)
- LEGO EV3 (ev3dev) via USB + RPyC

Das Projekt erkennt "butter" im Kamerabild (HEF-Modell), steuert den EV3 an die Position, fuehrt eine Pick-Sequenz aus und meldet den Fund per Sound.

## Repository-Aufbau

```text
.
├── hailo_butter_ev3_alert.py        # autonome Kernlogik (Detect + EV3-FSM)
├── hailo_robot_web_control.py       # Web-UI + Stream + Robot-Prozessmanager
├── hailo_web_detect_server.py       # einfacher MJPEG-Detektionsserver
├── pi_ev3_rpyc_usb_client.py        # Pi-seitiger USB/RPyC-Verbindungsclient
├── ev3_start_rpyc_server.py         # EV3-seitiger RPyC-Serverstarter
├── start_hailo_butter_alert.sh      # Launcher autonome Fahrt
├── start_hailo_robot_web.sh         # Launcher Unified Web Control
├── start_hailo_webserver.sh         # Alias auf start_hailo_robot_web.sh
├── requirements-pi.txt              # Python-Abhaengigkeiten Raspberry Pi
├── requirements-ev3.txt             # Python-Abhaengigkeiten EV3
└── docs/
    ├── INSTALLATION.md              # Installation und Inbetriebnahme
    ├── ARCHITEKTUR_UND_ABLAUF.md    # Aufbau und Laufzeitablauf
    └── GITHUB_SETUP.md              # GitHub-Remote/SSH/Push
```

## Laufzeitablauf (Kurzfassung)

1. EV3 startet `rpyc_classic` (Port `18812`).
2. Pi richtet USB-Interface ein und verbindet sich per RPyC.
3. Hailo-Detektor liest Frames von Kamera/Stream und findet `butter`.
4. Robot-Statemachine:
   - `SEARCH_RANDOM` (drehen + pausieren + kurze Vorwaertsbewegung)
   - `APPROACH_BUTTER` (zentrieren + anfahren)
   - `PICK_SEQUENCE` (vorfahren, Lift runter, Lift hoch, sprechen)
   - `DONE_STOP`
5. Optional schreibt der Robot Telemetrie nach JSON (z. B. `/tmp/hailo_robot_telemetry.json`), die vom Web-Control-Overlay genutzt wird.

Details inkl. Mermaid-Ablaufdiagramm: `docs/ARCHITEKTUR_UND_ABLAUF.md`.

## Installation

Schnellstart:

1. Vollen Installationsablauf lesen:
   - `docs/INSTALLATION.md` (inkl. Datensatz, Training, ONNX->HEF mit Hailo Developer Suite)
2. Pi/EV3 Dependencies installieren:
   - Pi: `requirements-pi.txt`
   - EV3: `requirements-ev3.txt`
3. EV3 RPyC-Server starten:
   - `python3 ev3_start_rpyc_server.py --host 0.0.0.0 --port 18812`
4. Pi USB/RPyC testen:
   - `sudo python3 pi_ev3_rpyc_usb_client.py --iface auto --oneshot --verbose`
5. Robot starten:
   - Web-Variante: `./start_hailo_webserver.sh`
   - Direkt: `./start_hailo_butter_alert.sh --left-port A --right-port D --lift-port C`

## Validierung (ohne Hardware)

```bash
python3 -m py_compile \
  hailo_butter_ev3_alert.py \
  hailo_web_detect_server.py \
  hailo_robot_web_control.py \
  pi_ev3_rpyc_usb_client.py \
  ev3_start_rpyc_server.py

bash -n start_hailo_butter_alert.sh
bash -n start_hailo_robot_web.sh
bash -n start_hailo_webserver.sh
```

## Sicherheit

- Keine Secrets/SSH-Keys committen.
- `rpyc<6` auf Pi und EV3 erzwingen (Kompatibilitaet zu ev3dev).
- Lift-Stop und Motorgrenzen sind safety-kritisch und nur mit Hardwaretests aendern.

# Butter Robot (Raspberry Pi 5 + Hailo + EV3dev)

Autonomer "Butter-Roboter" mit:
- Raspberry Pi 5 (Ubuntu)
- Hailo AI HAT+ (26 TOPS)
- Raspberry Pi Kamera (libcamera)
- LEGO EV3 (ev3dev) via USB + RPyC

Das Projekt erkennt "butter" im Kamerabild (HEF-Modell), steuert den EV3 an die Position, fuehrt eine Pick-Sequenz aus und meldet den Fund per Sound.

## Schnellzugriff

- Installation komplett (inkl. Datensatz, Training, ONNX -> HEF): [docs/INSTALLATION.md](docs/INSTALLATION.md)
- Robot-Architektur und Ablauf: [docs/ARCHITEKTUR_UND_ABLAUF.md](docs/ARCHITEKTUR_UND_ABLAUF.md)
- Mermaid-Programmablauf (einfach): [Direkt zum Diagramm](docs/ARCHITEKTUR_UND_ABLAUF.md#programmablauf-als-diagramm-einfach)
- GitHub/SSH Setup: [docs/GITHUB_SETUP.md](docs/GITHUB_SETUP.md)

## Wichtige Skripte

- Autonomer Robot-Flow: [hailo_butter_ev3_alert.py](hailo_butter_ev3_alert.py)
- Unified Web Control (Stream + Config + Robot-Prozess): [hailo_robot_web_control.py](hailo_robot_web_control.py)
- Reiner Detektions-Stream: [hailo_web_detect_server.py](hailo_web_detect_server.py)
- Pi USB/RPyC Client: [pi_ev3_rpyc_usb_client.py](pi_ev3_rpyc_usb_client.py)
- EV3 RPyC Server Starter: [ev3_start_rpyc_server.py](ev3_start_rpyc_server.py)
- Web Launcher: [start_hailo_webserver.sh](start_hailo_webserver.sh)
- Robot Launcher direkt: [start_hailo_butter_alert.sh](start_hailo_butter_alert.sh)
- Web Launcher intern: [start_hailo_robot_web.sh](start_hailo_robot_web.sh)

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

Details inkl. Mermaid-Ablaufdiagramm:
- [docs/ARCHITEKTUR_UND_ABLAUF.md](docs/ARCHITEKTUR_UND_ABLAUF.md)
- [Mermaid: Programmablauf als Diagramm (einfach)](docs/ARCHITEKTUR_UND_ABLAUF.md#programmablauf-als-diagramm-einfach)

## Installation

### End-to-End Prozess (wichtig)

1. Datensatz erstellen/labeln (Klasse `butter`)
2. YOLO auf Ubuntu Desktop trainieren
3. Modell nach ONNX exportieren
4. ONNX mit Hailo Developer Suite zu HEF fuer `hailo8` kompilieren
5. HEF auf Pi kopieren (`/home/gast/model.hef`)
6. Pi + EV3 Setup und USB/RPyC testen
7. Robot starten und mit Hardware validieren

Komplette Anleitung:
- [docs/INSTALLATION.md](docs/INSTALLATION.md)
- Hintergrund zur HEF-Erstellung und Versionskompatibilitaet: [HEF_ERSTELLUNG_RPI5_AI_HAT_PLUS_26TOPS.md](HEF_ERSTELLUNG_RPI5_AI_HAT_PLUS_26TOPS.md)

### Dependencies

- Pi Python Requirements: [requirements-pi.txt](requirements-pi.txt)
- EV3 Python Requirements: [requirements-ev3.txt](requirements-ev3.txt)

### Schnellstart Befehle

1. EV3-Server starten:
   - `python3 ev3_start_rpyc_server.py --host 0.0.0.0 --port 18812`
2. Pi USB/RPyC testen:
   - `sudo python3 pi_ev3_rpyc_usb_client.py --iface auto --oneshot --verbose`
3. Robot starten:
   - Web: `./start_hailo_webserver.sh`
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

CI dazu:
- [GitHub Action Sanity Checks](.github/workflows/sanity-checks.yml)

## Weitere Dokumente

- Ursprungsskizze: [butter_robot_sketch.md](butter_robot_sketch.md)
- EV3 USB/RPyC Setup: [EV3_RPYC_USB_SETUP.md](EV3_RPYC_USB_SETUP.md)
- EV3 RPyC Control Library: [EV3_RPYC_CONTROL_LIBRARY.md](EV3_RPYC_CONTROL_LIBRARY.md)
- Alternative EV3/Pi Setup-Notizen: [EV3_raspberry-setup.md](EV3_raspberry-setup.md)
- Aktivitaetsdiagramm Dateien: [butter-aktivitatsdiagramm/](butter-aktivitatsdiagramm/)
- Projektdokumentation: [Projektdokumenation-Butterbot.docx](Projektdokumenation-Butterbot.docx)
- Protokollierung: [Protokullierung.docx](Protokullierung.docx)
- Interview Notiz: [Interview Schreibplan.docx](Interview%20Schreibplan.docx)

## Sicherheit

- Keine Secrets/SSH-Keys committen.
- `rpyc<6` auf Pi und EV3 erzwingen (Kompatibilitaet zu ev3dev).
- Lift-Stop und Motorgrenzen sind safety-kritisch und nur mit Hardwaretests aendern.

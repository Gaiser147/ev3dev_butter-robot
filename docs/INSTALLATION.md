# Installation (Raspberry Pi 5 + EV3dev)

Diese Anleitung bildet den aktuellen Code-Stand ab.

## 1) Voraussetzungen

### Hardware
- Raspberry Pi 5 (Ubuntu)
- Hailo AI HAT+ (26 TOPS)
- Kamera (libcamera-faehig)
- LEGO EV3 mit ev3dev
- USB-Kabel Pi <-> EV3 (datenfaehig)

### Wichtig fuer den aktuellen Code
- HEF-Modell liegt standardmaessig unter `/home/gast/model.hef`.
- Einige Startskripte nutzen absolute Pfade nach `/home/gast/...`.

## 2) Raspberry Pi vorbereiten

Systempakete:

```bash
sudo apt update
sudo apt install -y \
  python3 python3-pip python3-venv \
  python3-opencv python3-numpy \
  iproute2 iputils-ping
```

Python-Pakete (Pi):

```bash
python3 -m pip install --user --break-system-packages -r requirements-pi.txt
```

Hailo Runtime pruefen (`hailo_platform` kommt ueber HailoRT, nicht ueber pip):

```bash
python3 -c "import hailo_platform; print('hailo_platform OK')"
```

Optional: lokales libcamera Prefix setzen (falls local build genutzt wird):

```bash
export LC_PREFIX=/home/gast/.local/libcamera-rpi
```

## 3) EV3 vorbereiten

Auf dem EV3:

```bash
pip3 install -r requirements-ev3.txt
```

USB-IP (Beispiel `usb0`):

```bash
sudo ip link set dev usb0 up
sudo ip -4 addr flush dev usb0
sudo ip -4 addr add 10.42.0.3/24 dev usb0
```

RPyC-Server starten:

```bash
python3 ev3_start_rpyc_server.py --host 0.0.0.0 --port 18812
```

## 4) Pi <-> EV3 Verbindung testen

Auf dem Pi:

```bash
sudo python3 pi_ev3_rpyc_usb_client.py \
  --iface auto \
  --pi-ip 10.42.0.1/24 \
  --ev3-ip 10.42.0.3 \
  --port 18812 \
  --oneshot \
  --verbose
```

## 5) Robot starten

### Option A: Unified Web Control (empfohlen)

```bash
./start_hailo_webserver.sh
```

Danach im Browser: `http://<pi-ip>:8080`

### Option B: Direkt autonom

```bash
./start_hailo_butter_alert.sh --left-port A --right-port D --lift-port C
```

## 6) Sanity Checks

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

## 7) Troubleshooting

- `invalid message type: 18`
  - Auf Pi und EV3 `rpyc<6` verwenden.
- `hailo_platform` Importfehler
  - HailoRT Runtime/Bindings auf dem Pi fehlen oder sind nicht im Python-Pfad.
- Kamera liefert keine Frames
  - Quelle wechseln (`--source`), libcamera/GStreamer Setup pruefen.
- Keine EV3-Verbindung
  - USB-Kabel, IP-Setup (`10.42.0.x`), laufenden EV3-Server pruefen.

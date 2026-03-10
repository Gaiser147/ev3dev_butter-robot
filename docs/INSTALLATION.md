# Installation (Raspberry Pi 5 + EV3dev)

Diese Anleitung bildet den aktuellen Code-Stand ab.

## 0) Gesamtprozess (von Daten bis Robot)

1. Datensatz erstellen (z. B. Roboflow, Klasse `butter`).
2. YOLO-Modell auf Ubuntu Desktop trainieren.
3. Modell nach ONNX exportieren.
4. ONNX mit Hailo Developer Suite zu HEF fuer `hailo8` kompilieren.
5. HEF auf den Raspberry Pi kopieren (`/home/gast/model.hef`).
6. Pi + EV3 installieren, USB/RPyC Verbindung testen.
7. Robot-Programm starten und mit echter Hardware pruefen.

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

## 2) Modell-Training auf Ubuntu Desktop (Beispielablauf)

### 2.1 Datensatz aufbauen

- Bilder vom Zielobjekt sammeln (Butter in realer Umgebung).
- Bounding Boxes labeln (nur Klasse `butter`).
- Train/Valid/Test Split erstellen.
- Dataset im YOLO-Format exportieren.

### 2.2 YOLO trainieren (Desktop)

Beispiel mit Ultralytics:

```bash
python3 -m pip install --upgrade ultralytics

yolo task=detect mode=train \
  model=yolo11n.pt \
  data=/path/to/data.yaml \
  epochs=100 \
  imgsz=640 \
  device=0
```

### 2.3 ONNX exportieren

```bash
yolo task=detect mode=export \
  model=/path/to/best.pt \
  format=onnx \
  imgsz=640
```

Ergebnis: `best.onnx`

## 3) ONNX -> HEF mit Hailo Developer Suite (Ubuntu Desktop)

Wichtig:
- Ziel fuer Raspberry Pi AI HAT+ 26 TOPS ist `hailo8`.
- Toolchain-Version muss zur Runtime auf dem Pi passen.

Beispiel mit Hailo Model Zoo:

```bash
# Symbolische Beispielbefehle; Modellname/YAML an eigenes Projekt anpassen
hailomz parse <MODEL_NAME> --hw-arch hailo8
hailomz optimize <MODEL_NAME> --hw-arch hailo8 --calib-path /path/to/calib_images
hailomz compile <MODEL_NAME> --hw-arch hailo8
```

Wenn du ein eigenes ONNX + eigenes Config-Setup nutzt:
- Parse -> Optimize (mit Kalibrierung) -> Compile
- Zielarchitektur immer `hailo8`
- Output muss eine `.hef` fuer Hailo-8 sein

Details und Hintergruende: `HEF_ERSTELLUNG_RPI5_AI_HAT_PLUS_26TOPS.md`

## 4) HEF auf Raspberry Pi deployen

Beispiel:

```bash
scp /path/to/model.hef <pi-user>@<pi-ip>:/home/gast/model.hef
```

Auf dem Pi pruefen:

```bash
hailortcli fw-control identify
hailortcli parse-hef /home/gast/model.hef
```

## 5) Raspberry Pi vorbereiten

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

## 6) EV3 vorbereiten

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

## 7) Pi <-> EV3 Verbindung testen

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

## 8) Robot starten

### Option A: Unified Web Control (empfohlen)

```bash
./start_hailo_webserver.sh
```

Danach im Browser: `http://<pi-ip>:8080`

### Option B: Direkt autonom

```bash
./start_hailo_butter_alert.sh --left-port A --right-port D --lift-port C
```

## 9) Sanity Checks

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

## 10) Troubleshooting

- `invalid message type: 18`
  - Auf Pi und EV3 `rpyc<6` verwenden.
- `hailo_platform` Importfehler
  - HailoRT Runtime/Bindings auf dem Pi fehlen oder sind nicht im Python-Pfad.
- Kamera liefert keine Frames
  - Quelle wechseln (`--source`), libcamera/GStreamer Setup pruefen.
- Keine EV3-Verbindung
  - USB-Kabel, IP-Setup (`10.42.0.x`), laufenden EV3-Server pruefen.

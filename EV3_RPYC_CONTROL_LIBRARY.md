# EV3dev per RPyC steuern (Raspberry Pi -> EV3)

Diese Doku beschreibt, wie du den EV3 über den Raspberry Pi mit RPyC steuerst.

## 1. Architektur

1. Auf dem EV3 läuft ein `rpyc_classic`-Server (z. B. über `ev3_start_rpyc_server.py`).
2. Der Raspberry stellt die USB-IP-Verbindung her (über `pi_ev3_rpyc_usb_client.py`).
3. Ein Python-Programm auf dem Raspberry verbindet sich per RPyC zum EV3 und führt dort `ev3dev2`-Code aus.

Wichtig: Auf **Pi und EV3** `rpyc<6` verwenden.

## 2. Voraussetzungen

### EV3

```bash
pip3 install "rpyc<6" python-ev3dev2
python3 ev3_start_rpyc_server.py --host 0.0.0.0 --port 18812
```

### Raspberry Pi

```bash
python3 -m pip install --user --break-system-packages "rpyc<6"
sudo python3 /home/gast/pi_ev3_rpyc_usb_client.py --iface auto --pi-ip 10.42.0.1/24 --ev3-ip 10.42.0.3 --port 18812 --oneshot --verbose
```

## 3. Basis-Client (Library-Pattern)

Speichere z. B. als `ev3_control_client.py`:

```python
#!/usr/bin/env python3
import rpyc


class EV3Remote:
    def __init__(self, host="10.42.0.3", port=18812, timeout=10):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.conn = None

    def connect(self):
        self.conn = rpyc.classic.connect(self.host, port=self.port, keepalive=True)
        self.conn._config["sync_request_timeout"] = self.timeout
        # Health check
        _ = self.conn.modules["sys"].version
        return self

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    @property
    def modules(self):
        if self.conn is None:
            raise RuntimeError("Nicht verbunden")
        return self.conn.modules


def demo():
    ev3 = EV3Remote().connect()
    try:
        # Remote imports auf dem EV3:
        Sound = ev3.modules["ev3dev2.sound"].Sound
        sound = Sound()
        sound.speak("Connection successful")
    finally:
        ev3.close()


if __name__ == "__main__":
    demo()
```

Start:

```bash
python3 ev3_control_client.py
```

## 4. Motoren steuern

### Direkte Motorsteuerung

```python
LargeMotor = ev3.modules["ev3dev2.motor"].LargeMotor
OUTPUT_A = ev3.modules["ev3dev2.motor"].OUTPUT_A
OUTPUT_B = ev3.modules["ev3dev2.motor"].OUTPUT_B

left = LargeMotor(OUTPUT_A)
right = LargeMotor(OUTPUT_B)

left.on_for_seconds(speed=30, seconds=1.5, brake=True, block=True)
right.on_for_seconds(speed=30, seconds=1.5, brake=True, block=True)
```

### Tank-Drive

```python
MoveTank = ev3.modules["ev3dev2.motor"].MoveTank
OUTPUT_A = ev3.modules["ev3dev2.motor"].OUTPUT_A
OUTPUT_B = ev3.modules["ev3dev2.motor"].OUTPUT_B

tank = MoveTank(OUTPUT_A, OUTPUT_B)
tank.on_for_seconds(left_speed=25, right_speed=25, seconds=2, brake=True, block=True)  # vorwaerts
tank.on_for_seconds(left_speed=20, right_speed=-20, seconds=0.8, brake=True, block=True)  # drehen
tank.off(brake=True)
```

## 5. Sensoren lesen

### Touch-Sensor

```python
TouchSensor = ev3.modules["ev3dev2.sensor.lego"].TouchSensor
touch = TouchSensor()
print("pressed =", touch.is_pressed)
```

### Farbe

```python
ColorSensor = ev3.modules["ev3dev2.sensor.lego"].ColorSensor
cs = ColorSensor()
print("reflected =", cs.reflected_light_intensity)
print("color =", cs.color_name)
```

### Distanz (Ultrasonic)

```python
UltrasonicSensor = ev3.modules["ev3dev2.sensor.lego"].UltrasonicSensor
us = UltrasonicSensor()
print("distance_cm =", us.distance_centimeters)
```

## 6. Sound/Display

```python
Sound = ev3.modules["ev3dev2.sound"].Sound
sound = Sound()
sound.beep()
sound.speak("Hello from Raspberry")
```

## 7. Robustes Steuerprogramm (Reconnect)

Wenn du ein dauerhaft laufendes Programm willst:

1. Bei Fehlern `except Exception` abfangen.
2. Verbindung schließen.
3. Nach kurzer Wartezeit neu verbinden.
4. Motoren im Fehlerfall immer stoppen (`off(brake=True)`), falls Objekt noch erreichbar ist.

Minimalbeispiel:

```python
import time
import rpyc

while True:
    conn = None
    try:
        conn = rpyc.classic.connect("10.42.0.3", port=18812, keepalive=True)
        conn._config["sync_request_timeout"] = 10
        MoveTank = conn.modules["ev3dev2.motor"].MoveTank
        m = conn.modules["ev3dev2.motor"]
        tank = MoveTank(m.OUTPUT_A, m.OUTPUT_B)
        tank.on_for_seconds(20, 20, 1, brake=True, block=True)
        conn.ping()
    except Exception as e:
        print("Reconnect wegen Fehler:", e)
        time.sleep(2)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
```

## 8. Betrieb in verschiedenen Netzwerken (LAN/WLAN)

Deine EV3-USB-Verbindung bleibt stabil, solange:

1. EV3-USB-Netz im eigenen Subnetz bleibt (`10.42.0.0/24`).
2. Kein anderes Interface dasselbe Subnetz nutzt.
3. USB-Kabel/Port stabil sind.

`eth0`/`wlan0` koennen parallel aktiv sein. Der EV3-Verkehr geht trotzdem ueber das USB-Interface.

## 9. Troubleshooting

### `invalid message type: 18`

Version-Mismatch. Loesung:

```bash
python3 -m pip install --user --break-system-packages "rpyc<6"
```

Auf EV3 ebenfalls `rpyc<6` setzen.

### `TCP 10.42.0.3:18812 nicht erreichbar`

1. EV3-Server laeuft nicht.
2. EV3-IP nicht gesetzt.
3. USB-Interface nicht oben.

### Ping geht, aber Motor bewegt sich nicht

1. Falsche Ports (`OUTPUT_A/B/C/D`).
2. Motor nicht erkannt (Kabel pruefen).
3. Sensor/Motor-Belegung auf EV3 pruefen (`/sys/class/tacho-motor`).

## 10. Sicherheitshinweis

`rpyc_classic` erlaubt Remote-Python-Ausfuehrung auf dem EV3. Das nur in vertrauenswuerdigen Netzsegmenten nutzen.

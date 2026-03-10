# EV3dev <-> Raspberry Pi per USB + RPyC (manuell, robust)

## 1) EV3 vorbereiten

Auf dem EV3dev:

0. Abhängigkeiten:

```bash
pip3 install "rpyc<6" python-ev3dev2
```

Hinweis: Für ev3dev ist `rpyc<6` wichtig. Ein Mix aus Pi `rpyc 6.x` und EV3 `4.x/5.x`
führt typischerweise zu `invalid message type: 18`.

1. USB-Netzwerk aktivieren (RNDIS/CDC Gadget).
2. Feste EV3-IP auf dem USB-Interface setzen: `10.42.0.3/24`.
   Beispiel (falls Interface `usb0` heißt):

```bash
sudo ip link set dev usb0 up
sudo ip -4 addr flush dev usb0
sudo ip -4 addr add 10.42.0.3/24 dev usb0
```

3. Skript auf EV3 kopieren: `ev3_start_rpyc_server.py`.
4. Starten:

```bash
python3 ev3_start_rpyc_server.py --host 0.0.0.0 --port 18812
```

Beim Start wird ein Sound abgespielt (ev3dev2 Sound API).

## 2) Raspberry Pi verbinden

Skript auf dem Pi:

```bash
python3 -m pip install --user --break-system-packages "rpyc<6"
```

Dann verbinden:

```bash
sudo python3 pi_ev3_rpyc_usb_client.py --iface auto --pi-ip 10.42.0.1/24 --ev3-ip 10.42.0.3 --port 18812
```

Der Client:

1. erkennt das USB-Interface,
2. setzt die Pi-IP,
3. verbindet sich per RPyC,
4. hält die Verbindung mit Healthcheck + Reconnect.

## 3) Nützliche Varianten

Einmaliger Testlauf:

```bash
sudo python3 pi_ev3_rpyc_usb_client.py --oneshot
```

Mehr Logs:

```bash
sudo python3 pi_ev3_rpyc_usb_client.py --verbose
```

## 4) Stabilitäts-Checkliste

1. **Datenfähiges USB-Kabel** (kein reines Ladekabel).
2. EV3-USB-Link muss als Netzwerkinterface erscheinen.
3. EV3 und Pi müssen im gleichen /24 sein (`10.42.0.x`).
4. EV3-Server muss auf `0.0.0.0:18812` laufen.
5. Bei Kernelfehlern wie `error -71`: Kabel/Port wechseln, ggf. powered USB-Hub testen.

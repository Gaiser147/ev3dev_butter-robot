# EV3dev an Raspberry Pi 5 (Ubuntu) per USB + RPyC anbinden

Diese Anleitung richtet einen Raspberry Pi 5 (Ubuntu) so ein, dass ein EV3dev per USB angeschlossen und ueber einen RPyC-Server genutzt werden kann.

## Zielbild

- EV3 per USB am Raspberry Pi
- USB-Netzwerk zwischen beiden Geraeten
- SSH-Zugriff vom Pi auf den EV3
- RPyC-Client auf dem Pi verbindet sich zum RPyC-Server auf dem EV3

## 1. Voraussetzungen

Auf dem Raspberry Pi (Ubuntu):

```bash
sudo apt update
sudo apt install -y network-manager openssh-client python3-venv netcat-openbsd
sudo systemctl enable --now NetworkManager
```

Hinweis: Falls du Ubuntu Desktop nutzt, ist `NetworkManager` meist bereits aktiv.

## 2. Verbindungs-Skript auf dem Raspberry Pi anlegen

Datei `~/.local/bin/ev3-connect` erstellen:

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/ev3-connect <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

CON_NAME="EV3-USB"
LOCAL_IP="192.168.2.2/24"
HOST_IP="192.168.2.3"
WAIT_SECS=20

AUTO_SSH=1
if [[ "${1:-}" == "--no-ssh" ]]; then
  AUTO_SSH=0
fi

find_usb_iface() {
  local path
  for iface_path in /sys/class/net/*; do
    local iface
    iface="$(basename "$iface_path")"
    [[ "$iface" == "lo" ]] && continue
    if udevadm info -q property -p "$iface_path" 2>/dev/null | grep -Eq '^ID_VENDOR_ID=0694$|^ID_MODEL=.*EV3'; then
      echo "$iface"
      return 0
    fi
    path="$(readlink -f "$iface_path")"
    if [[ "$path" == *"/usb"* ]]; then
      echo "$iface"
      return 0
    fi
  done
  return 1
}

find_ev3_ipv6() {
  local local_ll candidate=""
  ping -6 -c 1 -W 1 -I "$IFACE" ff02::1 >/dev/null 2>&1 || true
  local_ll="$(ip -6 -o addr show dev "$IFACE" scope link | awk '{print $4}' | cut -d/ -f1 | head -n1)"
  while read -r addr _ _ state; do
    [[ "$addr" =~ ^fe80:: ]] || continue
    [[ "$addr" == "$local_ll" ]] && continue
    [[ "$state" == "FAILED" || "$state" == "INCOMPLETE" ]] && continue
    if [[ "$state" == "REACHABLE" || "$state" == "STALE" || "$state" == "DELAY" || "$state" == "PROBE" ]]; then
      echo "$addr"
      return 0
    fi
    [[ -z "$candidate" ]] && candidate="$addr"
  done < <(ip -6 neigh show dev "$IFACE")
  [[ -n "$candidate" ]] && echo "$candidate"
}

print_cmd() {
  printf '%q ' "$@"
  echo
}

IFACE=""
for _ in $(seq 1 "$WAIT_SECS"); do
  IFACE="$(find_usb_iface || true)"
  [[ -n "$IFACE" ]] && break
  sleep 1
done

if [[ -z "$IFACE" ]]; then
  echo "Kein EV3-USB-Netzwerkinterface gefunden (nach ${WAIT_SECS}s)." >&2
  exit 1
fi

if ! nmcli -t -f NAME connection show | grep -Fxq "$CON_NAME"; then
  nmcli connection add type ethernet ifname "$IFACE" con-name "$CON_NAME" \
    ipv4.method manual ipv4.addresses "$LOCAL_IP" \
    ipv6.method ignore autoconnect no >/dev/null
fi

nmcli connection modify "$CON_NAME" connection.interface-name "$IFACE" \
  ipv4.method manual ipv4.addresses "$LOCAL_IP" ipv4.gateway "" >/dev/null

nmcli connection down "$CON_NAME" >/dev/null 2>&1 || true
for _ in {1..5}; do
  if nmcli connection up "$CON_NAME" ifname "$IFACE" >/dev/null 2>&1; then
    break
  fi
  sleep 1
  IFACE="$(find_usb_iface || true)"
done

echo "EV3-USB aktiv auf Interface $IFACE ($LOCAL_IP)."
echo "Warte auf EV3 unter $HOST_IP ..."

IPV4_OK=0
for _ in {1..10}; do
  if ping -c 1 -W 1 "$HOST_IP" >/dev/null 2>&1; then
    IPV4_OK=1
    break
  fi
  sleep 1
done

if [[ "$IPV4_OK" -eq 1 ]]; then
  SSH_CMD=(ssh ev3)
else
  EV3_IPV6="$(find_ev3_ipv6 || true)"
  if [[ -n "$EV3_IPV6" ]]; then
    SSH_CMD=(ssh -6 -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new \
      -o IdentitiesOnly=yes -o PubkeyAuthentication=no \
      -o PreferredAuthentications=password,keyboard-interactive \
      -l robot "${EV3_IPV6}%${IFACE}")
  else
    echo "EV3 antwortet weder auf IPv4 ($HOST_IP) noch auf IPv6-Link-Local." >&2
    exit 1
  fi
fi

if [[ "$AUTO_SSH" -eq 0 ]]; then
  echo -n "SSH starten mit: "
  print_cmd "${SSH_CMD[@]}"
  exit 0
fi

exec "${SSH_CMD[@]}"
EOF

chmod +x ~/.local/bin/ev3-connect
```

## 3. SSH-Host `ev3` konfigurieren

Datei `~/.ssh/config`:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
cat > ~/.ssh/config <<'EOF'
Host ev3
    HostName 192.168.2.3
    User robot
    Port 22
    ConnectTimeout 8
    ServerAliveInterval 30
    ServerAliveCountMax 2
    IdentitiesOnly yes
    PubkeyAuthentication no
    PreferredAuthentications password,keyboard-interactive
    StrictHostKeyChecking accept-new
EOF
chmod 600 ~/.ssh/config
```

## 4. RPyC-Client auf dem Raspberry Pi installieren

Wichtig: Mit EV3dev ist `rpyc==4.1.5` meist kompatibel. `6.x` fuehrt oft zu Protokollfehlern (`invalid message type`).

```bash
python3 -m venv ~/.venvs/ev3-rpyc4
~/.venvs/ev3-rpyc4/bin/python3 -m ensurepip --upgrade
~/.venvs/ev3-rpyc4/bin/python3 -m pip install --upgrade pip "rpyc==4.1.5"
```

## 5. RPyC-Server auf dem EV3 starten

Per SSH auf den EV3:

```bash
ev3-connect
```

Dann auf dem EV3 z. B.:

```python
#!/usr/bin/env python3
from rpyc import SlaveService
from rpyc.utils.server import ThreadedServer

print("Starting rpyc SlaveService server on port 18812...")
server = ThreadedServer(SlaveService, port=18812, hostname="0.0.0.0")
server.start()
```

Starten:

```bash
python3 /pfad/zu/deinem_rpyc_server.py
```

## 6. Verbindung vom Raspberry Pi testen

```bash
~/.venvs/ev3-rpyc4/bin/python3 - <<'PY'
import rpyc
conn = rpyc.classic.connect("192.168.2.3", 18812, keepalive=True)
print("Verbunden:", conn.modules.os.uname())
conn.close()
PY
```

## 7. Typische Fehler und Loesungen

- `No route to host`:
  - `ev3-connect --no-ssh` erneut ausfuehren
  - pruefen: `ip -4 -br addr` (EV3-USB sollte `192.168.2.2/24` haben)
- `Connection refused` auf `18812`:
  - RPyC-Server auf dem EV3 laeuft nicht
- `invalid message type`:
  - auf dem Pi wahrscheinlich falsche RPyC-Version, auf `4.1.5` wechseln
- `Permission denied` bei SSH:
  - Benutzer/Passwort auf EV3 pruefen (`robot` / haeufig `maker`, falls nicht geaendert)

## 8. Schnellablauf

1. EV3 per USB an den Pi anschliessen.
2. Auf dem Pi: `ev3-connect --no-ssh`
3. Auf dem Pi: `ssh ev3`
4. Auf dem EV3: RPyC-Server starten.
5. Auf dem Pi: Test mit `rpyc==4.1.5`.


# GitHub Setup (SSH + Push)

Diese Datei ist kurz gehalten: zuerst der Schnellweg, darunter Details.

## Schnellweg

```bash
git branch -M main
git remote set-url origin git@github.com:<USER>/<REPO>.git
git add .
git commit -m "docs: update repository documentation"
git push -u origin main
```

Falls SSH noch nicht eingerichtet ist: Abschnitt "SSH-Key Setup" unten aufklappen.

<details>
<summary><strong>SSH-Key Setup (ausklappen)</strong></summary>

## 1) SSH-Key pruefen/erstellen

```bash
ls -la ~/.ssh
ssh-keygen -t ed25519 -C "<deine-email>"
cat ~/.ssh/id_ed25519.pub
```

Public Key in GitHub hinterlegen:
- GitHub -> Settings -> SSH and GPG keys -> New SSH key

Verbindung testen:

```bash
ssh -T git@github.com
```

</details>

<details>
<summary><strong>Sauberer Erst-Commit (ausklappen)</strong></summary>

## 2) Branch und Commit

```bash
git branch -M main

git add \
  .gitignore \
  README.md \
  docs/INSTALLATION.md \
  docs/ARCHITEKTUR_UND_ABLAUF.md \
  docs/GITHUB_SETUP.md \
  requirements-pi.txt \
  requirements-ev3.txt \
  .github/workflows/sanity-checks.yml \
  hailo_butter_ev3_alert.py \
  hailo_robot_web_control.py \
  hailo_web_detect_server.py \
  pi_ev3_rpyc_usb_client.py \
  ev3_start_rpyc_server.py \
  start_hailo_butter_alert.sh \
  start_hailo_robot_web.sh \
  start_hailo_webserver.sh \
  butter_robot_sketch.md \
  EV3_RPYC_USB_SETUP.md \
  EV3_RPYC_CONTROL_LIBRARY.md \
  EV3_raspberry-setup.md \
  HEF_ERSTELLUNG_RPI5_AI_HAT_PLUS_26TOPS.md

git commit -m "docs: initialize butter robot repository"
git remote add origin git@github.com:<USER>/<REPO>.git
git push -u origin main
```

Wenn `origin` schon existiert:

```bash
git remote set-url origin git@github.com:<USER>/<REPO>.git
git push -u origin main
```

</details>

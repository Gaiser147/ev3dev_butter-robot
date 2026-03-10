# HEF-Erstellung fuer Raspberry Pi 5 + AI HAT+ (26 TOPS, Hailo-8)

## Ziel dieses Dokuments
Dieses Dokument beschreibt, wie eine `.hef`-Datei gebaut werden muss, damit sie auf einem **Raspberry Pi 5** mit **Raspberry Pi AI HAT+ 26 TOPS** (Hailo-8) lauffaehig ist.

Es deckt ab:
- Hardware-/Software-Anforderungen
- Versionskompatibilitaet (wichtigster Punkt)
- Empfohlener Build-Workflow fuer eigene Modelle
- Deployment- und Verifikation auf dem Raspberry Pi
- Fehlerbilder und schnelle Diagnose

## 1) Hardware-Basis (fuer 26 TOPS)

- Board: Raspberry Pi 5
- Accelerator: Raspberry Pi AI HAT+ **26 TOPS**
- NPU-Chip: **Hailo-8**
- Runtime-Architektur auf dem Geraet: **HAILO8**
- PCIe-Anbindung: auf Pi 5 ueber PCIe (Gen3 empfohlen/ueblich)

Wichtig:
- Die 13 TOPS Variante nutzt Hailo-8L.
- Die 26 TOPS Variante nutzt Hailo-8.
- Eine HEF muss zur Zielarchitektur passen. Fuer dieses Setup also auf **hailo8 / HAILO8** bauen.

## 2) Kritischer Punkt: Versionskompatibilitaet

Eine `.hef` ist **nicht versionsneutral**. Sie muss zur installierten Laufzeit-Toolchain passen.

Wenn Versionen nicht zusammenpassen, sieht man typischerweise:
- `HEF version does not match`
- `HAILO_INVALID_HEF(26)`

Das bedeutet meistens:
- HEF wurde mit anderer Hailo-Toolchain-Generation kompiliert als die Runtime auf dem Pi.

## 3) Konkreter Ist-Zustand dieses Raspberry Pi (gemessen)

- OS: Ubuntu 24.04.4 LTS
- Kernel: `6.8.0-1047-raspi`
- Modell: `Raspberry Pi 5 Model B Rev 1.1`
- PCIe Device: `Hailo-8 AI Processor [1e60:2864]`
- Treiber: `hailo_pci` geladen
- Treiber-Version: `4.17.0`
- HailoRT CLI: `4.17.0`
- Firmware auf Device: `4.17.0`
- Device-Arch: `HAILO8`
- PCIe Link: `8.0 GT/s` (Gen3), Width aktuell `x1`

Installierte relevante Pakete:
- `hailort 4.17.0`
- `hailo-tappas-core-3.28.2`

Aktueller Fehler bei `/home/gast/model.hef`:
- `HEF version does not match`
- `HAILO_INVALID_HEF(26)`

## 4) Welche Toolchain fuer Hailo-8 verwenden?

Fuer Hailo-8/Hailo-8L gilt:
- Nicht die Hailo-10/15-only Toolchain-Zweige verwenden.
- Bei Hailo Model Zoo die **v2.x Linie** (mit DFC v3.x) fuer Hailo-8 nutzen.

Praxisregel:
- Runtime auf Pi (z.B. HailoRT 4.17 / 4.18 / 4.19)
- Build-Umgebung (DFC + Model Zoo + ggf. Tappas) aus passender Generation
- Danach HEF erzeugen und auf Pi mit gleicher Runtime betreiben.

## 5) Empfohlener Build-Workflow fuer eigene HEF (Hailo-8)

Hinweis: Das Kompilieren passiert normalerweise auf einem leistungsfaehigeren Linux-Rechner (x86_64 Ubuntu), nicht direkt auf dem Pi.

### 5.1 Build-Umgebung aufsetzen

- Nutze eine konsistente Hailo Software Suite fuer Hailo-8 (kompatible Kombination aus DFC, Model Zoo, HailoRT).
- Fuer Hailo-8 keine Hailo-10/15-only Releases/Zweige verwenden.

### 5.2 Modell fuer Hailo-8 parsen/optimieren/kompilieren

Grundmuster (Model Zoo):

```bash
# Beispiel: parse/optimize/compile mit explizitem Ziel
hailomz parse <MODEL_NAME> --hw-arch hailo8
hailomz optimize <MODEL_NAME> --hw-arch hailo8 --calib-path /path/to/calib_images
hailomz compile <MODEL_NAME> --hw-arch hailo8
```

Wenn du ein eigenes ONNX + eigenes YAML/ALLS nutzt, bleibt die Kernregel gleich:
- Zielarchitektur immer `hailo8`
- Kalibrierung sauber und passend
- DFC/Model-Zoo-Version passend zur Runtime-Generation

### 5.3 Ergebnisartefakt

- Output ist eine `.hef`
- Diese `.hef` auf den Pi kopieren
- Auf dem Pi zuerst per `parse-hef` pruefen

## 6) Deployment auf Raspberry Pi 5

### 6.1 Runtime-Version auf dem Pi festlegen (Beispiel 4.17)

Fuer AI Kit / AI HAT+ nennt die Raspberry Pi Doku explizite, zueinander passende Paketsets.

Beispiel fuer 4.17:

```bash
sudo apt install hailo-tappas-core=3.28.2 hailort=4.17.0 hailo-dkms=4.17.0-1
sudo apt-mark hold hailo-tappas-core hailort hailo-dkms
```

(Alternativ gibt es in der Doku auch Sets fuer 4.18 und 4.19.)

### 6.2 Verifikation

```bash
hailortcli fw-control identify
hailortcli parse-hef /pfad/zur/model.hef
hailortcli run /pfad/zur/model.hef --batch-size 1
```

Erwartung:
- `parse-hef` darf keinen Versionsfehler werfen.
- `run` darf nicht mit `HAILO_INVALID_HEF(26)` abbrechen.

## 7) Schnelle Fehlerdiagnose

### Fehler: `HEF version does not match`
Ursache:
- HEF mit unpassender Toolchain-Version gebaut.

Fix:
- Entweder Runtime auf Pi auf passende Version bringen,
- oder HEF mit zur Pi-Runtime passender Toolchain neu bauen.

### Fehler: Modell kompiliert, laeuft aber nicht auf 26 TOPS HAT+
Pruefen:
- Wirklich fuer `hailo8` kompiliert?
- Keine Hailo-10/15-only Toolchain verwendet?
- Runtime-/Treiber-/FW-Versionen konsistent?

### Fehler: Device wird nicht erkannt
Pruefen:
- `lspci -nn | grep -i hailo`
- `lsmod | grep hailo_pci`
- `ls -l /dev/hailo0`

## 8) Kompatibilitaets-Checkliste vor jedem Deployment

- [ ] Zielhardware bestaetigt: AI HAT+ 26 TOPS = Hailo-8
- [ ] HEF fuer `hailo8` gebaut
- [ ] Build-Toolchain nicht aus Hailo-10/15-only Zweig
- [ ] Pi Runtime-Stack Versionen konsistent (hailort, hailo-dkms, tappas-core)
- [ ] `hailortcli parse-hef` auf Pi erfolgreich
- [ ] Erst dann Anwendung / Pipeline starten

## 9) Referenzen (offiziell)

- Raspberry Pi AI software (Install, Verify, Versionshinweise):
  - https://www.raspberrypi.com/documentation/computers/ai.html
- Raspberry Pi AI HATs (13/26 TOPS Varianten, Chip-Zuordnung):
  - https://www.raspberrypi.com/documentation/accessories/ai-hat-plus.html
- Hailo Model Zoo (Kompatibilitaetshinweis Hailo-8/Hailo-8L vs. neuere Linien):
  - https://github.com/hailo-ai/hailo_model_zoo
- HailoRT README (Hinweis zu Hailo-8 vs. master/hailo8 branch):
  - https://github.com/hailo-ai/hailort

---

Wenn du willst, kann ich im naechsten Schritt eine zweite Datei mit einem **konkreten, reproduzierbaren Build-Rezept** fuer dein Modell erstellen (inkl. Ordnerstruktur, Befehle fuer ONNX->HAR->HEF, und einem abschliessenden Pi-Kompatibilitaetstest als Shell-Skript).

# Spindel-Steuerung — HSD Hochfrequenzspindel via NI cDAQ-9178

Python-basierte Steuerung einer HSD Hochfrequenzspindel über eine NI cDAQ-9178 DAQ-Box mit Browser-Interface.

## Versionshistorie

- **v2** (2026-09-07): Drehzahl-Formel fuer 4-Pol-Motor korrigiert (RPM_MAX 24000 → 12000), modulares Sensor-Filtersystem (raw/mean/median/rms/min/max/lowpass), Timer-Race-Fix per Generation-Counter, Motor-Polzahl im Experten-Tab konfigurierbar, Version-Anzeige im UI (v2 als Badge), Rohdaten + Gefiltert in CSV. Alte .bat/Shortcut zum Ordner entfernt — Startpfad einheitlich ueber `Spindel.exe`.
- **v1**: RD6006 + DPM8624 Netzteile, 3 Pyrometer, 2 Ventile, EXE-Verteilung

**Hinweis zu alten CSV-Exporten:** Vor v2 exportierte CSVs enthalten falsche Frequenz-Werte (mit `f = RPM/60` statt `f = RPM * P/120` fuer 4-Pol). Bei Bedarf manuell umrechnen: `f_korrekt = f_alt × 2` fuer 4-Pol-Motor.

---

## Hardware

| Komponente | Details |
|---|---|
| Spindel | HSD Hochfrequenzspindel, 4-Pol-Motor, max. 12.000 U/min bei 400 Hz |
| DAQ-Chassis | NI cDAQ-9178 |
| Analogausgang | NI 9263 (Slot 4) — Drehzahlsollwert 0–10 V |
| Digitalausgang | NI 9472 (Slot 1) — Enable-Signal 24 V |
| Frequenzumrichter | Omron 3G3MX2 |

### Pinbelegung

**NI 9263 → Omron 3G3MX2 (Drehzahl):**
| NI 9263 | Omron MX2 | Funktion |
|---|---|---|
| `AO 1` | `O` | Analoger Sollwert 0–10 V |
| `COM` | `L` | Analoge Masse |

**NI 9472 → Omron 3G3MX2 (Enable):**
| NI 9472 | Omron MX2 | Funktion |
|---|---|---|
| `DO 1` | `1` (Vorwärts) | Enable-Signal |
| `COM` | `L` | Logik-Masse |
| `V+` | `P24` | 24 V Versorgung vom Umrichter |

---

## Software-Setup

### Voraussetzungen
- Python 3.x
- NI-DAQmx Runtime (von ni.com)

### Installation

```bash
pip install -r requirements.txt
```

### Starten

**Per Doppelklick:** `Spindel Starten.bat`

**Per Terminal:**
```bash
python spindel_web.py
```

Browser öffnet automatisch auf `http://127.0.0.1:5000`.
Mehrfaches Klicken auf das Bat-File öffnet nur den Browser neu — kein zweiter Server.

### Desktop-Shortcut für alle Nutzer installieren

`Setup - Shortcut installieren.bat` als **Administrator** ausführen.  
Kopiert den Shortcut nach `C:\Users\Public\Desktop` → erscheint bei allen Windows-Nutzern.

---

## Projektstruktur

```
Spindel/
├── spindel_web.py               # Flask Web-Server + API
├── spindle_controller.py        # DAQmx Hardware-Steuerung
├── templates/
│   └── index.html               # Browser-Interface
├── messtest.py                  # Analogausgang testen (8.37 V Beispiel)
├── messtest_do.py               # Digitalausgang testen
├── requirements.txt
├── Spindel Starten.bat          # Desktop-Shortcut
└── Setup - Shortcut installieren.bat
```

---

## Browser-Interface

Aufrufbar unter:
- Lokal: `http://127.0.0.1:5000`
- LAN: `http://<IP-Adresse>:5000`

### Einsteiger-Modus
- Drehzahl per Schieberegler oder Eingabefeld setzen
- Optionale Laufzeitbegrenzung in Minuten
- START / STOP

### Experten-Modus
- Direkte Spannungseingabe (0–10 V)
- AO/DO-Kanäle konfigurierbar
- RPM- und Spannungsgrenzen anpassbar
- Manuelle Enable/Disable-Kontrolle
- Kanäle neu initialisieren

### Live-Anzeige
- Soll- und Ist-Drehzahl (U/min)
- Spannungsausgang (V)
- Frequenz (Hz)
- Laufzeit / Restzeit
- Enable-Signal Status

### CSV-Export
Sessiondaten als `.csv` exportierbar über den Button in der Fußzeile.  
Spalten: `Zeitstempel, Soll_RPM, Ist_RPM, Spannung_V, Frequenz_Hz, Aktiv`

---

## DAQmx-Kanäle (bestätigt aus LabVIEW-Blockdiagramm)

```python
AO_CHANNEL = "cDAQ1Mod4/ao1"          # Drehzahl-Sollwert
DO_CHANNEL = "cDAQ1Mod1/port0/line1"  # Enable-Signal
VOLTAGE_MIN = 0.0   # V
VOLTAGE_MAX = 10.0  # V
RPM_MAX     = 24000 # U/min
```

---

## Simulation-Modus

Wenn keine Hardware angeschlossen ist, startet die Software automatisch im Simulation-Modus. Das Interface ist vollständig bedienbar, es werden aber keine realen Signale ausgegeben. Ein gelbes **SIMULATION**-Badge erscheint im Header.

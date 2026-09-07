"""
Hochfrequenzspindel-Steuerung via NI cDAQ-9178
================================================
Entspricht dem LabVIEW-Programm:
  Drehzahl_S1.vi  → AO-Kanal (Spannungsausgang = Drehzahl)
  Spindelstart.vi → DO-Kanal (Enable-Signal)

Physische Pins (direkt aus LabVIEW-Blockdiagramm bestätigt):
  NI 9263 Slot 4: Klemme AO 1 → Analogeingang Frequenzumrichter (+)
                  Klemme COM  → Analogeingang Frequenzumrichter (−)
  NI 9472 Slot 1: Klemme DO 1 → Enable-Eingang Spindelantrieb
                  Klemme COM  → GND Spindelantrieb
                  Klemme V+   → Externe 24V-Versorgung (erforderlich!)
"""

import sys
import nidaqmx
import nidaqmx.system
from nidaqmx.constants import LineGrouping

# ─── KONFIGURATION ────────────────────────────────────────────────────────────
# Bestätigt aus LabVIEW-Blockdiagramm-Bildern (Drehzahl__S1d.png, Spindelstartd.png)
AO_CHANNEL  = "cDAQ1Mod4/ao1"          # NI 9263, Slot 4, Kanal AO 1
DO_CHANNEL     = "cDAQ1Mod1/port0/line1"  # NI 9472, Slot 1, DO 1
VALVE1_CHANNEL = "cDAQ1Mod1/port0/line5"  # NI 9472, Slot 1, DO 5 (Ventil 1 / Relais D1)
VALVE2_CHANNEL = "cDAQ1Mod1/port0/line6"  # NI 9472, Slot 1, DO 6 (Ventil 2 / Relais D2)
AI_CHANNELS = [                        # NI 9203, Slot 7 (3 Pyrometer 4-20mA)
    "cDAQ1Mod7/ai0",
    "cDAQ1Mod7/ai1",
    "cDAQ1Mod7/ai2",
]
AI_CHANNEL  = AI_CHANNELS[0]           # Backward-Compat

VOLTAGE_MIN = 0.0    # Volt bei RPM_MIN
VOLTAGE_MAX = 10.0   # Volt bei RPM_MAX

RPM_MIN     = 0
RPM_MAX     = 24000  # Maximale Drehzahl der HSD-Spindel
DEFAULT_RPM = 1000    # Startdrehzahl

# ─── Pyrometer (Optris CSmicro LT22H, OPTCSMALT22HHCF305) ───────────────────
TEMP_MIN     = 0.0    # °C bei 4 mA (Werkseinstellung)
TEMP_MAX     = 500.0  # °C bei 20 mA (Werkseinstellung)
CURRENT_4MA  = 0.004  # 4 mA Untergrenze
CURRENT_20MA = 0.020  # 20 mA Obergrenze


def current_to_temp(amps: float) -> float:
    """4-20 mA Stromsignal in Temperatur (°C) umrechnen."""
    clamped = max(CURRENT_4MA, min(CURRENT_20MA, amps))
    return TEMP_MIN + (clamped - CURRENT_4MA) / (CURRENT_20MA - CURRENT_4MA) * (TEMP_MAX - TEMP_MIN)
# ──────────────────────────────────────────────────────────────────────────────


def discover_hardware():
    """
    Listet alle erkannten NI-DAQmx-Geräte, AO-Kanäle und DO-Leitungen auf.
    Ausführen mit: python spindle_controller.py --discover
    """
    system = nidaqmx.system.System.local()

    if not system.devices:
        print("Keine NI-DAQmx-Geräte gefunden.")
        print("Prüfen: NI-DAQmx-Runtime installiert? Gerät in NI MAX sichtbar?")
        return

    for device in system.devices:
        print(f"\nGerät: {device.name}  (Typ: {device.product_type})")

        ao_channels = [ch.name for ch in device.ao_physical_chans]
        do_lines    = [line.name for line in device.do_lines]
        ai_channels = [ch.name for ch in device.ai_physical_chans]

        if ao_channels:
            print(f"  AO-Kanäle : {ao_channels}")
        if do_lines:
            print(f"  DO-Leitungen: {do_lines}")
        if ai_channels:
            print(f"  AI-Kanäle : {ai_channels}")


class SpindleController:
    """
    Steuert die Hochfrequenzspindel über NI cDAQ-9178.

    Drehzahl → Spannung auf AO-Kanal (Drehzahl_S1.vi-Äquivalent)
    Enable/Disable → digitales Signal auf DO-Kanal (Spindelstart.vi-Äquivalent)
    """

    def __init__(self):
        self._ao_task = None
        self._do_task = None
        self._ai_task = None
        self._valve_task = None
        self._valve_states = [False, False]
        self._enabled = False
        self._current_rpm = 0.0
        self._setup_tasks()

    def _setup_tasks(self):
        # Analogausgang-Task (Drehzahl — entspricht Drehzahl_S1.vi)
        self._ao_task = nidaqmx.Task()
        self._ao_task.ao_channels.add_ao_voltage_chan(
            AO_CHANNEL,
            min_val=VOLTAGE_MIN,
            max_val=VOLTAGE_MAX,
        )

        # Digitalausgang-Task (Enable — entspricht Spindelstart.vi)
        self._do_task = nidaqmx.Task()
        self._do_task.do_channels.add_do_chan(
            DO_CHANNEL,
            line_grouping=LineGrouping.CHAN_PER_LINE,
        )

        # Ventil-Task (2 Magnetventile ueber Relais D1/D2)
        try:
            self._valve_task = nidaqmx.Task()
            self._valve_task.do_channels.add_do_chan(
                VALVE1_CHANNEL, line_grouping=LineGrouping.CHAN_PER_LINE)
            self._valve_task.do_channels.add_do_chan(
                VALVE2_CHANNEL, line_grouping=LineGrouping.CHAN_PER_LINE)
            self._valve_task.write([False, False])
        except Exception:
            self._valve_task = None

        # Analogeingang-Task (Pyrometer 4-20mA — NI 9203, bis zu 3 Kanaele)
        try:
            from nidaqmx.constants import AcquisitionType
            self._ai_task = nidaqmx.Task()
            for ch in AI_CHANNELS:
                self._ai_task.ai_channels.add_ai_current_chan(
                    ch,
                    min_val=CURRENT_4MA,
                    max_val=CURRENT_20MA,
                )
            # 50 Samples @ 1 kHz mitteln → stabiler Messwert
            self._ai_task.timing.cfg_samp_clk_timing(
                rate=1000,
                sample_mode=AcquisitionType.FINITE,
                samps_per_chan=50,
            )
            self._num_ai = len(AI_CHANNELS)
        except Exception:
            self._ai_task = None
            self._num_ai = 0

    def rpm_to_voltage(self, rpm: float) -> float:
        """Lineare Umrechnung: Drehzahl [1/min] → Spannung [V]."""
        rpm_clamped = max(float(RPM_MIN), min(float(RPM_MAX), rpm))
        if RPM_MAX == RPM_MIN:
            return VOLTAGE_MIN
        ratio = (rpm_clamped - RPM_MIN) / (RPM_MAX - RPM_MIN)
        return VOLTAGE_MIN + ratio * (VOLTAGE_MAX - VOLTAGE_MIN)

    def set_speed(self, rpm: float) -> float:
        """
        Setzt die Solldrehzahl. Klemmt auf [RPM_MIN, RPM_MAX].
        Gibt die tatsächlich geschriebene Spannung zurück.
        """
        rpm_clamped = max(float(RPM_MIN), min(float(RPM_MAX), float(rpm)))
        voltage = self.rpm_to_voltage(rpm_clamped)
        self._ao_task.write(voltage)
        self._current_rpm = rpm_clamped
        print(f"  Drehzahl: {rpm_clamped:.0f} U/min -> {voltage:.3f} V")
        return voltage

    def enable(self):
        """Spindel einschalten (DO 1 = HIGH)."""
        self._do_task.write(True)
        self._enabled = True
        print("  Spindel EINGESCHALTET")

    def disable(self):
        """Spindel ausschalten (DO 1 = LOW)."""
        self._do_task.write(False)
        self._enabled = False
        print("  Spindel AUSGESCHALTET")

    def set_valve(self, index, state):
        """Ventil schalten. index: 0 oder 1, state: True/False."""
        if self._valve_task is None:
            return
        self._valve_states[index] = bool(state)
        self._valve_task.write(self._valve_states)
        print(f"  Ventil {index+1}: {'OFFEN' if state else 'ZU'}")

    def get_valve_states(self):
        """Gibt [bool, bool] fuer Ventil 1 und 2 zurueck."""
        return list(self._valve_states)

    def read_temperature(self):
        """Temperatur vom ersten Pyrometer (backward-compat). Siehe read_temperatures()."""
        temps = self.read_temperatures()
        return temps[0] if temps else None

    def read_temperatures(self):
        """Liste aller Pyrometer-Temperaturen in °C (50-Sample-Mittelwert pro Kanal)."""
        if self._ai_task is None or self._num_ai == 0:
            return []
        self._ai_task.start()
        raw = self._ai_task.read(number_of_samples_per_channel=50)
        self._ai_task.stop()
        # Bei 1 Kanal: flache Liste. Bei mehreren: Liste von Listen.
        if self._num_ai == 1:
            channels = [raw]
        else:
            channels = raw
        return [round(current_to_temp(sum(s) / len(s)), 1) for s in channels]

    def close(self):
        """Sicheres Herunterfahren: Disable → AO auf 0 V → Tasks schließen."""
        print("Spindel-Controller wird beendet...")
        try:
            if self._enabled:
                self.disable()
        except Exception as exc:
            print(f"  Warnung beim Deaktivieren: {exc}")

        try:
            self._ao_task.write(VOLTAGE_MIN)
        except Exception as exc:
            print(f"  Warnung beim Nullsetzen des AO: {exc}")

        if self._ao_task:
            self._ao_task.close()
            self._ao_task = None

        if self._do_task:
            self._do_task.close()
            self._do_task = None

        if self._valve_task:
            try:
                self._valve_task.write([False, False])
            except Exception:
                pass
            self._valve_task.close()
            self._valve_task = None

        if self._ai_task:
            self._ai_task.close()
            self._ai_task = None

        print("Tasks sauber geschlossen.")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


LAUFZEIT_SEKUNDEN = 3 * 60  # 3 Minuten


def run_cli(controller: SpindleController):
    """
    Interaktive Eingabe. Stoppt automatisch nach 3 Minuten.
    Befehle: <Zahl> = Drehzahl in U/min | e = Ein | d = Aus | q = Beenden
    """
    import time
    import threading

    ende = time.time() + LAUFZEIT_SEKUNDEN

    def timer_thread():
        remaining = LAUFZEIT_SEKUNDEN
        while remaining > 0:
            time.sleep(min(30, remaining))
            remaining = ende - time.time()
            if remaining > 0:
                print(f"\n  [Timer] Noch {int(remaining // 60)}:{int(remaining % 60):02d} min")
        print("\n  [Timer] 3 Minuten abgelaufen — Spindel wird gestoppt.")
        controller.disable()

    t = threading.Thread(target=timer_thread, daemon=True)
    t.start()

    print("\nSpindel-Steuerung (CLI) — Läuft 3 Minuten")
    print(f"  Drehzahlbereich: {RPM_MIN} – {RPM_MAX} U/min")
    print(f"  Spannungsbereich: {VOLTAGE_MIN} V – {VOLTAGE_MAX} V")
    print("  Befehle: <Drehzahl> | e (ein) | d (aus) | q (beenden)\n")

    while time.time() < ende:
        try:
            raw = input("spindel> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue
        elif raw == "q":
            break
        elif raw == "e":
            controller.enable()
        elif raw == "d":
            controller.disable()
        else:
            try:
                rpm = float(raw)
                controller.set_speed(rpm)
            except ValueError:
                print(f"  Unbekannter Befehl: '{raw}'. Drehzahl eingeben, 'e', 'd' oder 'q'.")


def run_demo(controller: SpindleController):
    """
    Testreihe: Reduzierte Drehzahl → Lastdrehzahl → Stop.
    Entspricht dem Ablauf Drehzahl reduziert → Drehzahl Last in LabVIEW.
    """
    import time

    reduced = 1000   # Drehzahl reduziert [1/min]
    load    = 6000   # Drehzahl Last [1/min]

    print(f"\nDemo-Ablauf: reduziert={reduced} U/min, Last={load} U/min")

    print("Schritt 1: Reduzierte Drehzahl setzen, Spindel einschalten")
    controller.set_speed(reduced)
    controller.enable()
    time.sleep(3)

    print("Schritt 2: Auf Lastdrehzahl hochfahren")
    controller.set_speed(load)
    time.sleep(5)

    print("Schritt 3: Auf reduzierte Drehzahl zurück")
    controller.set_speed(reduced)
    time.sleep(2)

    print("Schritt 4: Spindel ausschalten")
    controller.disable()


def main():
    print("=== HSD Spindel-Steuerung (NI cDAQ-9178) ===\n")

    if "--discover" in sys.argv:
        print("Hardware-Erkennung:\n")
        discover_hardware()
        return

    demo_mode = "--demo" in sys.argv

    print(f"AO-Kanal  : {AO_CHANNEL}")
    print(f"DO-Kanal  : {DO_CHANNEL}")
    print(f"Start-Drehzahl: {DEFAULT_RPM} U/min\n")

    try:
        with SpindleController() as spindle:
            print(f"Initialisiert. Setze Startdrehzahl: {DEFAULT_RPM} U/min")
            spindle.set_speed(DEFAULT_RPM)

            if demo_mode:
                run_demo(spindle)
            else:
                run_cli(spindle)

    except nidaqmx.errors.DaqError as exc:
        print(f"\nDAQmx-Fehler: {exc}")
        print("\nFehlersuche:")
        print("  1. Ausführen: python spindle_controller.py --discover")
        print("     → Zeigt verfügbare Geräte und Kanalnamen")
        print("  2. AO_CHANNEL und DO_CHANNEL oben im Skript anpassen")
        print("  3. NI-DAQmx-Runtime installiert? Gerät in NI MAX sichtbar?")
        print("  4. NI 9472: Ist 24V an Klemme V+ angeschlossen?")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

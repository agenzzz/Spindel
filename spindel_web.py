"""
HSD Spindel Web-Interface
=========================
Steuert die Hochfrequenzspindel über einen Browser.
Doppelklick auf "Spindel Starten.bat" startet Server + öffnet Browser.
Mehrfaches Klicken: öffnet nur Browser, kein zweiter Server.
"""

import sys
import csv
import io
import json
import time
import socket
import threading
import webbrowser
from datetime import datetime

from flask import Flask, Response, render_template, request, jsonify

from filters import build_chain, AVAILABLE_FILTERS

# ─── KONFIGURATION ────────────────────────────────────────────────────────────
APP_VERSION  = "2"              # bei jeder Aenderung hochzaehlen
HOST         = "0.0.0.0"       # Alle Netzwerk-Interfaces → LAN-Zugriff möglich
PORT         = 5000
SPINDLE_NAME = "HSD Spindel"
PSU_MAX_VOLT  = 60.0            # RD6006 max 60V
PSU_MAX_AMP   = 6.0             # RD6006 max 6A
PSU2_MAX_VOLT = 60.0            # DPM8624 max 60V
PSU2_MAX_AMP  = 24.0            # DPM8624 max 24A
# ──────────────────────────────────────────────────────────────────────────────

# ─── CONTROLLER-IMPORT MIT SIMULATION-FALLBACK ────────────────────────────────
try:
    import spindle_controller as _sc
    from spindle_controller import (
        SpindleController,
        AO_CHANNEL, DO_CHANNEL,
        VOLTAGE_MIN, VOLTAGE_MAX,
        RPM_MIN, RPM_MAX, DEFAULT_RPM,
        MOTOR_POLES, FU_MAX_HZ,
    )
    _HW_AVAILABLE = True
except ImportError:
    _HW_AVAILABLE = False
    AO_CHANNEL  = "cDAQ1Mod4/ao1"
    DO_CHANNEL  = "cDAQ1Mod1/port0/line1"
    VOLTAGE_MIN = 0.0
    VOLTAGE_MAX = 10.0
    RPM_MIN     = 0
    MOTOR_POLES = 4
    FU_MAX_HZ   = 400.0
    RPM_MAX     = 12000
    DEFAULT_RPM = 1000


# ─── RD6006 PSU IMPORT MIT FALLBACK ──────────────────────────────────────────
try:
    from rd6006_control import RD6006
    _PSU_AVAILABLE = True
except ImportError:
    _PSU_AVAILABLE = False


# ─── DPM8600 PSU IMPORT MIT FALLBACK ─────────────────────────────────────────
try:
    from dpm8600_control import DPM8600
    _PSU2_AVAILABLE = True
except ImportError:
    _PSU2_AVAILABLE = False
# ──────────────────────────────────────────────────────────────────────────────


class SimulationPSU:
    """Ersetzt RD6006/DPM8600 wenn kein Netzteil verfügbar."""
    _output  = False
    _soll_v  = 0.0
    _soll_a  = 1.0

    def set_voltage(self, volts):
        self._soll_v = volts

    def set_current(self, amps):
        self._soll_a = amps

    def enable(self):
        self._output = True

    def disable(self):
        self._output = False

    def status(self):
        return {
            "soll_v":     self._soll_v,
            "soll_a":     self._soll_a,
            "ist_v":      self._soll_v if self._output else 0.0,
            "ist_a":      0.0,
            "input_v":    0.0,
            "protection": 0,
            "cv_cc":      0,
            "output":     self._output,
        }
# ──────────────────────────────────────────────────────────────────────────────


class SimulationController:
    """Ersetzt SpindleController wenn keine Hardware verfügbar."""
    _enabled    = False
    _current_rpm = 0.0

    def rpm_to_voltage(self, rpm):
        clamped = max(float(RPM_MIN), min(float(RPM_MAX), float(rpm)))
        if RPM_MAX == RPM_MIN:
            return VOLTAGE_MIN
        return VOLTAGE_MIN + (clamped - RPM_MIN) / (RPM_MAX - RPM_MIN) * (VOLTAGE_MAX - VOLTAGE_MIN)

    def set_speed(self, rpm):
        rpm = max(float(RPM_MIN), min(float(RPM_MAX), float(rpm)))
        self._current_rpm = rpm
        return self.rpm_to_voltage(rpm)

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    _valve_states = [False, False]

    def set_valve(self, index, state):
        self._valve_states[index] = bool(state)

    def get_valve_states(self):
        return list(self._valve_states)

    def read_temperature(self):
        return 22.0

    def read_temperatures(self):
        return [22.0, 22.5, 21.8]

    def close(self):
        self._enabled = False
# ──────────────────────────────────────────────────────────────────────────────

# ─── GLOBALER STATE ───────────────────────────────────────────────────────────
state = {
    "soll_rpm":     float(DEFAULT_RPM) if _HW_AVAILABLE else 1000.0,
    "ist_rpm":      0.0,
    "voltage":      0.0,
    "frequency_hz": 0.0,
    "enabled":      False,
    "laufzeit_s":   0.0,
    "restzeit_s":   None,
    "spindle_name": SPINDLE_NAME,
    "host":         HOST,
    "port":         PORT,
    "ao_channel":   AO_CHANNEL,
    "do_channel":   DO_CHANNEL,
    "rpm_min":      float(RPM_MIN),
    "rpm_max":      float(RPM_MAX),
    "voltage_min":  float(VOLTAGE_MIN),
    "voltage_max":  float(VOLTAGE_MAX),
    "motor_poles":  int(MOTOR_POLES),
    "fu_max_hz":    float(FU_MAX_HZ),
    "version":      APP_VERSION,
    "simulation":   False,
    "error":        "",
    # ── Temperaturen (3 Pyrometer) ──
    "temperature_c":  None,            # Backward-Compat = temps[0]
    "temperatures":       [None, None, None],  # gefiltert (fuer Anzeige)
    "temperatures_raw":   [None, None, None],  # Rohwerte (fuer CSV)
    "temp_labels":        ["Sensor 1", "Sensor 2", "Sensor 3"],
    "temp_filters":       [                      # pro Sensor eine Filterkette
        [{"type": "median", "n": 5}, {"type": "lowpass", "alpha": 0.3}],
        [{"type": "median", "n": 5}, {"type": "lowpass", "alpha": 0.3}],
        [{"type": "median", "n": 5}, {"type": "lowpass", "alpha": 0.3}],
    ],
    "available_filters":  AVAILABLE_FILTERS,
    # ── Ventile ──
    "valve1":         False,
    "valve2":         False,
    "valve_labels":   ["Ventil 1", "Ventil 2"],
    # ── PSU (RD6006) ──
    "psu_available":  False,
    "psu_simulation": False,
    "psu_soll_v":     0.0,
    "psu_soll_a":     1.0,
    "psu_ist_v":      0.0,
    "psu_ist_a":      0.0,
    "psu_input_v":    0.0,
    "psu_output":     False,
    "psu_cv_cc":      0,       # 0=CV, 1=CC
    "psu_protection": 0,       # 0=OK, 1=OVP, 2=OCP
    "psu_error":      "",
    # ── PSU2 (DPM8624) ──
    "psu2_available":  False,
    "psu2_simulation": False,
    "psu2_soll_v":     0.0,
    "psu2_soll_a":     1.0,
    "psu2_ist_v":      0.0,
    "psu2_ist_a":      0.0,
    "psu2_input_v":    0.0,
    "psu2_output":     False,
    "psu2_cv_cc":      0,
    "psu2_protection": 0,
    "psu2_error":      "",
}
state_lock  = threading.Lock()
_timer_gen  = 0
_timer_gen_lock = threading.Lock()  # Race-Fix: gen++ und capture atomar

# Filter-Ketten pro Sensor (werden bei Reconfig neu gebaut)
_temp_chains = [build_chain(cfg) for cfg in state["temp_filters"]]
_temp_chains_lock = threading.Lock()

# ─── HISTORIE ─────────────────────────────────────────────────────────────────
MAX_HISTORY  = 18000   # ~1 Stunde bei 200 ms Intervall
history      = []      # Liste von dicts: {ts, soll, ist, volt, freq, enabled}
history_lock = threading.Lock()
_session_start = datetime.now().strftime("%Y%m%d_%H%M%S")

def _record(snap):
    """Einen State-Snapshot in die Historie eintragen."""
    entry = {
        "ts":      time.time(),
        "soll":    snap["soll_rpm"],
        "ist":     snap["ist_rpm"],
        "volt":    snap["voltage"],
        "freq":    snap["frequency_hz"],
        "enabled":   int(snap["enabled"]),
        "temps":     list(snap.get("temperatures") or [None, None, None]),
        "temps_raw": list(snap.get("temperatures_raw") or [None, None, None]),
    }
    with history_lock:
        history.append(entry)
        if len(history) > MAX_HISTORY:
            del history[0]
# ──────────────────────────────────────────────────────────────────────────────

# ─── CONTROLLER INITIALISIEREN ────────────────────────────────────────────────
def _init_controller():
    global controller
    if not _HW_AVAILABLE:
        state["simulation"] = True
        state["error"] = "spindle_controller.py nicht gefunden — Simulation"
        return SimulationController()
    try:
        ctrl = SpindleController()
        state["simulation"] = False
        state["error"] = ""
        return ctrl
    except Exception as exc:
        state["simulation"] = True
        state["error"] = f"Hardware nicht erreichbar: {exc}"
        return SimulationController()


controller = _init_controller()


def _init_psu():
    global psu
    if not _PSU_AVAILABLE:
        state["psu_available"]  = False
        state["psu_simulation"] = True
        state["psu_error"]      = "rd6006_control.py nicht gefunden"
        return SimulationPSU()
    try:
        p = RD6006()
        state["psu_available"]  = True
        state["psu_simulation"] = False
        state["psu_error"]      = ""
        return p
    except Exception as exc:
        state["psu_available"]  = True
        state["psu_simulation"] = True
        state["psu_error"]      = f"RD6006 nicht erreichbar: {exc}"
        return SimulationPSU()


psu = _init_psu()
psu_lock = threading.Lock()

# RD6006 COM-Port merken, damit DPM8600 ihn ausschließen kann
_psu1_port = getattr(psu, "inst", None)
_psu1_com  = _psu1_port.serial.port if _psu1_port else None


def _init_psu2():
    global psu2
    if not _PSU2_AVAILABLE:
        state["psu2_available"]  = False
        state["psu2_simulation"] = True
        state["psu2_error"]      = "dpm8600_control.py nicht gefunden"
        return SimulationPSU()
    try:
        exclude = [_psu1_com] if _psu1_com else []
        p = DPM8600(exclude_ports=exclude)
        state["psu2_available"]  = True
        state["psu2_simulation"] = False
        state["psu2_error"]      = ""
        return p
    except Exception as exc:
        state["psu2_available"]  = True
        state["psu2_simulation"] = True
        state["psu2_error"]      = f"DPM8600 nicht erreichbar: {exc}"
        return SimulationPSU()


psu2 = _init_psu2()
psu2_lock = threading.Lock()
# ──────────────────────────────────────────────────────────────────────────────

# ─── SENSOR-POLL-THREAD ──────────────────────────────────────────────────────
# Ein einziger Thread liest DAQ + PSUs, egal wie viele Browser-Tabs offen sind.
# Verhindert konkurrierende ai_task.start()-Aufrufe (Fable-Review P3).
def _sensor_poll_worker():
    while True:
        # Temperaturen (DAQ) + Filter
        try:
            raw = controller.read_temperatures_raw()
            raw = (list(raw) + [None, None, None])[:3]
            with _temp_chains_lock:
                filtered = [
                    round(_temp_chains[i].apply(raw[i]), 2) if raw[i] is not None else None
                    for i in range(3)
                ]
            with state_lock:
                state["temperatures_raw"] = raw
                state["temperatures"]     = filtered
                state["temperature_c"]    = filtered[0]
        except Exception:
            pass

        # PSU1 (RD6006)
        try:
            with psu_lock:
                ps = psu.status()
            with state_lock:
                state["psu_soll_v"]     = ps["soll_v"]
                state["psu_soll_a"]     = ps["soll_a"]
                state["psu_ist_v"]      = ps["ist_v"]
                state["psu_ist_a"]      = ps["ist_a"]
                state["psu_input_v"]    = ps["input_v"]
                state["psu_output"]     = ps["output"]
                state["psu_cv_cc"]      = ps["cv_cc"]
                state["psu_protection"] = ps["protection"]
        except Exception:
            pass

        # PSU2 (DPM8624)
        try:
            with psu2_lock:
                ps2 = psu2.status()
            with state_lock:
                state["psu2_soll_v"]     = ps2["soll_v"]
                state["psu2_soll_a"]     = ps2["soll_a"]
                state["psu2_ist_v"]      = ps2["ist_v"]
                state["psu2_ist_a"]      = ps2["ist_a"]
                state["psu2_input_v"]    = ps2["input_v"]
                state["psu2_output"]     = ps2["output"]
                state["psu2_cv_cc"]      = ps2["cv_cc"]
                state["psu2_protection"] = ps2["protection"]
        except Exception:
            pass

        time.sleep(0.2)   # 5 Hz Update


_sensor_thread = threading.Thread(target=_sensor_poll_worker, daemon=True)
_sensor_thread.start()
# ──────────────────────────────────────────────────────────────────────────────

# ─── TIMER-THREAD (Generation-Counter, kein Self-Join-Deadlock) ──────────────
def _timer_worker(limit_s, my_gen):
    """Laufzeit-Zaehler. Terminiert wenn my_gen != _timer_gen (invalidiert)."""
    t0 = time.monotonic()
    while my_gen == _timer_gen:
        elapsed = time.monotonic() - t0
        remaining = (limit_s - elapsed) if limit_s is not None else None
        with state_lock:
            state["laufzeit_s"] = elapsed
            state["restzeit_s"] = remaining
        if limit_s is not None and elapsed >= limit_s:
            _do_stop()  # ruft intern _stop_timer() → wir sehen my_gen != _timer_gen und exiten
            return
        time.sleep(0.5)


def _start_timer(limit_s=None):
    global _timer_gen
    with _timer_gen_lock:             # atomar: inkrementieren + capturen
        _timer_gen += 1
        my_gen = _timer_gen
    t = threading.Thread(target=_timer_worker, args=(limit_s, my_gen), daemon=True)
    t.start()


def _stop_timer():
    global _timer_gen
    with _timer_gen_lock:
        _timer_gen += 1               # alte Threads terminieren beim naechsten Loop
# ──────────────────────────────────────────────────────────────────────────────

# ─── HILFSFUNKTIONEN ──────────────────────────────────────────────────────────
def _rpm_to_voltage(rpm):
    r_min = state["rpm_min"]
    r_max = state["rpm_max"]
    v_min = state["voltage_min"]
    v_max = state["voltage_max"]
    clamped = max(r_min, min(r_max, float(rpm)))
    if r_max == r_min:
        return v_min
    return v_min + (clamped - r_min) / (r_max - r_min) * (v_max - v_min)


def _do_stop():
    """Spindel stoppen — thread-sicher, kein Flask-Context nötig."""
    _stop_timer()
    try:
        controller.disable()
    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)
    with state_lock:
        state["enabled"]      = False
        state["ist_rpm"]      = 0.0
        state["laufzeit_s"]   = 0.0
        state["restzeit_s"]   = None


def _get_local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
# ──────────────────────────────────────────────────────────────────────────────

# ─── FLASK APP ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True


@app.route("/")
def index():
    local_ip = _get_local_ip()
    return render_template("index.html", local_ip=local_ip, port=PORT)


@app.route("/stream")
def stream():
    """SSE-Stream: liest nur State (Hardware-Reads passieren in _sensor_poll_worker).

    Sicher fuer mehrere gleichzeitige Browser-Tabs — kein DAQ-Race mehr.
    """
    def event_gen():
        while True:
            with state_lock:
                snap = dict(state)
            _record(snap)
            yield f"data: {json.dumps(snap)}\n\n"
            time.sleep(0.2)
    return Response(
        event_gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/export/csv")
def export_csv():
    with history_lock:
        rows = list(history)
    buf = io.StringIO()
    w = csv.writer(buf)
    with state_lock:
        labels = list(state.get("temp_labels", ["Sensor 1", "Sensor 2", "Sensor 3"]))
    w.writerow([
        "Zeitstempel", "Soll_RPM", "Ist_RPM", "Spannung_V", "Frequenz_Hz", "Aktiv",
        f"{labels[0]}_gefiltert_C", f"{labels[1]}_gefiltert_C", f"{labels[2]}_gefiltert_C",
        f"{labels[0]}_roh_C",       f"{labels[1]}_roh_C",       f"{labels[2]}_roh_C",
    ])
    for r in rows:
        temps     = (list(r.get("temps")     or []) + [None, None, None])[:3]
        temps_raw = (list(r.get("temps_raw") or []) + [None, None, None])[:3]
        w.writerow([
            datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            f"{r['soll']:.1f}",
            f"{r['ist']:.1f}",
            f"{r['volt']:.4f}",
            f"{r['freq']:.2f}",
            r["enabled"],
            f"{temps[0]:.2f}"     if temps[0]     is not None else "",
            f"{temps[1]:.2f}"     if temps[1]     is not None else "",
            f"{temps[2]:.2f}"     if temps[2]     is not None else "",
            f"{temps_raw[0]:.2f}" if temps_raw[0] is not None else "",
            f"{temps_raw[1]:.2f}" if temps_raw[1] is not None else "",
            f"{temps_raw[2]:.2f}" if temps_raw[2] is not None else "",
        ])
    filename = f"Spindel_Historie_{_session_start}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify(state)


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(silent=True) or {}
    rpm      = float(data.get("rpm", state["soll_rpm"]))
    limit_m  = data.get("limit_min")   # None oder Zahl

    rpm = max(state["rpm_min"], min(state["rpm_max"], rpm))
    try:
        # Timer-Race-Fix: erst alten Timer stoppen, DANN State-Reset, DANN neuen starten
        _stop_timer()
        voltage = controller.set_speed(rpm)
        controller.enable()
        poles = state["motor_poles"]
        with state_lock:
            state["soll_rpm"]     = rpm
            state["ist_rpm"]      = rpm
            state["voltage"]      = voltage
            state["frequency_hz"] = rpm * poles / 120.0
            state["enabled"]      = True
            state["laufzeit_s"]   = 0.0
            state["restzeit_s"]   = float(limit_m) * 60 if limit_m else None
            state["error"]        = ""
        limit_s = float(limit_m) * 60 if limit_m else None
        _start_timer(limit_s)
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/stop", methods=["POST"])
def api_stop():
    try:
        _do_stop()
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/set_speed", methods=["POST"])
def api_set_speed():
    data = request.get_json(silent=True) or {}
    rpm  = float(data.get("rpm", state["soll_rpm"]))
    rpm  = max(state["rpm_min"], min(state["rpm_max"], rpm))
    try:
        voltage = controller.set_speed(rpm)
        poles = state["motor_poles"]
        with state_lock:
            state["soll_rpm"]     = rpm
            state["ist_rpm"]      = rpm if state["enabled"] else 0.0
            state["voltage"]      = voltage
            state["frequency_hz"] = rpm * poles / 120.0
            state["error"]        = ""
        return jsonify({"ok": True, "voltage": voltage})
    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/enable", methods=["POST"])
def api_enable():
    try:
        controller.enable()
        with state_lock:
            state["enabled"]  = True
            state["ist_rpm"]  = state["soll_rpm"]
            state["error"]    = ""
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/disable", methods=["POST"])
def api_disable():
    try:
        _do_stop()
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/settings", methods=["POST"])
def api_settings():
    data = request.get_json(silent=True) or {}
    with state_lock:
        if "spindle_name" in data:
            state["spindle_name"] = str(data["spindle_name"])[:64]
        if "host" in data:
            state["host"] = str(data["host"])
        if "temp_labels" in data and isinstance(data["temp_labels"], list):
            labels = [str(x)[:32] for x in data["temp_labels"]][:3]
            while len(labels) < 3:
                labels.append(f"Sensor {len(labels)+1}")
            state["temp_labels"] = labels
        if "valve_labels" in data and isinstance(data["valve_labels"], list):
            vlabels = [str(x)[:32] for x in data["valve_labels"]][:2]
            while len(vlabels) < 2:
                vlabels.append(f"Ventil {len(vlabels)+1}")
            state["valve_labels"] = vlabels
    return jsonify({"ok": True})


@app.route("/api/reinit", methods=["POST"])
def api_reinit():
    """Experten-Modus: Kanäle und Limits neu konfigurieren."""
    if not _HW_AVAILABLE:
        return jsonify({"ok": False, "error": "Hardware-Modul nicht geladen"})
    data = request.get_json(silent=True) or {}
    try:
        # State-Limits updaten
        with state_lock:
            if "ao_channel"  in data: state["ao_channel"]  = data["ao_channel"]
            if "do_channel"  in data: state["do_channel"]  = data["do_channel"]
            if "rpm_min"     in data: state["rpm_min"]     = float(data["rpm_min"])
            if "rpm_max"     in data: state["rpm_max"]     = float(data["rpm_max"])
            if "voltage_min" in data: state["voltage_min"] = float(data["voltage_min"])
            if "voltage_max" in data: state["voltage_max"] = float(data["voltage_max"])
            if "motor_poles" in data:
                p = max(2, min(16, int(data["motor_poles"])))
                state["motor_poles"] = p
                # RPM_MAX = 120 * FU_MAX_HZ / P  automatisch neu berechnen
                state["rpm_max"] = int(120.0 * state["fu_max_hz"] / p)
            if "fu_max_hz"   in data:
                state["fu_max_hz"] = float(data["fu_max_hz"])
                state["rpm_max"]   = int(120.0 * state["fu_max_hz"] / state["motor_poles"])

        global controller

        # Alte Tasks sauber schließen
        controller.close()

        # Modul-Globals patchen damit SpindleController() die neuen Werte liest
        _sc.AO_CHANNEL  = state["ao_channel"]
        _sc.DO_CHANNEL  = state["do_channel"]
        _sc.VOLTAGE_MIN = state["voltage_min"]
        _sc.VOLTAGE_MAX = state["voltage_max"]
        _sc.RPM_MIN     = state["rpm_min"]
        _sc.RPM_MAX     = state["rpm_max"]
        _sc.MOTOR_POLES = state["motor_poles"]
        _sc.FU_MAX_HZ   = state["fu_max_hz"]

        controller = SpindleController()

        with state_lock:
            state["error"] = ""
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
# ──────────────────────────────────────────────────────────────────────────────

# ─── PSU API-ROUTEN ──────────────────────────────────────────────────────────
@app.route("/api/psu/set", methods=["POST"])
def api_psu_set():
    data = request.get_json(silent=True) or {}
    try:
        with psu_lock:
            if "voltage" in data:
                v = max(0.0, min(PSU_MAX_VOLT, float(data["voltage"])))
                psu.set_voltage(v)
            if "current" in data:
                a = max(0.0, min(PSU_MAX_AMP, float(data["current"])))
                psu.set_current(a)
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["psu_error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/psu/on", methods=["POST"])
def api_psu_on():
    try:
        with psu_lock:
            psu.enable()
        with state_lock:
            state["psu_output"] = True
            state["psu_error"]  = ""
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["psu_error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/psu/off", methods=["POST"])
def api_psu_off():
    try:
        with psu_lock:
            psu.disable()
        with state_lock:
            state["psu_output"] = False
            state["psu_error"]  = ""
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["psu_error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
# ──────────────────────────────────────────────────────────────────────────────

# ─── TEMP-FILTER API ────────────────────────────────────────────────────────
@app.route("/api/temp_filters", methods=["POST"])
def api_temp_filters():
    """Erwartet: {"filters": [[...chain0...], [...chain1...], [...chain2...]]}"""
    global _temp_chains
    data = request.get_json(silent=True) or {}
    chains_cfg = data.get("filters")
    if not isinstance(chains_cfg, list) or len(chains_cfg) != 3:
        return jsonify({"ok": False, "error": "filters muss Liste mit 3 Ketten sein"}), 400
    try:
        new_chains = [build_chain(c) for c in chains_cfg]
        with _temp_chains_lock:
            _temp_chains = new_chains
        with state_lock:
            state["temp_filters"] = chains_cfg
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ─── VENTIL API-ROUTEN ───────────────────────────────────────────────────────
@app.route("/api/valve", methods=["POST"])
def api_valve():
    data = request.get_json(silent=True) or {}
    idx = int(data.get("valve", 1)) - 1  # 1-basiert → 0-basiert
    val = bool(data.get("state", False))
    if idx not in (0, 1):
        return jsonify({"ok": False, "error": "Ventil muss 1 oder 2 sein"}), 400
    try:
        controller.set_valve(idx, val)
        with state_lock:
            state[f"valve{idx+1}"] = val
            state["error"] = ""
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
# ──────────────────────────────────────────────────────────────────────────────

# ─── PSU2 (DPM8624) API-ROUTEN ──────────────────────────────────────────────
@app.route("/api/psu2/set", methods=["POST"])
def api_psu2_set():
    data = request.get_json(silent=True) or {}
    try:
        with psu2_lock:
            if "voltage" in data:
                v = max(0.0, min(PSU2_MAX_VOLT, float(data["voltage"])))
                psu2.set_voltage(v)
            if "current" in data:
                a = max(0.0, min(PSU2_MAX_AMP, float(data["current"])))
                psu2.set_current(a)
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["psu2_error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/psu2/on", methods=["POST"])
def api_psu2_on():
    try:
        with psu2_lock:
            psu2.enable()
        with state_lock:
            state["psu2_output"] = True
            state["psu2_error"]  = ""
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["psu2_error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/psu2/off", methods=["POST"])
def api_psu2_off():
    try:
        with psu2_lock:
            psu2.disable()
        with state_lock:
            state["psu2_output"] = False
            state["psu2_error"]  = ""
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["psu2_error"] = str(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
# ──────────────────────────────────────────────────────────────────────────────

# ─── SINGLE-INSTANCE + STARTER ────────────────────────────────────────────────
def _port_in_use():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


if __name__ == "__main__":
    if _port_in_use():
        # Server läuft bereits → nur Browser öffnen
        print(f"Server läuft bereits. Öffne Browser auf http://127.0.0.1:{PORT}")
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        sys.exit(0)

    local_ip = _get_local_ip()
    print("=" * 55)
    print(f"  HSD Spindel Web-Interface")
    print(f"  Lokal:  http://127.0.0.1:{PORT}")
    print(f"  LAN:    http://{local_ip}:{PORT}")
    print(f"  Modus:  {'SIMULATION' if state['simulation'] else 'HARDWARE'}")
    print("=" * 55)

    # Browser nach kurzem Delay öffnen (Server braucht ~1s)
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()

    try:
        app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)
    finally:
        controller.close()
        try:
            psu.disable()
        except Exception:
            pass
        try:
            psu2.disable()
        except Exception:
            pass

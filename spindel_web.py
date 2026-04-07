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

# ─── KONFIGURATION ────────────────────────────────────────────────────────────
HOST         = "0.0.0.0"       # Alle Netzwerk-Interfaces → LAN-Zugriff möglich
PORT         = 5000
SPINDLE_NAME = "HSD Spindel"
# ──────────────────────────────────────────────────────────────────────────────

# ─── CONTROLLER-IMPORT MIT SIMULATION-FALLBACK ────────────────────────────────
try:
    import spindle_controller as _sc
    from spindle_controller import (
        SpindleController,
        AO_CHANNEL, DO_CHANNEL,
        VOLTAGE_MIN, VOLTAGE_MAX,
        RPM_MIN, RPM_MAX, DEFAULT_RPM,
    )
    _HW_AVAILABLE = True
except ImportError:
    _HW_AVAILABLE = False
    AO_CHANNEL  = "cDAQ1Mod4/ao1"
    DO_CHANNEL  = "cDAQ1Mod1/port0/line1"
    VOLTAGE_MIN = 0.0
    VOLTAGE_MAX = 10.0
    RPM_MIN     = 0
    RPM_MAX     = 24000
    DEFAULT_RPM = 1000


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
    "simulation":   False,
    "error":        "",
}
state_lock  = threading.Lock()
timer_event = threading.Event()   # gesetzt → Timer-Thread soll stoppen

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
        "enabled": int(snap["enabled"]),
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
# ──────────────────────────────────────────────────────────────────────────────

# ─── TIMER-THREAD ─────────────────────────────────────────────────────────────
def _timer_worker(limit_s):
    t0 = time.monotonic()
    while not timer_event.is_set():
        elapsed = time.monotonic() - t0
        remaining = (limit_s - elapsed) if limit_s is not None else None
        with state_lock:
            state["laufzeit_s"] = elapsed
            state["restzeit_s"] = remaining
        if limit_s is not None and elapsed >= limit_s:
            _do_stop()
            break
        time.sleep(0.5)


def _start_timer(limit_s=None):
    timer_event.clear()
    t = threading.Thread(target=_timer_worker, args=(limit_s,), daemon=True)
    t.start()


def _stop_timer():
    timer_event.set()
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
    w.writerow(["Zeitstempel", "Soll_RPM", "Ist_RPM", "Spannung_V", "Frequenz_Hz", "Aktiv"])
    for r in rows:
        w.writerow([
            datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            f"{r['soll']:.1f}",
            f"{r['ist']:.1f}",
            f"{r['volt']:.4f}",
            f"{r['freq']:.2f}",
            r["enabled"],
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
        voltage = controller.set_speed(rpm)
        controller.enable()
        with state_lock:
            state["soll_rpm"]     = rpm
            state["ist_rpm"]      = rpm
            state["voltage"]      = voltage
            state["frequency_hz"] = rpm / 60.0
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
        with state_lock:
            state["soll_rpm"]     = rpm
            state["ist_rpm"]      = rpm if state["enabled"] else 0.0
            state["voltage"]      = voltage
            state["frequency_hz"] = rpm / 60.0
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

        controller = SpindleController()

        with state_lock:
            state["error"] = ""
        return jsonify({"ok": True})
    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)
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

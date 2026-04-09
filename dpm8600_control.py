"""
DPM8600 DC Power Supply — USB-Steuerung
Joy-IT JT-DPM8624 / Juntek DPM8600 via ASCII-Serial (CH341 USB-Serial)
Protokoll: `:01w10=3000\r\n` (einfaches ASCII, kein Modbus)
"""

import serial
import serial.tools.list_ports
import time


def find_dpm8600(exclude_ports=None):
    """CH340/CH341-Ports finden, die nicht schon vom RD6006 belegt sind."""
    exclude = set(exclude_ports or [])
    for port in serial.tools.list_ports.comports():
        if port.device in exclude:
            continue
        desc = (port.description or "") + (port.manufacturer or "")
        if "CH340" in desc or "CH341" in desc:
            return port.device
    return None


class DPM8600:
    def __init__(self, port=None, address=1, exclude_ports=None):
        if port is None:
            port = find_dpm8600(exclude_ports)
            if port is None:
                raise RuntimeError("DPM8600 nicht gefunden. USB angeschlossen?")
        self.port = port
        self.addr = f"{address:02d}"
        self.ser = serial.Serial(port, 9600, timeout=0.5)
        time.sleep(0.2)
        # Probe: Max-Strom lesen zur Validierung
        self.max_current = self._read_register(1) / 1000.0

    def _reconnect(self):
        """Verbindung neu aufbauen (USB kann kurz wegfallen)."""
        try:
            self.ser.close()
        except Exception:
            pass
        time.sleep(0.3)
        self.ser = serial.Serial(self.port, 9600, timeout=0.5)
        time.sleep(0.2)

    def _send(self, cmd, retries=2):
        """Befehl senden und Antwort lesen, mit Retry bei Verbindungsverlust."""
        for attempt in range(retries + 1):
            try:
                self.ser.reset_input_buffer()
                msg = f":{self.addr}{cmd}\n"
                self.ser.write(msg.encode())
                time.sleep(0.15)
                return self.ser.readline().decode(errors="ignore").strip()
            except (serial.SerialException, OSError):
                if attempt < retries:
                    self._reconnect()
                else:
                    raise
        return ""

    def _write_register(self, reg, value):
        resp = self._send(f"w{reg:02d}={value},")
        return "ok" in resp

    def _read_register(self, reg):
        resp = self._send(f"r{reg:02d}=0,,")
        # Antwort z.B.: ":01r30=3000." oder ":01r30=1."
        if "=" in resp:
            val_str = resp.split("=")[-1].rstrip(",. \r\n")
            try:
                return int(val_str)
            except ValueError:
                return 0
        return 0

    def set_voltage(self, volts: float):
        """Soll-Spannung setzen (V)."""
        self._write_register(10, int(volts * 100))

    def set_current(self, amps: float):
        """Strom-Limit setzen (A)."""
        self._write_register(11, int(amps * 1000))

    def enable(self):
        """Output einschalten."""
        self._write_register(12, 1)

    def disable(self):
        """Output ausschalten."""
        self._write_register(12, 0)

    def status(self) -> dict:
        """Alle relevanten Werte lesen."""
        return {
            "soll_v":     self._read_register(10) / 100.0,
            "soll_a":     self._read_register(11) / 1000.0,
            "ist_v":      self._read_register(30) / 100.0,
            "ist_a":      self._read_register(31) / 1000.0,
            "input_v":    0.0,
            "protection": 0,
            "cv_cc":      self._read_register(32),
            "output":     bool(self._read_register(12)),
        }


if __name__ == "__main__":
    psu = DPM8600()
    print(f"DPM8600 verbunden auf {psu.port}")
    print(f"Max. Strom: {psu.max_current:.1f} A")
    print()
    print("Befehle:  v <Volt>  |  i <Ampere>  |  on  |  off  |  s (Status)  |  q (Quit)")

    while True:
        try:
            cmd = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            psu.disable()
            break

        if cmd == "q":
            psu.disable()
            print("  Output AUS - Beendet.")
            break
        elif cmd == "s":
            st = psu.status()
            print(f"  Output: {'EIN' if st['output'] else 'AUS'}  "
                  f"| {st['ist_v']:.2f}V / {st['ist_a']:.3f}A  "
                  f"| Soll: {st['soll_v']:.2f}V / {st['soll_a']:.3f}A  "
                  f"| {'CV' if st['cv_cc'] == 0 else 'CC'}")
        elif cmd == "on":
            psu.enable()
            print("  Output EIN")
        elif cmd == "off":
            psu.disable()
            print("  Output AUS")
        elif cmd.startswith("v "):
            try:
                v = float(cmd[2:])
                psu.set_voltage(v)
                print(f"  Spannung -> {v:.2f} V")
            except ValueError:
                print("  Ungueltige Eingabe. Beispiel: v 5.0")
        elif cmd.startswith("i "):
            try:
                a = float(cmd[2:])
                psu.set_current(a)
                print(f"  Strom-Limit -> {a:.3f} A")
            except ValueError:
                print("  Ungueltige Eingabe. Beispiel: i 1.5")
        elif cmd:
            print("  Unbekannt. Befehle: v <V> | i <A> | on | off | s | q")

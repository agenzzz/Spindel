"""
RD6006 DC Power Supply — USB-Steuerung
Joy-IT JT-RD6006 / Riden RD6006 via Modbus RTU (CH341 USB-Serial)
"""

import minimalmodbus
import serial.tools.list_ports


def find_rd6006():
    """Sucht nach CH340/CH341 USB-Serial-Adapter (RD6006)."""
    for port in serial.tools.list_ports.comports():
        desc = (port.description or "") + (port.manufacturer or "")
        if "CH340" in desc or "CH341" in desc:
            return port.device
    return None


class RD6006:
    def __init__(self, port=None, address=1):
        if port is None:
            port = find_rd6006()
            if port is None:
                raise RuntimeError("RD6006 nicht gefunden. USB angeschlossen?")
        self.inst = minimalmodbus.Instrument(port, address)
        self.inst.serial.baudrate = 115200
        self.inst.serial.timeout = 1.0
        # Probe: Register lesen um sicherzustellen dass es ein RD6006 ist
        try:
            self.inst.read_register(0)  # Modell-ID
        except Exception:
            self.inst.serial.close()
            raise RuntimeError(f"Geraet auf {port} antwortet nicht als RD6006")

    def set_voltage(self, volts: float):
        """Soll-Spannung setzen (V)."""
        self.inst.write_register(8, int(volts * 100), functioncode=6)

    def set_current(self, amps: float):
        """Strom-Limit setzen (A)."""
        self.inst.write_register(9, int(amps * 1000), functioncode=6)

    def enable(self):
        """Output einschalten."""
        self.inst.write_register(18, 1, functioncode=6)

    def disable(self):
        """Output ausschalten."""
        self.inst.write_register(18, 0, functioncode=6)

    def read_voltage(self) -> float:
        """Ist-Spannung lesen (V)."""
        return self.inst.read_register(10) / 100.0

    def read_current(self) -> float:
        """Ist-Strom lesen (A)."""
        return self.inst.read_register(11) / 1000.0

    def read_input_voltage(self) -> float:
        """Eingangsspannung lesen (V)."""
        return self.inst.read_register(14) / 100.0

    def status(self) -> dict:
        """Alle relevanten Werte auf einmal lesen."""
        regs = self.inst.read_registers(8, 11)  # Register 8-18
        return {
            "soll_v":     regs[0] / 100.0,
            "soll_a":     regs[1] / 1000.0,
            "ist_v":      regs[2] / 100.0,
            "ist_a":      regs[3] / 1000.0,
            "input_v":    regs[6] / 100.0,
            "protection": regs[8],    # 0=OK, 1=OVP, 2=OCP
            "cv_cc":      regs[9],    # 0=CV, 1=CC
            "output":     bool(regs[10]),
        }


if __name__ == "__main__":
    psu = RD6006()
    print(f"RD6006 verbunden auf {psu.inst.serial.port}")
    print(f"Eingangsspannung: {psu.read_input_voltage():.1f} V")
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

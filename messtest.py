"""
Messtest: Gibt exakt 8.37 V auf dem Analogausgang aus.
Kanal: cDAQ1Mod4/ao1 (NI 9263, Slot 4, Klemme AO 1)
"""

import nidaqmx

AO_CHANNEL  = "cDAQ1Mod4/ao1"
SPANNUNG    = 4.2   # Volt

with nidaqmx.Task() as task:
    task.ao_channels.add_ao_voltage_chan(AO_CHANNEL, min_val=0.0, max_val=10.0)
    task.write(SPANNUNG)
    print(f"Ausgabe: {SPANNUNG} V auf {AO_CHANNEL}")
    print("Jetzt messen. Enter drücken zum Beenden...")
    input()
    task.write(0.0)
    print("Ausgang auf 0 V gesetzt.")

"""
Messtest: Setzt den Digitalausgang auf HIGH.
Kanal: cDAQ1Mod1/port0/line1 (NI 9472, Slot 1, Klemme DO 1)
"""

import nidaqmx
from nidaqmx.constants import LineGrouping

DO_CHANNEL = "cDAQ1Mod1/port0/line1"

with nidaqmx.Task() as task:
    task.do_channels.add_do_chan(DO_CHANNEL, line_grouping=LineGrouping.CHAN_PER_LINE)
    task.write(True)
    print(f"DO 1 = HIGH auf {DO_CHANNEL}")
    print("Jetzt messen (~24 V zwischen DO 1 und L). Enter drücken zum Beenden...")
    input()
    task.write(False)
    print("DO 1 = LOW gesetzt.")

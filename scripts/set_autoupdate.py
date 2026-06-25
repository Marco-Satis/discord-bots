#!/usr/bin/env python3
"""Schaltet scheduler.auto_update_enabled in config.json (atomar, formatschonend).
Aufruf: python3 set_autoupdate.py on|off
"""
import json
import os
import sys
from pathlib import Path

P = Path("/home/botuser/Discord_Bots/config/config.json")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("on", "off"):
        print("usage: set_autoupdate.py on|off")
        return 2
    on = sys.argv[1] == "on"
    txt = P.read_text(encoding="utf-8")
    src = '"auto_update_enabled": false' if on else '"auto_update_enabled": true'
    dst = '"auto_update_enabled": true' if on else '"auto_update_enabled": false'
    if dst in txt and src not in txt:
        print(f"bereits {dst} — keine Aenderung")
        return 0
    if src not in txt:
        print(f"PATTERN NICHT GEFUNDEN: {src}")
        return 3
    new = txt.replace(src, dst, 1)
    json.loads(new)  # Validierung: muss weiterhin gueltiges JSON sein
    tmp = str(P) + ".tmp"
    Path(tmp).write_text(new, encoding="utf-8")
    os.replace(tmp, P)
    print(f"OK: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

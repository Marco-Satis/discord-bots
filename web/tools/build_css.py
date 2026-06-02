#!/usr/bin/env python
"""Tailwind-CSS Build-Wrapper — umgeht Bun+OneDrive-Probleme.

Zwei Probleme auf OneDrive-synced Repos:
1. Bun's mkdir(recursive) ist nicht idempotent → EEXIST wenn Output-Dir existiert.
2. OneDrive lockt Verzeichnisse → shutil.rmtree(dist) scheitert mit WinError 5.

Lösung: Tailwind in ein frisches TEMP-Dir (außerhalb OneDrive) bauen lassen, dann
nur die fertige output.css per Datei-Copy ins Repo legen. Datei-Copy überschreibt
eine bestehende Datei ohne mkdir → kein EEXIST, kein Dir-Lock.

Aufruf (aus beliebigem Verzeichnis):
  python web/tools/build_css.py
Nach jeder Template-/input.css-Änderung erneut laufen (echtes --watch crasht wegen
des Bun-Bugs).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent  # .../web
BIN = WEB / "tools" / "tailwindcss.exe"
INPUT = WEB / "static" / "tailwind" / "input.css"
DIST = WEB / "static" / "tailwind" / "dist"
OUTPUT = DIST / "output.css"


def main() -> int:
    if not BIN.exists():
        print(f"[build_css] Tailwind-Binary fehlt: {BIN}")
        return 2
    if not INPUT.exists():
        print(f"[build_css] input.css fehlt: {INPUT}")
        return 2

    # Build in frisches TEMP-Dir außerhalb OneDrive (Bun mag frische Dirs)
    tmp_dir = Path(tempfile.mkdtemp(prefix="tw_build_"))
    tmp_out = tmp_dir / "output.css"
    try:
        result = subprocess.run(
            [str(BIN), "-i", str(INPUT), "-o", str(tmp_out)],
            cwd=str(WEB),
        )
        if result.returncode != 0 or not tmp_out.exists():
            print(f"[build_css] Tailwind-Build fehlgeschlagen (exit {result.returncode})")
            return result.returncode or 1

        # Ziel-Dir sicherstellen (Python-mkdir IST idempotent) + Datei-Copy
        os.makedirs(DIST, exist_ok=True)
        shutil.copyfile(tmp_out, OUTPUT)
        kb = OUTPUT.stat().st_size // 1024
        print(f"[build_css] OK -> {OUTPUT} ({kb} KB)")
        return 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

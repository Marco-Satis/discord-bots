#!/usr/bin/env python3
"""Minecraft-Mod-Hash-Lockfile Tooling.

Hintergrund (audit 2026-05-17, minecraft.md Finding-1):
BMC laeuft mit 288 NeoForge-Mods aus CurseForge. Das Server-Pack-ZIP wird via
SHA1+MD5 gegen das Manifest verifiziert, aber die einzelnen Mods darin nicht.
Kombiniert mit op-permission-level=4 und enable-command-block=true ist ein
malicious-Mod-Update via Author-Account-Takeover ein direkter Server-Takeover-
Vektor.

Dieses Tool erstellt + verifiziert eine SHA256-Lockfile fuer alle .jar in mods/.
Pre-install-Verify in der Update-Pipeline blockt unbemerkte Aenderungen.

Usage:
    # Lockfile generieren (initial oder nach bewusstem Mod-Update):
    python3 scripts/mc_mod_lockfile.py generate <mods_dir> > mods.lockfile.csv

    # Lockfile verifizieren (vor Server-Start, in Update-Pipeline):
    python3 scripts/mc_mod_lockfile.py verify <mods_dir> <lockfile>

    # Drift-Diff anzeigen (alle Aenderungen seit letztem Lockfile):
    python3 scripts/mc_mod_lockfile.py diff <mods_dir> <lockfile>

Exit-Codes:
    0 — Verify OK, kein Drift
    1 — Verify FAIL: Mod hat anderen Hash, neue Mod ohne Eintrag, oder
        Lockfile-Eintrag ohne Datei
    2 — Argument-Fehler oder I/O-Problem

CSV-Format:
    filename,sha256,size_bytes
    examplemod-1.0.0.jar,a3f8...,123456
"""

from __future__ import annotations

import csv
import hashlib
import sys
from pathlib import Path
from typing import Iterator


CHUNK_SIZE = 64 * 1024  # 64 KB chunks fuer streaming-hash, RAM-freundlich


def _hash_file(path: Path) -> str:
    """SHA256-Hex-Digest eines Files via Streaming-Read."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _iter_mods(mods_dir: Path) -> Iterator[Path]:
    """Iteriert alle .jar (case-insensitive) im mods-Verzeichnis."""
    for p in sorted(mods_dir.iterdir()):
        if p.is_file() and p.suffix.lower() == ".jar":
            yield p


def cmd_generate(mods_dir: Path) -> int:
    """Schreibt CSV-Lockfile fuer alle .jar in mods_dir nach stdout."""
    if not mods_dir.is_dir():
        print(f"FEHLER: kein Verzeichnis: {mods_dir}", file=sys.stderr)
        return 2

    writer = csv.writer(sys.stdout)
    writer.writerow(["filename", "sha256", "size_bytes"])

    count = 0
    for jar in _iter_mods(mods_dir):
        size = jar.stat().st_size
        h = _hash_file(jar)
        writer.writerow([jar.name, h, size])
        count += 1

    print(f"# Generated {count} mod entries", file=sys.stderr)
    return 0


def _load_lockfile(lockfile: Path) -> dict[str, tuple[str, int]]:
    """Liest CSV-Lockfile in Dict {filename: (sha256, size)}."""
    result: dict[str, tuple[str, int]] = {}
    with open(lockfile, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                result[row["filename"]] = (row["sha256"], int(row["size_bytes"]))
            except (KeyError, ValueError) as e:
                print(f"WARNUNG: kaputter Lockfile-Eintrag {row}: {e}", file=sys.stderr)
    return result


def cmd_verify(mods_dir: Path, lockfile: Path) -> int:
    """Prueft alle Mods gegen Lockfile. Exit 1 bei Drift."""
    if not mods_dir.is_dir():
        print(f"FEHLER: kein Verzeichnis: {mods_dir}", file=sys.stderr)
        return 2
    if not lockfile.is_file():
        print(f"FEHLER: Lockfile nicht gefunden: {lockfile}", file=sys.stderr)
        return 2

    expected = _load_lockfile(lockfile)
    actual_seen: set[str] = set()
    mismatches: list[str] = []

    for jar in _iter_mods(mods_dir):
        actual_seen.add(jar.name)
        if jar.name not in expected:
            mismatches.append(f"NEW    {jar.name} (nicht im Lockfile)")
            continue

        exp_hash, exp_size = expected[jar.name]
        actual_size = jar.stat().st_size
        if actual_size != exp_size:
            mismatches.append(
                f"SIZE   {jar.name} (erwartet {exp_size}, ist {actual_size})"
            )
            continue

        actual_hash = _hash_file(jar)
        if actual_hash != exp_hash:
            mismatches.append(
                f"HASH   {jar.name} (erwartet {exp_hash[:16]}..., ist {actual_hash[:16]}...)"
            )

    # Files im Lockfile aber nicht im mods/ (ohne -Match-Logik)
    missing = set(expected.keys()) - actual_seen
    for name in sorted(missing):
        mismatches.append(f"MISSING {name} (im Lockfile, fehlt im mods/)")

    if mismatches:
        print(f"VERIFY FAIL — {len(mismatches)} Abweichungen:", file=sys.stderr)
        for m in mismatches:
            print(f"  {m}", file=sys.stderr)
        return 1

    print(f"VERIFY OK ({len(actual_seen)} mods)", file=sys.stderr)
    return 0


def cmd_diff(mods_dir: Path, lockfile: Path) -> int:
    """Wie verify, aber exit 0 immer + nur Drift-Liste."""
    rc = cmd_verify(mods_dir, lockfile)
    # Drift OK fuer Diff-Mode — Exit-Code immer 0
    return 0 if rc <= 1 else rc


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    cmd = argv[1]
    if cmd == "generate" and len(argv) >= 3:
        return cmd_generate(Path(argv[2]))
    if cmd == "verify" and len(argv) >= 4:
        return cmd_verify(Path(argv[2]), Path(argv[3]))
    if cmd == "diff" and len(argv) >= 4:
        return cmd_diff(Path(argv[2]), Path(argv[3]))

    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

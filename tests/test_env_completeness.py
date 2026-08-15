#!/usr/bin/env python3
"""
Test-Skript: Vollstaendigkeitspruefung der .env.example

Vergleicht alle im Code verwendeten Umgebungsvariablen mit den in
config/.env.example definierten Variablen und gibt Abweichungen aus.

Nutzung:
    python tests/test_env_completeness.py

Rueckgabecodes:
    0  -- Alles in Ordnung, keine Abweichungen
    1  -- Abweichungen gefunden (fehlend oder ungenutzt)
"""

import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = PROJECT_ROOT / "config" / ".env.example"
SCAN_DIRS = ["bots", "cogs", "modules", "utils", "web"]

# ---------------------------------------------------------------------------
# Regulaere Ausdruecke fuer Env-Zugriffe im Code
# ---------------------------------------------------------------------------
# Erfasst:
#   get_env("KEY"  /  get_env('KEY'
#   os.getenv("KEY"  /  os.getenv('KEY'
#   os.environ["KEY"]  /  os.environ['KEY']
#   os.environ.get("KEY"  /  os.environ.get('KEY'
PATTERNS = [
    # get_env("VAR_NAME" ...) -- das eigene Hilfsmittel
    re.compile(r"""get_env\(\s*["']([A-Z_][A-Z0-9_]*)["']"""),
    # os.getenv("VAR_NAME" ...)
    re.compile(r"""os\.getenv\(\s*["']([A-Z_][A-Z0-9_]*)["']"""),
    # os.environ["VAR_NAME"] oder os.environ['VAR_NAME']
    re.compile(r"""os\.environ\[\s*["']([A-Z_][A-Z0-9_]*)["']\s*\]"""),
    # os.environ.get("VAR_NAME" ...)
    re.compile(r"""os\.environ\.get\(\s*["']([A-Z_][A-Z0-9_]*)["']"""),
    # server_ids("VAR_NAME", ...) -- die Server-Registry (utils.config), seit
    # 2026-08-14. Ohne dieses Muster meldet der Test SAT_SERVER_IDS als
    # ungenutzt, obwohl recon_bot damit die Instanzen aufbaut.
    re.compile(r"""server_ids\(\s*["']([A-Z_][A-Z0-9_]*)["']"""),
]

# Dynamische Variablen werden via f-String konstruiert (z.B. f"MC_{sid}_SERVICE").
# Diese koennen nicht statisch extrahiert werden und werden daher ignoriert.
# Das Muster erkennt get_env(f"...) und aehnliche Aufrufe.
DYNAMIC_PATTERNS = [
    re.compile(r"""get_env\(\s*f["']"""),
    re.compile(r"""os\.getenv\(\s*f["']"""),
    re.compile(r"""os\.environ\[\s*f["']"""),
    re.compile(r"""os\.environ\.get\(\s*f["']"""),
]

# Bekannte dynamische Prefixe  --  Variablen, die per f-String erzeugt
# werden (z.B. MC_{ID}_SERVICE).  Wenn eine .env.example-Variable zu
# einem dieser Muster passt, wird sie als "dynamisch genutzt" gewertet.
DYNAMIC_PREFIX_PATTERNS = [
    re.compile(r"^MC_[A-Z0-9]+_"),   # MC_BMC_*, MC_VANILLA_*, ...
    re.compile(r"^SAT_[A-Z0-9]+_"),  # SAT_MAIN_*, SAT_SECOND_*, ...
    # Die alten Satisfactory-Namen ohne ID liest SatisfactoryServer seit
    # 2026-08-14 als Rueckfall des ersten Servers — ueber einen Parameter,
    # nicht als Literal. Statisch ist das nicht mehr sichtbar, benutzt werden
    # sie trotzdem.
    re.compile(r"^SATISFACTORY_"),
]


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def find_python_files() -> list[Path]:
    """Alle .py-Dateien in den relevanten Verzeichnissen finden."""
    files: list[Path] = []
    for dirname in SCAN_DIRS:
        scan_dir = PROJECT_ROOT / dirname
        if scan_dir.is_dir():
            files.extend(scan_dir.rglob("*.py"))
    return sorted(files)


def extract_env_vars_from_code(files: list[Path]) -> dict[str, list[str]]:
    """Statische Env-Variablennamen aus allen Dateien extrahieren.

    Returns:
        dict mit Variablenname -> Liste der Dateien, in denen sie vorkommt.
    """
    result: dict[str, list[str]] = {}
    dynamic_count = 0

    for filepath in files:
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        rel = filepath.relative_to(PROJECT_ROOT)

        # Statische Aufrufe
        for pattern in PATTERNS:
            for match in pattern.finditer(content):
                var_name = match.group(1)
                result.setdefault(var_name, []).append(str(rel))

        # Dynamische Aufrufe zaehlen (zur Info)
        for pattern in DYNAMIC_PATTERNS:
            dynamic_count += len(pattern.findall(content))

    return result, dynamic_count


def extract_env_vars_from_example(env_file: Path) -> dict[str, int]:
    """Alle definierten Variablen aus .env.example extrahieren.

    Returns:
        dict mit Variablenname -> Zeilennummer
    """
    result: dict[str, int] = {}
    var_pattern = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=")
    try:
        lines = env_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return result

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = var_pattern.match(stripped)
        if m:
            result[m.group(1)] = lineno

    return result


def is_dynamic_prefix_match(var_name: str) -> bool:
    """Prueft ob eine Variable zu einem dynamischen Prefix-Muster passt."""
    return any(p.match(var_name) for p in DYNAMIC_PREFIX_PATTERNS)


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 64)
    print("  Env-Vollstaendigkeitspruefung")
    print("=" * 64)
    print()

    # --- .env.example pruefen ---
    if not ENV_EXAMPLE.exists():
        print(f"FEHLER: {ENV_EXAMPLE} nicht gefunden!")
        return 1

    env_example_vars = extract_env_vars_from_example(ENV_EXAMPLE)
    print(f"  .env.example:  {len(env_example_vars)} Variablen gefunden")
    print(f"  Pfad:          {ENV_EXAMPLE}")
    print()

    # --- Code-Dateien scannen ---
    py_files = find_python_files()
    print(f"  Python-Dateien: {len(py_files)} in {', '.join(SCAN_DIRS)}")

    code_vars, dynamic_count = extract_env_vars_from_code(py_files)
    print(f"  Statische Env-Zugriffe: {len(code_vars)} eindeutige Variablen")
    print(f"  Dynamische Env-Zugriffe: {dynamic_count} (f-String, nicht pruefbar)")
    print()

    # --- Vergleich ---
    code_set = set(code_vars.keys())
    example_set = set(env_example_vars.keys())

    missing_in_example = code_set - example_set
    unused_in_code = example_set - code_set

    # Dynamische MC_*-Variablen aus "unused" herausfiltern, da sie per
    # f-String im Code konstruiert werden und daher nicht statisch erkannt
    # werden koennen.
    truly_unused = {v for v in unused_in_code if not is_dynamic_prefix_match(v)}
    dynamically_used = unused_in_code - truly_unused

    # --- Ergebnisse ---
    has_issues = False

    print("-" * 64)
    print("  ERGEBNIS")
    print("-" * 64)
    print()

    # 1) Im Code genutzt, fehlt in .env.example
    if missing_in_example:
        has_issues = True
        print(f"  [FEHLT] {len(missing_in_example)} Variable(n) im Code genutzt,")
        print(f"          aber NICHT in .env.example definiert:\n")
        for var in sorted(missing_in_example):
            locations = sorted(set(code_vars[var]))
            print(f"    - {var}")
            for loc in locations:
                print(f"        -> {loc}")
        print()
    else:
        print("  [OK] Alle im Code genutzten Variablen sind in .env.example vorhanden.")
        print()

    # 2) In .env.example, aber nirgends im Code
    if truly_unused:
        has_issues = True
        print(f"  [UNGENUTZT] {len(truly_unused)} Variable(n) in .env.example,")
        print(f"              aber NICHT im Code verwendet:\n")
        for var in sorted(truly_unused):
            lineno = env_example_vars[var]
            print(f"    - {var}  (Zeile {lineno})")
        print()
    else:
        print("  [OK] Keine ungenutzten Variablen in .env.example (statisch).")
        print()

    # 3) Dynamisch erkannte (Hinweis, kein Fehler)
    if dynamically_used:
        print(f"  [INFO] {len(dynamically_used)} Variable(n) in .env.example werden")
        print(f"         vermutlich dynamisch per f-String erzeugt (MC_*-Prefix):\n")
        for var in sorted(dynamically_used):
            lineno = env_example_vars[var]
            print(f"    - {var}  (Zeile {lineno})")
        print()

    # --- Zusammenfassung ---
    print("=" * 64)
    if not has_issues:
        print("  ERGEBNIS: Alles in Ordnung -- keine Abweichungen gefunden.")
    else:
        total = len(missing_in_example) + len(truly_unused)
        print(f"  ERGEBNIS: {total} Abweichung(en) gefunden -- bitte pruefen.")
    print("=" * 64)

    return 1 if has_issues else 0


if __name__ == "__main__":
    sys.exit(main())

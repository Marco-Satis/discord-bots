#!/usr/bin/env python3
"""
Statische Cog-Analyse fuer das Discord Bot Projekt.

Prueft ohne Discord-Dependencies:
  1. Alle .py Dateien in cogs/ finden
  2. Ob jede Cog eine `async def setup(bot` Funktion hat
  3. Welche Cogs in welchem Bot geladen werden (load_extension)
  4. Doppelte Command-Namen ueber alle Cogs
  5. Zusammenfassung ausgeben

Ausfuehren:  python tests/test_cogs.py
"""

import re
import sys
from pathlib import Path
from collections import defaultdict

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COGS_DIR = PROJECT_ROOT / "cogs"
BOTS_DIR = PROJECT_ROOT / "bots"

BOT_FILES = {
    "gameserver_bot": BOTS_DIR / "gameserver_bot.py",
    "monitor_bot":    BOTS_DIR / "monitor_bot.py",
    "admin_bot":      BOTS_DIR / "admin_bot.py",
}

# ---------------------------------------------------------------------------
# Regex-Patterns
# ---------------------------------------------------------------------------

# async def setup(bot  -- mit oder ohne Type-Hint
RE_SETUP_FUNC = re.compile(
    r'^\s*async\s+def\s+setup\s*\(\s*bot\b',
    re.MULTILINE,
)

# load_extension("cogs.xxx") oder load_extension('cogs.xxx')
# Deckt ab:
#   await bot.load_extension("cogs.xxx")
#   await bot.load_extension('cogs.xxx')
#   "cogs.xxx"  in einer Liste (INITIAL_COGS = [...])
RE_LOAD_EXTENSION = re.compile(
    r'''load_extension\(\s*["']cogs\.(\w+)["']\s*\)''',
)

# Cog-Referenzen in Listen wie INITIAL_COGS = ["cogs.xxx", ...]
RE_COG_IN_LIST = re.compile(
    r'''["']cogs\.(\w+)["']''',
)

# .command(name="xxx") -- einzeilig
RE_COMMAND_INLINE = re.compile(
    r'''\.command\(\s*name\s*=\s*["'](\w+)["']''',
)

# .command(\n  name="xxx" -- mehrzeilig (name= auf Folgezeile)
RE_COMMAND_MULTILINE_START = re.compile(
    r'''\.command\(\s*$''',
)
RE_COMMAND_NAME_LINE = re.compile(
    r'''^\s*name\s*=\s*["'](\w+)["']''',
)

# app_commands.Group(name="xxx", parent=yyy ...) fuer Command-Gruppen
# Einzeilig: var = app_commands.Group(name="xxx", parent=yyy, ...)
RE_GROUP_DEF = re.compile(
    r'''(\w+)\s*=\s*app_commands\.Group\(''',
)
RE_GROUP_NAME_ATTR = re.compile(
    r'''name\s*=\s*["'](\w+)["']''',
)
RE_GROUP_PARENT_ATTR = re.compile(
    r'''parent\s*=\s*(\w+)''',
)

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def read_file(path: Path) -> str:
    """Datei lesen und Inhalt als String zurueckgeben."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"  FEHLER: Kann {path} nicht lesen: {e}")
        return ""


def find_cog_files() -> list[Path]:
    """Alle .py Dateien in cogs/ finden (ohne __init__.py und __pycache__)."""
    cog_files = sorted(
        p for p in COGS_DIR.glob("*.py")
        if p.name != "__init__.py"
    )
    return cog_files


def check_setup_functions(cog_files: list[Path]) -> dict[str, bool]:
    """Pruefen, ob jede Cog eine async def setup(bot ...) hat."""
    results = {}
    for cog_path in cog_files:
        content = read_file(cog_path)
        has_setup = bool(RE_SETUP_FUNC.search(content))
        # Key = Modulname ohne .py (z.B. "satisfactory_cog")
        results[cog_path.stem] = has_setup
    return results


def find_loaded_cogs_per_bot() -> dict[str, list[str]]:
    """
    Fuer jede Bot-Datei alle load_extension- bzw. INITIAL_COGS-Eintraege
    finden und zurueckgeben.

    Return: { "gameserver_bot": ["satisfactory_cog", "general_cog", ...], ... }
    """
    loaded = {}
    for bot_name, bot_path in BOT_FILES.items():
        if not bot_path.exists():
            print(f"  WARNUNG: Bot-Datei nicht gefunden: {bot_path}")
            loaded[bot_name] = []
            continue

        content = read_file(bot_path)
        cogs = set()

        # 1) Direkte load_extension-Aufrufe
        for m in RE_LOAD_EXTENSION.finditer(content):
            cogs.add(m.group(1))

        # 2) Cog-Referenzen in Listen (z.B. INITIAL_COGS)
        for m in RE_COG_IN_LIST.finditer(content):
            cogs.add(m.group(1))

        loaded[bot_name] = sorted(cogs)

    return loaded


def _parse_group_hierarchy(content: str) -> dict[str, str]:
    """
    Parst alle app_commands.Group()-Definitionen in einer Cog-Datei und
    baut eine Mapping-Tabelle: Python-Variablenname -> voller Slash-Pfad.

    Beispiel fuer satisfactory_cog.py:
        sat = app_commands.Group(name="sat", ...)
        players_grp = app_commands.Group(name="players", parent=sat, ...)

    Ergibt: {"sat": "sat", "players_grp": "sat.players"}
    """
    # Schritt 1: Alle Group-Definitionen sammeln (mehrzeilig-sicher)
    # Wir sammeln Bloecke: "var = app_commands.Group(...)"
    groups: dict[str, dict] = {}  # var_name -> {"name": ..., "parent_var": ...}

    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        gm = RE_GROUP_DEF.search(line)
        if gm:
            var_name = gm.group(1)
            # Sammle die gesamte Group()-Definition (kann mehrzeilig sein)
            block = line
            # Suche die schliessende Klammer
            depth = block.count("(") - block.count(")")
            j = i + 1
            while depth > 0 and j < len(lines):
                block += " " + lines[j]
                depth += lines[j].count("(") - lines[j].count(")")
                j += 1

            name_m = RE_GROUP_NAME_ATTR.search(block)
            parent_m = RE_GROUP_PARENT_ATTR.search(block)

            if name_m:
                groups[var_name] = {
                    "name": name_m.group(1),
                    "parent_var": parent_m.group(1) if parent_m else None,
                }
        i += 1

    # Schritt 2: Eltern-Ketten aufloesen -> voller Pfad
    resolved: dict[str, str] = {}

    def resolve(var_name: str) -> str:
        if var_name in resolved:
            return resolved[var_name]
        info = groups.get(var_name)
        if not info:
            return var_name  # Fallback: Variablenname
        if info["parent_var"] and info["parent_var"] in groups:
            parent_path = resolve(info["parent_var"])
            path = f"{parent_path}.{info['name']}"
        else:
            path = info["name"]
        resolved[var_name] = path
        return path

    for var_name in groups:
        resolve(var_name)

    return resolved


def find_commands_per_cog(cog_files: list[Path]) -> dict[str, list[dict]]:
    """
    Alle Command-Namen pro Cog finden (einzeilige und mehrzeilige Dekoratoren).

    Gibt zurueck: { "satisfactory_cog": [
        {"name": "status", "group": "sat", "full_path": "sat.status", "line": 74},
        ...
    ], ... }
    """
    commands_per_cog = {}

    for cog_path in cog_files:
        content = read_file(cog_path)
        lines = content.splitlines()
        cog_name = cog_path.stem
        cmds = []

        # Gruppen-Hierarchie parsen (var_name -> voller Slash-Pfad)
        group_paths = _parse_group_hierarchy(content)

        for line_no, line in enumerate(lines, 1):
            cmd_name = None
            decorator_line = line_no

            # --- Fall 1: name= steht auf derselben Zeile wie .command( ---
            cm = RE_COMMAND_INLINE.search(line)
            if cm:
                cmd_name = cm.group(1)

            # --- Fall 2: .command( am Zeilenende, name= auf Folgezeile ---
            elif RE_COMMAND_MULTILINE_START.search(line):
                # Naechste nicht-leere Zeile pruefen (bis zu 3 Zeilen voraus)
                for offset in range(1, 4):
                    if line_no - 1 + offset < len(lines):
                        next_line = lines[line_no - 1 + offset]
                        nm = RE_COMMAND_NAME_LINE.search(next_line)
                        if nm:
                            cmd_name = nm.group(1)
                            break

            if not cmd_name:
                continue

            # Gruppe erkennen anhand des Dekorators:
            #   @sat.command(         -> group_var = "sat"
            #   @players_grp.command( -> group_var = "players_grp"
            #   @app_commands.command( -> group_var = None (Top-Level)
            group_match = re.match(r'\s*@(\w+)\.command\(', line)
            group_var = None
            group_display = None
            full_path = cmd_name  # Default: nur Command-Name

            if group_match:
                raw_group = group_match.group(1)
                if raw_group != "app_commands":
                    group_var = raw_group
                    # Voller Pfad aus Hierarchie (z.B. "sat.players")
                    group_display = group_paths.get(group_var, group_var)
                    full_path = f"{group_display}.{cmd_name}"

            cmds.append({
                "name": cmd_name,
                "group": group_display,
                "full_path": full_path,
                "line": decorator_line,
            })

        commands_per_cog[cog_name] = cmds

    return commands_per_cog


def find_duplicate_commands(
    commands_per_cog: dict[str, list[dict]],
    loaded_per_bot: dict[str, list[str]],
) -> dict[str, list[tuple[str, int]]]:
    """
    Doppelte Top-Level-Command-Namen finden -- innerhalb jedes Bots.

    Commands in Sub-Gruppen (z.B. /sat status vs /mc status) sind kein
    Konflikt, weil sie verschiedene Gruppen-Praefix haben.
    Nur Top-Level app_commands.command(name=...) koennen kollidieren.

    Return: { "command_name": [("cog_name", line), ...], ... }
    """
    # Pro Bot die Top-Level Commands sammeln
    all_duplicates = {}

    for bot_name, cog_list in loaded_per_bot.items():
        # Nur Commands von Cogs die in diesem Bot geladen werden
        top_level_cmds: dict[str, list[tuple[str, int]]] = defaultdict(list)

        for cog_name in cog_list:
            cmds = commands_per_cog.get(cog_name, [])
            for cmd in cmds:
                # full_path enthaelt den aufgeloesten Slash-Pfad
                # z.B. "sat.players.ban" oder "help" (Top-Level)
                qualified = cmd["full_path"]

                top_level_cmds[qualified].append(
                    (cog_name, cmd["line"], bot_name)
                )

        for cmd_name, locations in top_level_cmds.items():
            if len(locations) > 1:
                if cmd_name not in all_duplicates:
                    all_duplicates[cmd_name] = []
                all_duplicates[cmd_name].extend(locations)

    # Auch bot-uebergreifend deduplizieren
    for key in all_duplicates:
        all_duplicates[key] = list(set(all_duplicates[key]))

    return all_duplicates


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("  STATISCHE COG-ANALYSE")
    print(f"  Projekt: {PROJECT_ROOT}")
    print("=" * 70)
    print()

    errors = 0
    warnings = 0

    # ------------------------------------------------------------------
    # 1. Alle Cog-Dateien finden
    # ------------------------------------------------------------------
    print("[1] Cog-Dateien in cogs/ suchen ...")
    cog_files = find_cog_files()
    print(f"    Gefunden: {len(cog_files)} Cog-Dateien")
    for f in cog_files:
        print(f"      - {f.name}")
    print()

    if not cog_files:
        print("  FEHLER: Keine Cog-Dateien gefunden!")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. setup()-Funktionen pruefen
    # ------------------------------------------------------------------
    print("[2] setup(bot) Funktionen pruefen ...")
    setup_results = check_setup_functions(cog_files)
    missing_setup = []
    for cog_name, has_setup in sorted(setup_results.items()):
        status = "OK" if has_setup else "FEHLT"
        if not has_setup:
            status = "FEHLT <<<<<"
            missing_setup.append(cog_name)
        print(f"    {cog_name:30s}  setup(): {status}")

    if missing_setup:
        errors += len(missing_setup)
        print(f"\n    !!! {len(missing_setup)} Cog(s) ohne setup()-Funktion!")
    else:
        print(f"\n    Alle {len(setup_results)} Cogs haben eine setup()-Funktion.")
    print()

    # ------------------------------------------------------------------
    # 3. Bot-Dateien lesen und load_extension-Aufrufe finden
    # ------------------------------------------------------------------
    print("[3] Bot-Dateien analysieren ...")
    loaded_per_bot = find_loaded_cogs_per_bot()
    all_loaded_cogs = set()
    for bot_name, cogs in loaded_per_bot.items():
        print(f"\n    {bot_name}:")
        if not cogs:
            print("      (keine Cogs gefunden)")
        for cog_name in cogs:
            print(f"      - cogs.{cog_name}")
            all_loaded_cogs.add(cog_name)
    print()

    # ------------------------------------------------------------------
    # 4. Vergleich: Cogs mit setup() vs. geladene Cogs
    # ------------------------------------------------------------------
    print("[4] Vergleich: setup() vorhanden vs. in Bot geladen ...")

    cog_names = set(setup_results.keys())

    # Cogs mit setup() die in keinem Bot geladen werden
    not_loaded = sorted(
        name for name in cog_names
        if setup_results[name] and name not in all_loaded_cogs
    )
    if not_loaded:
        warnings += len(not_loaded)
        print(f"\n    WARNUNG: {len(not_loaded)} Cog(s) mit setup() aber in keinem Bot geladen:")
        for name in not_loaded:
            print(f"      - {name}")

    # Cogs die in einem Bot geladen werden aber kein setup() haben
    loaded_without_setup = sorted(
        name for name in all_loaded_cogs
        if name in cog_names and not setup_results.get(name, False)
    )
    if loaded_without_setup:
        errors += len(loaded_without_setup)
        print(f"\n    FEHLER: {len(loaded_without_setup)} Cog(s) werden geladen, haben aber kein setup():")
        for name in loaded_without_setup:
            bots = [b for b, cl in loaded_per_bot.items() if name in cl]
            print(f"      - {name}  (in: {', '.join(bots)})")

    # Cogs die in Bot referenziert werden aber als Datei nicht existieren
    missing_files = sorted(
        name for name in all_loaded_cogs
        if name not in cog_names
    )
    if missing_files:
        errors += len(missing_files)
        print(f"\n    FEHLER: {len(missing_files)} Cog(s) werden geladen, aber Datei fehlt:")
        for name in missing_files:
            bots = [b for b, cl in loaded_per_bot.items() if name in cl]
            print(f"      - cogs/{name}.py  (referenziert in: {', '.join(bots)})")

    if not not_loaded and not loaded_without_setup and not missing_files:
        print("    Alles konsistent.")
    print()

    # ------------------------------------------------------------------
    # 5. Commands pro Cog finden
    # ------------------------------------------------------------------
    print("[5] Commands pro Cog analysieren ...")
    commands_per_cog = find_commands_per_cog(cog_files)
    total_commands = 0
    for cog_name in sorted(commands_per_cog.keys()):
        cmds = commands_per_cog[cog_name]
        if not cmds:
            continue
        total_commands += len(cmds)
        print(f"\n    {cog_name} ({len(cmds)} Commands):")
        for cmd in cmds:
            print(f"      /{cmd['full_path']}  (Zeile {cmd['line']})")

    print(f"\n    Gesamt: {total_commands} Commands in {len(cog_files)} Cogs")
    print()

    # ------------------------------------------------------------------
    # 6. Doppelte Command-Namen finden
    # ------------------------------------------------------------------
    print("[6] Doppelte Command-Namen pro Bot pruefen ...")
    duplicates = find_duplicate_commands(commands_per_cog, loaded_per_bot)

    if duplicates:
        warnings += len(duplicates)
        print(f"\n    WARNUNG: {len(duplicates)} potenziell doppelte Command-Namen gefunden:")
        for cmd_name, locations in sorted(duplicates.items()):
            print(f"\n      Command: /{cmd_name}")
            for cog_name, line, bot_name in sorted(locations):
                print(f"        -> {cog_name} (Zeile {line}, {bot_name})")
    else:
        print("    Keine doppelten Command-Namen gefunden.")
    print()

    # ------------------------------------------------------------------
    # 7. Zusammenfassung
    # ------------------------------------------------------------------
    print("=" * 70)
    print("  ZUSAMMENFASSUNG")
    print("=" * 70)
    print(f"  Cog-Dateien:       {len(cog_files)}")
    print(f"  Mit setup():       {sum(1 for v in setup_results.values() if v)}")
    print(f"  In Bots geladen:   {len(all_loaded_cogs)}")
    print(f"  Commands gesamt:   {total_commands}")
    print(f"  Bots analysiert:   {len(BOT_FILES)}")

    for bot_name, cogs in loaded_per_bot.items():
        cmd_count = sum(len(commands_per_cog.get(c, [])) for c in cogs)
        print(f"    {bot_name:20s} {len(cogs):2d} Cogs, {cmd_count:3d} Commands")

    print()
    print(f"  Fehler:    {errors}")
    print(f"  Warnungen: {warnings}")
    print("=" * 70)

    if errors > 0:
        print("\n  ERGEBNIS: FEHLER gefunden -- bitte beheben!")
        sys.exit(1)
    elif warnings > 0:
        print("\n  ERGEBNIS: Warnungen vorhanden -- bitte pruefen.")
        sys.exit(0)
    else:
        print("\n  ERGEBNIS: Alle Pruefungen bestanden.")
        sys.exit(0)


if __name__ == "__main__":
    main()

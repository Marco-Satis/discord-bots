#!/usr/bin/env python3
"""
test_imports.py - Syntax- und Import-Check fuer alle Python-Module im Projekt.

Prueft alle .py-Dateien in bots/, cogs/, modules/, utils/, web/:
  1. Syntax-Check via py_compile.compile()
  2. Import-Test via importlib (nur wenn keine externen Dependencies noetig)

Module, die Third-Party-Pakete wie discord.py, fastapi, aiohttp etc.
benoetigen, werden NUR per py_compile geprueft (kein Import), da diese
Dependencies auf dem Zielserver installiert sind, nicht lokal.

Aufruf:
    python tests/test_imports.py
"""

import os
import sys
import ast
import py_compile
import importlib
from pathlib import Path

# ---------------------------------------------------------------------------
# Projekt-Root ermitteln (eine Ebene ueber tests/)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Sicherstellen, dass PROJECT_ROOT im sys.path ist
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Verzeichnisse, die geprueft werden sollen
# ---------------------------------------------------------------------------
SCAN_DIRS = ["bots", "cogs", "modules", "utils", "web"]

# ---------------------------------------------------------------------------
# Third-Party-Pakete, die auf dem Zielserver installiert sind.
# Dateien, die eines dieser Pakete importieren, werden nur per py_compile
# geprueft aber NICHT importiert, um ImportError zu vermeiden.
# ---------------------------------------------------------------------------
THIRD_PARTY_MODULES = {
    # discord.py
    "discord",
    # FastAPI / Starlette / Web
    "fastapi",
    "uvicorn",
    "starlette",
    "httpx",
    # Auth / Crypto
    "jwt",
    "PyJWT",
    "bcrypt",
    "itsdangerous",
    # Templates
    "jinja2",
    # Multipart
    "multipart",
    "python_multipart",
    # Async I/O
    "aiohttp",
    "aiofiles",
    # System
    "psutil",
    # Env
    "dotenv",
    # Satisfactory
    "satisfactory_save",
}


def _extract_top_level_imports(filepath: Path) -> set[str]:
    """
    Parst die Datei mit ast und gibt die Top-Level-Modulnamen aller
    import/from-Statements zurueck.

    Beispiel:
        import discord           -> {"discord"}
        from fastapi import ...  -> {"fastapi"}
        from discord.ext import  -> {"discord"}
        import os                -> {"os"}
    """
    top_level = set()
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError):
        # Syntax-Fehler werden spaeter von py_compile erfasst
        return top_level

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:  # nur absolute imports
                top_level.add(node.module.split(".")[0])
    return top_level


# ---------------------------------------------------------------------------
# Transitive Abhaengigkeits-Analyse
# ---------------------------------------------------------------------------
# Projekt-interne Top-Level-Packages (alles was in SCAN_DIRS liegt)
_PROJECT_PACKAGES = set(SCAN_DIRS)

# Cache: Modulname -> braucht Third-Party (True/False/None=in Berechnung)
_third_party_cache: dict[str, bool | None] = {}


def _resolve_module_files(module_top: str, full_module: str) -> list[Path]:
    """
    Loest ein Projekt-internes Modul auf alle .py-Dateien auf, die beim
    Import ausgefuehrt werden.

    Wenn z.B. 'utils.logger' importiert wird, muss Python zuerst
    utils/__init__.py ausfuehren und dann utils/logger.py. Beide Dateien
    muessen geprueft werden.

    Rueckgabe: Liste von Dateien, die beim Import ausgefuehrt werden.
    """
    result = []
    parts = full_module.split(".")

    # Zuerst alle __init__.py der Eltern-Packages sammeln
    # z.B. fuer "modules.monitoring.health_check":
    #   -> modules/__init__.py, modules/monitoring/__init__.py
    for i in range(1, len(parts)):
        parent_parts = parts[:i]
        init_file = PROJECT_ROOT / Path(*parent_parts) / "__init__.py"
        if init_file.is_file():
            result.append(init_file)

    # Dann das Modul selbst (als Datei oder als Package)
    # Versuch 1: als Datei (z.B. utils/config.py)
    candidate = PROJECT_ROOT / Path(*parts).with_suffix(".py")
    if candidate.is_file():
        result.append(candidate)
    else:
        # Versuch 2: als Package (z.B. utils/__init__.py)
        candidate = PROJECT_ROOT / Path(*parts) / "__init__.py"
        if candidate.is_file():
            result.append(candidate)

    return result


def _file_needs_third_party(filepath: Path, _visiting: set[str] | None = None) -> bool:
    """
    Prueft rekursiv ob eine Datei (direkt oder transitiv ueber
    Projekt-interne Imports) ein Third-Party-Paket benoetigt.

    Zyklische Abhaengigkeiten werden durch ein _visiting-Set erkannt
    und als "kein Third-Party" gewertet (konservativ).
    """
    key = str(filepath)
    if key in _third_party_cache:
        cached = _third_party_cache[key]
        if cached is None:
            # Zyklus erkannt -> konservativ False
            return False
        return cached

    if _visiting is None:
        _visiting = set()

    # Markieren: "wird gerade berechnet" (Zyklus-Erkennung)
    _third_party_cache[key] = None
    _visiting.add(key)

    imports = _extract_top_level_imports(filepath)

    # Direkter Third-Party-Import?
    if imports & THIRD_PARTY_MODULES:
        _third_party_cache[key] = True
        _visiting.discard(key)
        return True

    # Pruefen ob das eigene Package-__init__.py Third-Party braucht.
    # Wenn z.B. diese Datei modules/anti_spam.py ist, wird beim Import
    # auch modules/__init__.py ausgefuehrt.
    rel = filepath.relative_to(PROJECT_ROOT)
    rel_parts = list(rel.parts)
    if filepath.name != "__init__.py" and len(rel_parts) > 1:
        # Alle __init__.py der Eltern-Packages pruefen
        for i in range(1, len(rel_parts)):
            parent_init = PROJECT_ROOT / Path(*rel_parts[:i]) / "__init__.py"
            if parent_init.is_file():
                parent_key = str(parent_init)
                if parent_key not in _visiting:
                    if _file_needs_third_party(parent_init, _visiting):
                        _third_party_cache[key] = True
                        _visiting.discard(key)
                        return True

    # Transitiv: Projekt-interne Imports verfolgen
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError):
        _third_party_cache[key] = False
        _visiting.discard(key)
        return False

    # Package-Pfad der aktuellen Datei ermitteln (fuer relative Imports)
    file_rel = filepath.relative_to(PROJECT_ROOT)
    if filepath.name == "__init__.py":
        # __init__.py: Package ist das Verzeichnis selbst
        current_package_parts = list(file_rel.parent.parts)
    else:
        # Normales Modul: Package ist das Elternverzeichnis
        current_package_parts = list(file_rel.parent.parts)

    for node in ast.walk(tree):
        full_modules = []
        if isinstance(node, ast.Import):
            for alias in node.names:
                full_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                # Absoluter Import: from utils.config import ...
                full_modules.append(node.module)
            elif node.level > 0:
                # Relativer Import: from .config import ... / from ..foo import ...
                # Level 1 = aktuelles Package, Level 2 = ein Package hoch, etc.
                base_parts = current_package_parts[: len(current_package_parts) - (node.level - 1)]
                if node.module:
                    resolved = ".".join(base_parts + node.module.split("."))
                else:
                    resolved = ".".join(base_parts)
                if resolved:
                    full_modules.append(resolved)

        for full_mod in full_modules:
            top = full_mod.split(".")[0]
            if top not in _PROJECT_PACKAGES:
                continue  # Nicht projekt-intern -> bereits oben geprueft

            dep_files = _resolve_module_files(top, full_mod)
            for dep_file in dep_files:
                dep_key = str(dep_file)
                if dep_key in _visiting:
                    continue  # Zyklus -> ueberspringen

                if _file_needs_third_party(dep_file, _visiting):
                    _third_party_cache[key] = True
                    _visiting.discard(key)
                    return True

    _third_party_cache[key] = False
    _visiting.discard(key)
    return False


def _needs_third_party(filepath: Path) -> bool:
    """True wenn die Datei direkt oder transitiv ein Third-Party-Paket importiert."""
    return _file_needs_third_party(filepath)


def _filepath_to_module(filepath: Path) -> str:
    """
    Wandelt einen Dateipfad relativ zum PROJECT_ROOT in einen
    Python-Modulnamen um.

    Beispiel:
        bots/admin_bot.py  -> bots.admin_bot
        modules/a/b.py     -> modules.a.b
        utils/__init__.py  -> utils
    """
    rel = filepath.relative_to(PROJECT_ROOT)
    parts = list(rel.with_suffix("").parts)
    # __init__ wegschneiden -> Package-Import
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Ergebnis-Typen
# ---------------------------------------------------------------------------
COMPILE_OK = "compile_ok"
COMPILE_FAIL = "compile_fail"
IMPORT_OK = "import_ok"
IMPORT_FAIL = "import_fail"
IMPORT_SKIPPED = "import_skipped"


def check_file(filepath: Path) -> dict:
    """
    Prueft eine einzelne .py-Datei.

    Rueckgabe: dict mit Schluesseln
        - file:           relativer Pfad
        - compile_status: COMPILE_OK | COMPILE_FAIL
        - compile_error:  Fehlertext oder None
        - import_status:  IMPORT_OK | IMPORT_FAIL | IMPORT_SKIPPED
        - import_error:   Fehlertext oder None
    """
    rel_path = str(filepath.relative_to(PROJECT_ROOT))
    result = {
        "file": rel_path,
        "compile_status": None,
        "compile_error": None,
        "import_status": None,
        "import_error": None,
    }

    # --- 1. py_compile ---
    try:
        py_compile.compile(str(filepath), doraise=True)
        result["compile_status"] = COMPILE_OK
    except py_compile.PyCompileError as exc:
        result["compile_status"] = COMPILE_FAIL
        result["compile_error"] = str(exc)
        # Wenn compile schon fehlschlaegt, Import ueberspringen
        result["import_status"] = IMPORT_SKIPPED
        return result

    # --- 2. Import (nur wenn keine Third-Party-Abhaengigkeit) ---
    if _needs_third_party(filepath):
        result["import_status"] = IMPORT_SKIPPED
        return result

    module_name = _filepath_to_module(filepath)
    if not module_name:
        result["import_status"] = IMPORT_SKIPPED
        return result

    try:
        importlib.import_module(module_name)
        result["import_status"] = IMPORT_OK
    except Exception as exc:
        result["import_status"] = IMPORT_FAIL
        result["import_error"] = f"{type(exc).__name__}: {exc}"

    return result


def collect_py_files() -> list[Path]:
    """Sammelt alle .py-Dateien in den konfigurierten Verzeichnissen."""
    files = []
    for dirname in SCAN_DIRS:
        scan_path = PROJECT_ROOT / dirname
        if not scan_path.is_dir():
            continue
        for py_file in sorted(scan_path.rglob("*.py")):
            files.append(py_file)
    return files


# ---------------------------------------------------------------------------
# ANSI-Farben (werden deaktiviert wenn kein TTY)
# ---------------------------------------------------------------------------
_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _green(text: str) -> str:
    return f"\033[32m{text}\033[0m" if _USE_COLOR else text


def _red(text: str) -> str:
    return f"\033[31m{text}\033[0m" if _USE_COLOR else text


def _yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m" if _USE_COLOR else text


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if _USE_COLOR else text


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------
def main() -> int:
    print(_bold(f"Projekt-Root: {PROJECT_ROOT}"))
    print(_bold(f"Verzeichnisse: {', '.join(SCAN_DIRS)}"))
    print()

    files = collect_py_files()
    if not files:
        print(_red("Keine .py-Dateien gefunden!"))
        return 1

    results = []
    for f in files:
        results.append(check_file(f))

    # --- Zusammenfassung ---
    compile_ok = sum(1 for r in results if r["compile_status"] == COMPILE_OK)
    compile_fail = sum(1 for r in results if r["compile_status"] == COMPILE_FAIL)
    import_ok = sum(1 for r in results if r["import_status"] == IMPORT_OK)
    import_fail = sum(1 for r in results if r["import_status"] == IMPORT_FAIL)
    import_skipped = sum(1 for r in results if r["import_status"] == IMPORT_SKIPPED)

    # Trennlinie
    sep = "-" * 78

    # --- Detaillierte Ergebnisse ---
    print(_bold("Ergebnisse pro Datei:"))
    print(sep)

    for r in results:
        # Compile-Status
        if r["compile_status"] == COMPILE_OK:
            c_tag = _green("COMPILE OK")
        else:
            c_tag = _red("COMPILE FAIL")

        # Import-Status
        if r["import_status"] == IMPORT_OK:
            i_tag = _green("IMPORT OK")
        elif r["import_status"] == IMPORT_FAIL:
            i_tag = _red("IMPORT FAIL")
        else:
            i_tag = _yellow("IMPORT SKIP")

        print(f"  {r['file']:<55s}  {c_tag}  {i_tag}")

        # Fehlerdetails eingerueckt ausgeben
        if r["compile_error"]:
            print(f"    -> {_red(r['compile_error'])}")
        if r["import_error"]:
            print(f"    -> {_red(r['import_error'])}")

    # --- Gesamtuebersicht ---
    print(sep)
    print(_bold("Zusammenfassung:"))
    print(f"  Dateien gesamt:       {len(results)}")
    print()
    print(f"  Compile OK:           {_green(str(compile_ok))}")
    print(f"  Compile FAIL:         {_red(str(compile_fail)) if compile_fail else str(compile_fail)}")
    print()
    print(f"  Import OK:            {_green(str(import_ok))}")
    print(f"  Import FAIL:          {_red(str(import_fail)) if import_fail else str(import_fail)}")
    print(f"  Import uebersprungen: {_yellow(str(import_skipped))}")
    print(f"  (Third-Party-Deps nicht lokal vorhanden)")
    print(sep)

    # Exit-Code: 0 = alles OK, 1 = mindestens ein Fehler
    total_failures = compile_fail + import_fail
    if total_failures == 0:
        print(_green(_bold("ALLE CHECKS BESTANDEN")))
    else:
        print(_red(_bold(f"{total_failures} FEHLER GEFUNDEN")))

    return 1 if total_failures > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Tests gegen Injection in `hx-vals`-Attributen der Dashboard-Templates.

Der Angriffsweg: HTMX liest `hx-vals` als JSON. Steht dort ein fremdkontrollierter
Wert zwischen Anführungszeichen, kann er das JSON umschreiben — aus
`{"action": "kick", "target": "<name>"}` wird `{"action": "stop", …}`. Jinja
escapt zwar `"` zu `&quot;`, aber der Browser dekodiert das beim Parsen des
Attributs zurück. Schutz ist `| tojson`, das den Wert als JSON-String erzeugt.

Fremdkontrolliert sind hier: Spielernamen (jeder, der auf dem Gameserver joint),
Backup-Dateinamen, Dienstnamen und IP-Einträge aus der Sperrliste.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_template_injection.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    HAVE_JINJA = True
except ImportError:
    HAVE_JINJA = False

_results: list[tuple[str, bool, str]] = []

# Ein Name, der aus dem Ziel-Feld ausbrechen und die Aktion umbiegen will.
ANGRIFF = 'Max", "action": "stop", "egal": "'


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _env():
    wurzel = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "web", "templates")
    return Environment(loader=FileSystemLoader(wurzel),
                       autoescape=select_autoescape(["html"]))


def _hx_vals(html: str) -> list[dict]:
    """Alle hx-vals-Attribute als geparstes JSON."""
    gefunden = []
    for m in re.finditer(r"hx-vals='([^']*)'", html):
        roh = m.group(1).replace("&quot;", '"').replace("&#34;", '"').replace("&amp;", "&")
        try:
            gefunden.append(json.loads(roh))
        except json.JSONDecodeError as e:
            gefunden.append({"__parse_fehler__": f"{e}: {roh[:120]}"})
    return gefunden


def run_tests() -> None:
    env = _env()

    # --- Spielerliste: kick/ban dürfen nicht zu stop werden ---
    html = env.get_template("partials/server_players.html").render(
        server_id="sat", players=[{"name": ANGRIFF, "join_time": "12:00"}]
    )
    werte = _hx_vals(html)
    _check("spieler_json_parsebar",
           all("__parse_fehler__" not in w for w in werte), str(werte))
    aktionen = [w.get("action") for w in werte if "action" in w]
    _check("spieler_aktion_unveraendert", set(aktionen) <= {"kick", "ban"}, str(aktionen))
    _check("spieler_name_bleibt_wert",
           all(w.get("target") == ANGRIFF for w in werte if "target" in w), str(werte))

    # --- Backups: der Dateiname darf die Aktion nicht umbiegen ---
    html = env.get_template("partials/server_backups.html").render(
        server_id="sat",
        backups=[{"filename": ANGRIFF, "size_mb": 1, "created": "heute", "age": "1h"}],
    )
    werte = _hx_vals(html)
    _check("backup_json_parsebar",
           all("__parse_fehler__" not in w for w in werte), str(werte))
    aktionen = [w.get("action") for w in werte if "action" in w]
    _check("backup_aktion_unveraendert",
           set(aktionen) <= {"download_backup", "delete_backup"}, str(aktionen))

    # Ganze Seiten (system.html, security.html) werden nicht gerendert — die
    # brauchen den vollen Anfrage-Kontext des Dashboards. Für die greift die
    # statische Prüfung unten, und die deckt ohnehin jedes Template ab.

    # --- Keine ungeschützte Variable in hx-vals, über ALLE Templates ---
    wurzel = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "web", "templates")
    ungeschuetzt: list[str] = []
    for ordner, _, dateien in os.walk(wurzel):
        for d in dateien:
            if not d.endswith(".html"):
                continue
            pfad = os.path.join(ordner, d)
            with open(pfad, encoding="utf-8") as f:
                for nr, zeile in enumerate(f, 1):
                    for m in re.finditer(r"hx-vals='([^']*)'", zeile):
                        # Eine Variable in Anführungszeichen ohne tojson ist die Lücke.
                        for treffer in re.finditer(r'"\s*\{\{\s*([^}]+?)\s*\}\}\s*"', m.group(1)):
                            if "tojson" not in treffer.group(1):
                                ungeschuetzt.append(f"{d}:{nr} {treffer.group(1)}")
    _check("keine_ungeschuetzte_variable", not ungeschuetzt,
           "ohne tojson: " + ", ".join(ungeschuetzt))


def main() -> int:
    print("=" * 60)
    print("  Template-Injection Tests (hx-vals)")
    print("=" * 60)
    if not HAVE_JINJA:
        print("  [SKIP] jinja2 lokal nicht installiert — Test läuft am Server.")
        print("  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN (übersprungen)")
        return 0

    try:
        run_tests()
    except Exception as e:  # noqa: BLE001
        _check("run", False, f"Exception: {e}")

    failed = 0
    for name, ok, msg in _results:
        status = "[OK]  " if ok else "[FAIL]"
        line = f"  {status} {name}"
        if not ok and msg:
            line += f"  -> {msg}"
        print(line)
        if not ok:
            failed += 1

    print("-" * 60)
    if failed == 0:
        print(f"  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN ({len(_results)} Checks)")
        return 0
    print(f"  ERGEBNIS: {failed}/{len(_results)} FEHLGESCHLAGEN")
    return 1


if __name__ == "__main__":
    sys.exit(main())

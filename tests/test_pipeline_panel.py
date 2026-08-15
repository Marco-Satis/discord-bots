#!/usr/bin/env python3
"""
Tests für das Pipeline-Control-Panel (cogs/pipeline_control_cog.py).

Umstellung 2026-08-13: Embed mit Feld „Live-Status" -> Components-V2-Panel.
Geprüft wird, was beim Umstieg brechen könnte: Signatur (sonst editiert der
Refresh-Loop entweder dauernd oder nie), custom_ids (sonst sterben die Buttons
alter Panels) und das Komponenten-Budget.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_pipeline_panel.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import discord  # noqa: F401
    from cogs import pipeline_control_cog as pcc
    from utils.panels import COMPONENT_LIMIT
    HAVE_DISCORD = True
except ImportError:
    HAVE_DISCORD = False

_results: list[tuple[str, bool, str]] = []

ERWARTETE_IDS = {
    "pipeline_ctl_start", "pipeline_ctl_stop", "pipeline_ctl_status",
    "pipeline_ctl_mode", "pipeline_ctl_sweepmode", "pipeline_ctl_daylimit",
}


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _status(**werte) -> dict:
    daten = {
        "paused": False, "pause_reason": "", "daemon": True,
        "modus": "normal", "sweep_mode": "text", "tageslimit": "an",
        "today": 12, "day_cap": 40, "window": 3, "window_cap": 10,
        "inbox_pending": 5, "keepers_left": 2,
        "last_finding": {"title": "Ein Fund", "category": "howto",
                         "started_at": "18:00"},
    }
    daten.update(werte)
    return daten


def _ids(view) -> set[str]:
    """custom_ids aller Buttons im Layout einsammeln."""
    gefunden: set[str] = set()

    def _lauf(elemente):
        for element in elemente or ():
            cid = getattr(element, "custom_id", None)
            if cid:
                gefunden.add(cid)
            for attribut in ("children", "items", "_children"):
                kinder = getattr(element, attribut, None)
                if kinder:
                    _lauf(kinder)

    _lauf(list(view.children))
    return gefunden


def run_tests() -> None:
    # 1. Signatur reagiert auf Inhalt, nicht auf die Uhr
    sig = pcc._panel_signature(_status())
    _check("signatur_stabil", sig == pcc._panel_signature(_status()), sig[:60])
    _check("signatur_reagiert_auf_pause",
           sig != pcc._panel_signature(_status(paused=True)))
    _check("signatur_reagiert_auf_zaehler",
           sig != pcc._panel_signature(_status(today=13)))
    _check("signatur_reagiert_auf_modus",
           sig != pcc._panel_signature(_status(modus="burst")))

    # 2. Inhalt: Zustand steuert Farbe, Kennzahlen stehen im Kopf
    zustand, kennzahlen, zeilen = pcc._panel_inhalt(_status())
    _check("zustand_aktiv_ok", zustand == "ok", zustand)
    _check("zustand_pausiert_crit", pcc._panel_inhalt(_status(paused=True))[0] == "crit")
    _check("kennzahl_heute", ("12/40", "heute") in kennzahlen, str(kennzahlen))
    _check("kennzahl_fenster", ("3/10", "im 5h-Fenster") in kennzahlen)
    _check("kennzahl_warteschlange", ("5", "in der Warteschlange") in kennzahlen)
    _check("zeile_zustand", zeilen[0].startswith("🟢"), zeilen[0])
    _check("zeile_pausiert_grund",
           "Wartung" in pcc._panel_inhalt(_status(paused=True, pause_reason="Wartung"))[2][0])
    _check("zeile_letzter_fund", any("Ein Fund" in z for z in zeilen), str(zeilen))
    _check("keine_warteschlange_kein_eintrag",
           all(l != "in der Warteschlange" for _, l in
               pcc._panel_inhalt(_status(inbox_pending=None))[1]))

    # 3. Das Panel selbst (LayoutView braucht einen laufenden Event-Loop)
    async def _panel_checks() -> None:
        view = pcc.ControlPanelView(live=_status())
        _check("panel_eine_top_komponente", len(view.to_components()) == 1)
        _check("panel_unter_limit", view._total_children <= COMPONENT_LIMIT,
               str(view._total_children))
        _check("panel_alle_buttons", _ids(view) == ERWARTETE_IDS, str(_ids(view)))
        _check("panel_persistent", view.timeout is None)
        # Kein is_persistent()-Check: bei LayoutView prueft der nur die oberste
        # Ebene, und ein Container meldet immer True — er kann also gar nicht
        # fehlschlagen. Aussagekraeftig ist, ob JEDER dispatchbare Button eine
        # custom_id traegt; das entscheidet ueber die Restart-Festigkeit.
        _check("panel_jeder_button_hat_id",
               all(getattr(b, "custom_id", None)
                   for b in view.walk_children()
                   if isinstance(b, discord.ui.Button)),
               "Button ohne custom_id im Layout")

        ohne_live = pcc.ControlPanelView(status_line="alles ruhig")
        _check("panel_ohne_live_baut", len(ohne_live.to_components()) == 1)
        _check("panel_ohne_live_buttons", _ids(ohne_live) == ERWARTETE_IDS)

    asyncio.run(_panel_checks())

    # 4. Panel-Erkennung: eigene Buttons werden auch verschachtelt gefunden
    knopf = SimpleNamespace(custom_id="pipeline_ctl_start")
    reihe = SimpleNamespace(children=[knopf])
    container = SimpleNamespace(children=[reihe])
    _check("erkennung_verschachtelt",
           pcc._ist_control_panel(SimpleNamespace(components=[container])))
    _check("erkennung_als_zubehoer",
           pcc._ist_control_panel(SimpleNamespace(
               components=[SimpleNamespace(children=[SimpleNamespace(accessory=knopf)])])))
    _check("erkennung_fremde_nachricht",
           not pcc._ist_control_panel(SimpleNamespace(
               components=[SimpleNamespace(children=[SimpleNamespace(custom_id="anderes")])])))
    _check("erkennung_ohne_komponenten",
           not pcc._ist_control_panel(SimpleNamespace(components=None)))

    # 5. Berechtigung: der Umbau hat die Button-Verdrahtung ausgetauscht — ein
    #    dabei verlorener UID-Check wäre durch nichts aufgefallen.
    async def _berechtigung() -> None:
        aufrufe: list = []
        echtes_run = pcc._run_control
        pcc._run_control = lambda *a, **k: aufrufe.append(a)
        alte_uid = os.environ.get("MARCO_DISCORD_UID")
        os.environ["MARCO_DISCORD_UID"] = "4711"
        try:
            abgewiesen: list = []

            class _Antwort:
                async def send_message(self, text, **kw):
                    abgewiesen.append((text, kw))

                async def defer(self, **kw):
                    abgewiesen.append(("defer", kw))

            fremder = SimpleNamespace(user=SimpleNamespace(id=999),
                                      response=_Antwort(), message=None)
            await pcc._handle_action(fremder, "start")
            _check("fremder_startet_nichts", aufrufe == [], str(aufrufe))
            _check("fremder_bekommt_absage",
                   any("Nur Marco" in str(a[0]) for a in abgewiesen), str(abgewiesen))
            _check("absage_ist_ephemeral",
                   all(a[1].get("ephemeral") for a in abgewiesen), str(abgewiesen))

            # Ohne konfigurierte UID wird JEDER abgewiesen, auch der Richtige.
            os.environ["MARCO_DISCORD_UID"] = "0"
            abgewiesen.clear()
            marco = SimpleNamespace(user=SimpleNamespace(id=4711),
                                    response=_Antwort(), message=None)
            await pcc._handle_action(marco, "start")
            _check("ohne_uid_niemand_durch", aufrufe == [], str(aufrufe))
            _check("ohne_uid_hinweis",
                   any("nicht konfiguriert" in str(a[0]) for a in abgewiesen),
                   str(abgewiesen))
        finally:
            pcc._run_control = echtes_run
            if alte_uid is None:
                os.environ.pop("MARCO_DISCORD_UID", None)
            else:
                os.environ["MARCO_DISCORD_UID"] = alte_uid

    asyncio.run(_berechtigung())


def main() -> int:
    print("=" * 60)
    print("  Pipeline-Panel Tests (cogs/pipeline_control_cog.py)")
    print("=" * 60)
    if not HAVE_DISCORD:
        print("  [SKIP] discord.py lokal nicht installiert — Test läuft am Server.")
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

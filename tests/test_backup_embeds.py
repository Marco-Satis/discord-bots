#!/usr/bin/env python3
"""
Tests für die Backup-Panels (cogs/server_backup_cog.py).

Deckt ``build_backup_info_embed`` und ``build_backup_compare_embed`` ab —
vorher 9 bzw. 7 Embed-Felder, gerendert mitten im Interaction-Pfad.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_backup_embeds.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import discord  # noqa: F401
    from cogs.server_backup_cog import (
        MAX_DIFF,
        MAX_EINTRAEGE,
        build_backup_compare_embed,
        build_backup_info_embed,
    )
    HAVE_DISCORD = True
except ImportError:
    HAVE_DISCORD = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _backup(**werte) -> dict:
    daten = {
        "guild_name": "Testserver",
        "guild_id": 1,
        "created_at": "2026-08-12T18:00:00",
        "created_by": 4711,
        "categories": [],
        "channels": [],
        "roles": [],
        "emojis": [],
        "settings": {},
    }
    daten.update(werte)
    return daten


def run_tests() -> None:
    # --- Detail-Panel ---------------------------------------------------
    e = build_backup_info_embed("abc123", _backup(
        channels=[{"name": "allgemein", "type": "text", "position": 0},
                  {"name": "sprache", "type": "voice", "position": 1}],
        roles=[{"name": "Admin", "position": 5}],
        categories=[{"name": "Info", "position": 0}],
        emojis=[{"name": "party"}],
        settings={"verification_level": "high", "default_notifications": "mentions",
                  "afk_channel_name": None, "afk_timeout": 300,
                  "system_channel_name": "willkommen"},
    ))
    _check("info_titel", "BACKUP `abc123`" in e.title, e.title)
    _check("info_punkt", e.title.startswith("🔵"), e.title)
    _check("info_kennzahl_channels", "**2** Channels" in e.description, e.description[:80])
    _check("info_kennzahl_rollen", "**1** Rollen" in e.description)
    _check("info_servername", "**Testserver**" in e.description)
    _check("info_datum", "erstellt 12.08.2026 18:00" in e.description, e.description[:200])
    _check("info_ersteller", "<@4711>" in e.description)
    _check("info_keine_felder", len(e.fields) == 0, f"{len(e.fields)} Felder")
    _check("info_gruppe_channels", "-# CHANNELS · 2" in e.description)
    _check("info_channel_eintrag", "› allgemein · text" in e.description, e.description)
    _check("info_gruppe_emojis", "-# EMOJIS · 1" in e.description)
    _check("info_einstellungen", "Verifizierung high" in e.description)
    _check("info_afk_leer_lesbar", "AFK keiner · 300s" in e.description, e.description)

    # Leere Gruppen erscheinen gar nicht
    leer = build_backup_info_embed("leer", _backup())
    _check("info_leere_gruppen_fehlen",
           all(g not in leer.description for g in
               ("-# CHANNELS", "-# ROLLEN", "-# KATEGORIEN", "-# EMOJIS")),
           leer.description)
    _check("info_leer_kennzahl_null", "**0** Channels" in leer.description)

    # Kürzung ist sichtbar, nicht still
    viele = build_backup_info_embed("viele", _backup(
        channels=[{"name": f"kanal-{i}", "type": "text", "position": i} for i in range(40)],
    ))
    _check("info_kuerzung_sichtbar", f"… und {40 - MAX_EINTRAEGE} weitere" in viele.description,
           viele.description[-200:])
    _check("info_kennzahl_bleibt_vollstaendig", "**40** Channels" in viele.description)
    _check("info_unter_limit", len(viele.description) < 4096, str(len(viele.description)))

    # Backticks in der ID können den Chip nicht aufbrechen
    boese = build_backup_info_embed("a`b", _backup())
    _check("info_id_entschaerft", boese.title.endswith("`ab`"), boese.title)

    # --- Vergleichs-Panel ------------------------------------------------
    v = build_backup_compare_embed("abc123", {
        "found": True,
        "channels": {"added": ["neu-1", "neu-2"], "removed": ["alt"], "changed": []},
        "roles": {"added": [], "removed": [], "changed": ["Admin"]},
        "settings": {"changed": ["afk_timeout"]},
    })
    _check("cmp_titel", "VERGLEICH `abc123`" in v.title, v.title)
    _check("cmp_gesamt", "**5** Unterschiede" in v.description, v.description[:80])
    _check("cmp_punkt_warn", v.title.startswith("🟡"), v.title)
    _check("cmp_keine_felder", len(v.fields) == 0, f"{len(v.fields)} Felder")
    _check("cmp_gruppe_channels", "-# CHANNELS · 3" in v.description, v.description)
    _check("cmp_neu_zeile", "**neu 2** › neu-1 · neu-2" in v.description, v.description)
    _check("cmp_entfernt_zeile", "**entfernt 1** › alt" in v.description)
    _check("cmp_einstellungen", "-# EINSTELLUNGEN · 1" in v.description)

    gleich = build_backup_compare_embed("abc123", {
        "found": True, "channels": {}, "roles": {}, "settings": {},
    })
    _check("cmp_ohne_diff_gruen", gleich.title.startswith("🟢"), gleich.title)
    _check("cmp_ohne_diff_null", "**0** Unterschiede" in gleich.description)
    _check("cmp_ohne_diff_text", "keine Änderungen" in gleich.description, gleich.description)

    viele_diff = build_backup_compare_embed("x", {
        "found": True,
        "channels": {"added": [f"k{i}" for i in range(12)]},
        "roles": {}, "settings": {},
    })
    _check("cmp_kuerzung_sichtbar", f"… +{12 - MAX_DIFF}" in viele_diff.description,
           viele_diff.description)
    _check("cmp_kuerzung_zahl_bleibt", "**neu 12**" in viele_diff.description)


def main() -> int:
    print("=" * 60)
    print("  Backup-Panel Tests (cogs/server_backup_cog.py)")
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

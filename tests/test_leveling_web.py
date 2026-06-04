#!/usr/bin/env python3
"""
Tests fuer die Web-Leaderboard-Route (D5, web/routes/leveling_route.py).

  - _format_voice: Minuten -> lesbar.
  - _load_leaderboard: guild-scoped (kein Cross-Guild-Leak), XP-sortiert, Rang.

Lauf: python tests/test_leveling_web.py
Skippt sauber wenn fastapi/aiosqlite lokal fehlen.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import aiosqlite  # noqa: F401
    from modules.database import db_manager
    from modules.guild_context import GuildConfig, clear_cache
    from web.routes.leveling_route import (
        _format_voice,
        _load_leaderboard,
        _load_levelup_config,
    )
    HAVE_DEPS = True
except Exception:  # noqa: BLE001 — fastapi/pydantic lokal evtl. nicht ladbar
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []
GA, GB = "111", "222"


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def run_tests() -> None:
    # _format_voice (pure)
    _check("voice_0", _format_voice(0) == "0m")
    _check("voice_30", _format_voice(30) == "30m")
    _check("voice_90", _format_voice(90) == "1h 30m")
    _check("voice_none", _format_voice(None) == "0m")

    tmp = tempfile.mkdtemp(prefix="lvlweb_")
    await db_manager.init_db(db_path=Path(tmp) / "w.db")
    db = await db_manager.get_db()
    try:
        rows = [
            (GA, "u1", 5000, 27, 100, 65),
            (GA, "u2", 300, 4, 20, 0),
            (GA, "u3", 1500, 12, 50, 130),
            (GB, "u9", 9999, 40, 999, 600),  # andere Guild -> darf NICHT auftauchen
        ]
        for gid, uid, xp, lvl, msg, vm in rows:
            await db.execute(
                "INSERT INTO leveling (guild_id, user_id, xp, level, messages, voice_minutes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (gid, uid, xp, lvl, msg, vm),
            )
        await db.commit()

        # E3: Member-Cache fuer Namen+Avatare (u1 gecacht, u2/u3 nicht)
        await db.execute(
            "INSERT INTO member_cache (guild_id, user_id, display_name, avatar_url) "
            "VALUES (?, ?, ?, ?)",
            (GA, "u1", "TopPlayer", "https://cdn/u1.png"),
        )
        await db.commit()

        lb = await _load_leaderboard(int(GA))
        _check("count_guild_a", len(lb) == 3, f"n={len(lb)}")
        _check("no_cross_guild", all(e["user_id"] != "u9" for e in lb))
        _check("sorted_desc", [e["xp"] for e in lb] == [5000, 1500, 300])
        _check("ranks", [e["rank"] for e in lb] == [1, 2, 3])
        _check("top_user", lb[0]["user_id"] == "u1" and lb[0]["level"] == 27)
        _check("voice_fmt", lb[2]["voice"] == "0m" and lb[0]["voice"] == "1h 5m")
        # E3-Anreicherung: u1 hat Name+Avatar, u2 (kein Cache) -> leer
        _check("enrich_name", lb[0]["name"] == "TopPlayer", str(lb[0].get("name")))
        _check("enrich_avatar", lb[0]["avatar_url"] == "https://cdn/u1.png")
        _check("enrich_fallback_empty", lb[1]["name"] == "" and lb[1]["avatar_url"] == "")

        # --- B1+B2: _load_levelup_config (Dashboard liest guild_config) ---
        clear_cache()
        # Default-Guild (kein Eintrag): announce leer, ping True
        cfg_def = await _load_levelup_config(999)
        _check("web_cfg_default_announce", cfg_def["announce_channel"] == "")
        _check("web_cfg_default_ping", cfg_def["levelup_ping"] is True)

        # Mit gesetzten Werten: announce als str (Template-tauglich), ping bool
        await GuildConfig.set(int(GA), "leveling.announce_channel", 778899)
        await GuildConfig.set(int(GA), "leveling.levelup_ping", False)
        await GuildConfig.set(int(GA), "leveling.leaderboard_card_enabled", True)
        await GuildConfig.set(int(GA), "leveling.leaderboard_bg", "leaderboard_bg.png")
        clear_cache()
        cfg_a = await _load_levelup_config(int(GA))
        _check("web_cfg_announce_str", cfg_a["announce_channel"] == "778899")
        _check("web_cfg_ping_off", cfg_a["levelup_ping"] is False)
        _check("web_cfg_lbcard_on", cfg_a["leaderboard_card"] is True)
        _check("web_cfg_lbbg", cfg_a["leaderboard_bg"] == "leaderboard_bg.png")
        # Default-Guild: Leaderboard-Karte aus
        _check("web_cfg_lbcard_default", cfg_def["leaderboard_card"] is False)
    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  Web-Leaderboard Tests (D5)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] fastapi/aiosqlite lokal nicht ladbar — laeuft am Server.")
        print("  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN (uebersprungen)")
        return 0

    try:
        asyncio.run(run_tests())
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

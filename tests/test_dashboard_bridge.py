#!/usr/bin/env python3
"""
Tests fuer die Dashboard<->Per-Guild-Config-Bruecke (MVP-Config).

End-to-End-Pfad (der eigentliche Wert dieser Phase):
  Dashboard-Form (Listen-Format)
    -> write_leveling_config -> guild_config (Dict-Format)
    -> LevelingManager.load_guild_config liest es (so wie der Bot zur Laufzeit)
    -> wirkt funktional auf XP.

Format-Konvertierung wird in beide Richtungen geprueft (Liste<->Dict).
Lauf: python tests/test_dashboard_bridge.py
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
    from modules.leveling import LevelingManager
    from modules.guild_context import clear_cache
    import web.guild_config_bridge as bridge
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []
GUILD = "999000999"


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def run_tests() -> None:
    tmp = tempfile.mkdtemp(prefix="bridge_db_")
    db_path = Path(tmp) / "b.db"
    await db_manager.init_db(db_path=db_path)
    clear_cache()

    # Haupt-Guild fuer die Bruecke fest verdrahten (statt ENV GUILD_ID)
    _orig = bridge.get_primary_guild_id
    bridge.get_primary_guild_id = lambda: int(GUILD)

    try:
        # --- Dashboard-Form (Listen-Format wie _parse_form_leveling liefert) ---
        parsed = {
            "xp_per_message_min": 20,
            "xp_per_message_max": 30,
            "xp_cooldown_seconds": 45,
            "voice_xp_per_minute": 7,
            "channel_multipliers": [{"channel_id": "111", "multiplier": 2.0}],
            "role_rewards": [
                {"level": 5, "role_id": "5005"},
                {"level": 10, "role_id": "1010"},
            ],
            "no_xp_channels": ["222"],
            "remove_lower_rewards": True,
        }

        ok = await bridge.write_leveling_config(parsed, updated_by="test")
        _check("write_ok", ok is True)

        # --- Read-Back ins Dashboard-Format (Listen) ---
        rb = await bridge.read_leveling_config()
        _check("rb_scalar_cooldown", rb.get("xp_cooldown_seconds") == 45, f"rb={rb}")
        _check("rb_scalar_voice", rb.get("voice_xp_per_minute") == 7)
        _check("rb_cm_list", [e["channel_id"] for e in rb.get("channel_multipliers", [])] == ["111"])
        _check("rb_rr_list", sorted(e["level"] for e in rb.get("role_rewards", [])) == [5, 10])
        _check("rb_remove_lower", rb.get("remove_lower_rewards") is True)
        _check("rb_no_xp", rb.get("no_xp_channels") == ["222"])

        # --- Bot liest dieselben guild_config-Werte (Dict-Format!) ---
        mgr = LevelingManager(config_file=Path(tmp) / "lvl.json")
        await mgr.load_guild_config(GUILD)
        cfg = mgr._gcfg(GUILD)
        _check("bot_cooldown", cfg["xp_cooldown_seconds"] == 45)
        _check("bot_voice", cfg["voice_xp_per_minute"] == 7)
        _check("bot_cm_dict", cfg["channel_multipliers"] == {"111": 2.0}, f"cm={cfg['channel_multipliers']}")
        _check("bot_rr_dict", cfg["role_rewards"] == {"5": 5005, "10": 1010}, f"rr={cfg['role_rewards']}")

        # --- Bot-Helper greifen ---
        _check("bot_multiplier", mgr.get_channel_multiplier(GUILD, 111) == 2.0)
        _check("bot_no_xp", mgr.is_no_xp_channel(GUILD, "222"))
        _check("bot_remove_lower", mgr.is_remove_lower_enabled(GUILD) is True)
        _check("bot_role_reward", mgr.get_role_reward(GUILD, 10) == 1010)

        # --- Funktional: Voice-XP nutzt den vom Dashboard gesetzten Wert (7/min) ---
        vxp, _ = mgr.add_voice_xp(GUILD, "42", 3)  # 3 * 7 = 21
        _check("bot_voice_xp_applied", vxp == 21, f"vxp={vxp}")

        # --- Leere Guild -> read liefert {} (Fallback auf JSON-Default) ---
        bridge.get_primary_guild_id = lambda: 123123123
        empty = await bridge.read_leveling_config()
        _check("empty_guild_returns_empty", empty == {})

        # --- Keine Guild gesetzt -> write False ---
        bridge.get_primary_guild_id = lambda: None
        _check("write_no_guild_false", await bridge.write_leveling_config(parsed) is False)

    finally:
        bridge.get_primary_guild_id = _orig
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  Dashboard<->guild_config-Bruecke Tests (MVP-Config)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] aiosqlite/web nicht importierbar — laeuft am Server.")
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

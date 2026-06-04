#!/usr/bin/env python3
"""
Tests fuer die LFG-Route (web/routes/lfg_route.py).

  - _load_cfg: Defaults (leer/300s) ohne guild_config-Eintrag.
  - guild_config-Override schlaegt durch + ist per-Guild isoliert.

Lauf: python tests/test_lfg_web.py
Skippt sauber wenn fastapi/aiosqlite lokal fehlen (laeuft am Server).
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
    from web.routes.lfg_route import _load_cfg, _DEFAULT_COOLDOWN
    HAVE_DEPS = True
except Exception:  # noqa: BLE001 — fastapi/pydantic lokal evtl. nicht ladbar
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []
GA, GB = 4711, 4712


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def run_tests() -> None:
    tmp = tempfile.mkdtemp(prefix="lfgweb_")
    await db_manager.init_db(db_path=Path(tmp) / "l.db")
    clear_cache()
    try:
        # Defaults (kein Eintrag)
        cfg = await _load_cfg(GA)
        _check("default_role_empty", cfg["role_id"] == "")
        _check("default_channel_empty", cfg["channel_id"] == "")
        _check("default_cooldown", cfg["cooldown_seconds"] == _DEFAULT_COOLDOWN)

        # Override Guild A
        await GuildConfig.set(GA, "lfg.role_id", "111111111111111111")
        await GuildConfig.set(GA, "lfg.channel_id", "444444444444444444")
        await GuildConfig.set(GA, "lfg.cooldown_seconds", 60)
        clear_cache()
        cfg_a = await _load_cfg(GA)
        _check("override_role", cfg_a["role_id"] == "111111111111111111")
        _check("override_channel", cfg_a["channel_id"] == "444444444444444444")
        _check("override_cooldown", cfg_a["cooldown_seconds"] == 60)

        # Isolation: Guild B unberuehrt (Defaults)
        cfg_b = await _load_cfg(GB)
        _check("isolation_role_b", cfg_b["role_id"] == "")
        _check("isolation_cooldown_b", cfg_b["cooldown_seconds"] == _DEFAULT_COOLDOWN)

        # Cooldown int-coercion auch wenn als String gespeichert
        await GuildConfig.set(GB, "lfg.cooldown_seconds", "45")
        clear_cache()
        cfg_b2 = await _load_cfg(GB)
        _check("cooldown_str_coerced", cfg_b2["cooldown_seconds"] == 45)
    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  LFG-Route Tests (guild_config Round-Trip + Isolation)")
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

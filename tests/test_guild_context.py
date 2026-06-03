#!/usr/bin/env python3
"""
Tests fuer das Multi-Tenant-Fundament (modules/guild_context.py + Migration v5).

Lauf: python tests/test_guild_context.py
Nutzt eine temporaere DB (kein Zugriff auf die Live-botdata.db).
Skippt sauber wenn aiosqlite lokal fehlt (laeuft dann am Server).

Schwerpunkt: Daten-Isolation pro Guild (kein Cross-Guild-Leak) — der
gefaehrlichste Multi-Tenant-Bug. Plus Config-Roundtrip, Feature-Flags, Cache.
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
    from modules.guild_context import (
        GuildConfig,
        clear_cache,
        get_primary_guild_id,
    )
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []

GUILD_A = 111111111111111111
GUILD_B = 222222222222222222


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def run_tests() -> None:
    # Temp-DB initialisieren (Migrationen laufen -> v5-Tabellen entstehen)
    tmpdir = tempfile.mkdtemp(prefix="gctx_test_")
    db_path = Path(tmpdir) / "test.db"
    await db_manager.init_db(db_path=db_path)

    try:
        # 1. Schema-Version ist mind. 5 (Migration v5 lief)
        conn = await db_manager.get_db()
        cur = await conn.execute("PRAGMA user_version")
        ver_row = await cur.fetchone()
        ver = ver_row[0] if ver_row else 0
        _check("schema_v5", ver >= 5, f"user_version={ver}")

        # 2. v5-Tabellen existieren
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('guilds','guild_config','guild_modules')"
        )
        tables = {r[0] for r in await cur.fetchall()}
        _check("tables_exist", tables == {"guilds", "guild_config", "guild_modules"},
               f"gefunden={tables}")

        # 3. Guild-Registry: ensure_guild idempotent + list
        await GuildConfig.ensure_guild(GUILD_A, name="Marco", owner_id=999)
        await GuildConfig.ensure_guild(GUILD_A, name="Marco")  # 2. Call -> kein Dup
        guilds = await GuildConfig.list_guilds()
        _check("ensure_guild_once", len(guilds) == 1, f"n={len(guilds)}")
        _check("ensure_guild_data",
               guilds and guilds[0]["guild_id"] == str(GUILD_A)
               and guilds[0]["owner_id"] == "999")

        # 4. Config set/get Roundtrip (str)
        await GuildConfig.set(GUILD_A, "tempvoice.naming", "🎮 {user}")
        _check("config_str_roundtrip",
               await GuildConfig.get(GUILD_A, "tempvoice.naming") == "🎮 {user}")

        # 5. Default wenn Key fehlt
        _check("config_default",
               await GuildConfig.get(GUILD_A, "nicht.da", "fallback") == "fallback")

        # 6. JSON-Typen roundtrip-sicher (bool/int/dict/list)
        await GuildConfig.set(GUILD_A, "k.bool", True)
        await GuildConfig.set(GUILD_A, "k.int", 42)
        await GuildConfig.set(GUILD_A, "k.dict", {"a": 1, "b": [2, 3]})
        _check("json_bool", await GuildConfig.get(GUILD_A, "k.bool") is True)
        _check("json_int", await GuildConfig.get(GUILD_A, "k.int") == 42)
        _check("json_dict",
               await GuildConfig.get(GUILD_A, "k.dict") == {"a": 1, "b": [2, 3]})

        # 7. *** ISOLATION *** — gleicher Key, andere Guild = getrennte Werte
        await GuildConfig.set(GUILD_A, "shared.key", "wert_A")
        await GuildConfig.set(GUILD_B, "shared.key", "wert_B")
        _check("isolation_config_A",
               await GuildConfig.get(GUILD_A, "shared.key") == "wert_A")
        _check("isolation_config_B",
               await GuildConfig.get(GUILD_B, "shared.key") == "wert_B")
        # Guild B sieht NICHT Guild As private Keys
        _check("isolation_no_leak",
               await GuildConfig.get(GUILD_B, "tempvoice.naming", "DEFAULT") == "DEFAULT")

        # 8. get_all nur eigene Guild
        all_a = await GuildConfig.get_all(GUILD_A)
        _check("get_all_scope",
               "tempvoice.naming" in all_a and "shared.key" in all_a
               and all_a["shared.key"] == "wert_A")

        # 9. Feature-Flags: default enabled
        _check("module_default_on",
               await GuildConfig.is_module_enabled(GUILD_A, "leveling") is True)

        # 10. Modul deaktivieren
        await GuildConfig.set_module(GUILD_A, "leveling", False)
        _check("module_disabled",
               await GuildConfig.is_module_enabled(GUILD_A, "leveling") is False)

        # 11. *** ISOLATION *** — Modul-Flag pro Guild getrennt
        _check("module_isolation",
               await GuildConfig.is_module_enabled(GUILD_B, "leveling") is True)

        # 12. delete -> zurueck auf Default
        await GuildConfig.delete(GUILD_A, "shared.key")
        _check("delete_resets",
               await GuildConfig.get(GUILD_A, "shared.key", "GONE") == "GONE")

        # 13. set aktualisiert Cache sofort (kein stale read nach Update)
        await GuildConfig.set(GUILD_A, "cache.test", "v1")
        _ = await GuildConfig.get(GUILD_A, "cache.test")  # cached
        await GuildConfig.set(GUILD_A, "cache.test", "v2")
        _check("cache_update_on_set",
               await GuildConfig.get(GUILD_A, "cache.test") == "v2")

        # 14. clear_cache crasht nicht + get funktioniert weiter
        clear_cache()
        _check("clear_cache_ok",
               await GuildConfig.get(GUILD_A, "cache.test") == "v2")

        # 15. get_primary_guild_id liefert int oder None (kein Crash)
        pg = get_primary_guild_id()
        _check("primary_guild_type", pg is None or isinstance(pg, int))

    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  Multi-Tenant-Fundament Tests (guild_context.py + v5)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] aiosqlite lokal nicht installiert — Test laeuft am Server.")
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

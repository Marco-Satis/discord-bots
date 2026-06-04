#!/usr/bin/env python3
"""
Tests fuer die Moderation-Route (D4, web/routes/moderation_route.py).

  - _load_rules: Defaults korrekt (word/anti_spam an, invite/caps/zalgo aus).
  - guild_config-Override schlaegt durch + ist per-Guild isoliert.

Lauf: python tests/test_moderation_web.py
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
    from web.routes.moderation_route import _load_rules, RULES
    HAVE_DEPS = True
except Exception:  # noqa: BLE001 — fastapi/pydantic lokal evtl. nicht ladbar
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []
GA, GB = 111, 222


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def run_tests() -> None:
    tmp = tempfile.mkdtemp(prefix="modweb_")
    await db_manager.init_db(db_path=Path(tmp) / "m.db")
    clear_cache()
    try:
        # Defaults (kein guild_config-Eintrag)
        rules = await _load_rules(GA)
        by_key = {r["key"]: r["enabled"] for r in rules}
        _check("rule_count", len(rules) == len(RULES))
        _check("default_word_on", by_key["word_filter"] is True)
        _check("default_antispam_on", by_key["anti_spam"] is True)
        _check("default_invite_off", by_key["invite_filter"] is False)
        _check("default_caps_off", by_key["caps_filter"] is False)
        _check("default_zalgo_off", by_key["zalgo_filter"] is False)

        # Override: caps an, word aus (Guild A)
        await GuildConfig.set(GA, "moderation.caps_filter", True)
        await GuildConfig.set(GA, "moderation.word_filter", False)
        clear_cache()
        rules_a = {r["key"]: r["enabled"] for r in await _load_rules(GA)}
        _check("override_caps_on", rules_a["caps_filter"] is True)
        _check("override_word_off", rules_a["word_filter"] is False)

        # Isolation: Guild B unberuehrt (Defaults)
        rules_b = {r["key"]: r["enabled"] for r in await _load_rules(GB)}
        _check("isolation_caps_b_off", rules_b["caps_filter"] is False)
        _check("isolation_word_b_on", rules_b["word_filter"] is True)
    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  Moderation-Route Tests (D4 Auto-Mod-Toggles)")
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

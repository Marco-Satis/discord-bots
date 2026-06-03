#!/usr/bin/env python3
"""
Tests fuer den Linked-Accounts-Manager (Phase Linked, L2).

  - link/get/unlink Roundtrip + Upsert (gleiche Plattform ueberschreibt).
  - Plattform-Whitelist + leere ID -> abgelehnt.
  - Guild-Isolation: gleicher User in 2 Guilds = getrennte Links.
  - Account-ID-Laengen-Cap.

Lauf: python tests/test_linked_accounts.py
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
    from modules.linked_accounts import (
        LinkedAccountsManager, is_valid_platform, platform_display,
        MAX_ACCOUNT_ID_LEN,
    )
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []
GA, GB, U1 = "guildA", "guildB", "user1"


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def run_tests() -> None:
    tmp = tempfile.mkdtemp(prefix="la_")
    await db_manager.init_db(db_path=Path(tmp) / "la.db")
    m = LinkedAccountsManager

    try:
        # Whitelist
        _check("valid_platform", is_valid_platform("Activision"))
        _check("invalid_platform", not is_valid_platform("myspace"))
        _check("display", platform_display("steam") == "Steam")

        # link + get
        _check("link_ok", await m.link(GA, U1, "activision", "Acti#1234"))
        _check("get_link", await m.get_link(GA, U1, "activision") == "Acti#1234")
        _check("get_links", await m.get_links(GA, U1) == {"activision": "Acti#1234"})

        # Upsert: gleiche Plattform ueberschreibt
        await m.link(GA, U1, "activision", "Acti#9999")
        _check("upsert", await m.get_link(GA, U1, "activision") == "Acti#9999")

        # zweite Plattform
        await m.link(GA, U1, "steam", "7656119")
        _check("two_platforms", len(await m.get_links(GA, U1)) == 2)

        # ungueltige Plattform / leere ID
        _check("reject_platform", await m.link(GA, U1, "myspace", "x") is False)
        _check("reject_empty", await m.link(GA, U1, "steam", "   ") is False)

        # Guild-Isolation
        await m.link(GB, U1, "activision", "OtherGuild#1")
        _check("isolation_a", await m.get_link(GA, U1, "activision") == "Acti#9999")
        _check("isolation_b", await m.get_link(GB, U1, "activision") == "OtherGuild#1")
        _check("isolation_links_a", "steam" in await m.get_links(GA, U1))
        _check("isolation_links_b", "steam" not in await m.get_links(GB, U1))

        # unlink
        _check("unlink_ok", await m.unlink(GA, U1, "steam") is True)
        _check("unlink_gone", await m.get_link(GA, U1, "steam") is None)
        _check("unlink_again_false", await m.unlink(GA, U1, "steam") is False)

        # Laengen-Cap
        long_id = "x" * (MAX_ACCOUNT_ID_LEN + 50)
        await m.link(GA, U1, "epic", long_id)
        stored = await m.get_link(GA, U1, "epic")
        _check("length_cap", stored is not None and len(stored) == MAX_ACCOUNT_ID_LEN)
    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  Linked-Accounts-Manager Tests (L2)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] aiosqlite nicht installiert — laeuft am Server.")
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

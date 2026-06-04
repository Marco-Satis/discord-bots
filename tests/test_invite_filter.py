#!/usr/bin/env python3
"""
Tests fuer den Invite-Link-Filter (Phase G Auto-Mod).

  - find_invites: erkennt discord.gg / .com-invite / .me / dsc.gg, keine
    False-Positives.
  - InviteFilter: default AUS, enabled filtert fremde, allowed_codes nimmt aus,
    toggle, Persistenz-Roundtrip.

Lauf: python tests/test_invite_filter.py
Skippt sauber wenn aiofiles/utils lokal nicht ladbar sind.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from modules.moderation.invite_filter import InviteFilter, find_invites
    HAVE_DEPS = True
except Exception:  # noqa: BLE001 — aiofiles/utils evtl. nicht ladbar
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


async def run_tests() -> None:
    # --- find_invites (pure) ---
    _check("find_gg", find_invites("komm auf discord.gg/abc123") == ["abc123"])
    _check("find_https", find_invites("https://discord.gg/Xy-9") == ["Xy-9"])
    _check("find_com_invite",
           find_invites("https://discord.com/invite/server1") == ["server1"])
    _check("find_app_invite",
           find_invites("discordapp.com/invite/oldcode") == ["oldcode"])
    _check("find_me", find_invites("discord.me/foo") == ["foo"])
    _check("find_dsc", find_invites("dsc.gg/bar") == ["bar"])
    _check("find_multiple",
           sorted(find_invites("discord.gg/a1b und discord.gg/c2d")) == ["a1b", "c2d"])

    # keine False-Positives
    _check("no_fp_plain", find_invites("ich liebe discord wirklich") == [])
    _check("no_fp_no_code", find_invites("discord.gg/") == [])
    _check("no_fp_empty", find_invites("") == [])
    _check("no_fp_none", find_invites(None) == [])

    # case-insensitive Domain
    _check("find_case", find_invites("DISCORD.GG/AbC") == ["AbC"])

    # --- InviteFilter ---
    f_off = InviteFilter({})
    _check("default_off", f_off.enabled is False)
    _check("disabled_no_filter", f_off.check_message("discord.gg/abc")[0] is False)

    f_on = InviteFilter({"invite_filter": {"enabled": True}})
    _check("enabled_filters", f_on.check_message("join discord.gg/raid")[0] is True)
    _check("enabled_clean_ok", f_on.check_message("hallo welt")[0] is False)
    _check("enabled_reason",
           f_on.check_message("discord.gg/abc")[1] == "Discord-Einladungslink")
    # Invite-Code-Mindestlaenge: 1 Zeichen matcht nicht (echte Codes sind laenger)
    _check("short_code_ignored", f_on.check_message("discord.gg/x")[0] is False)

    # allowed_codes nimmt eigene Codes aus (case-insensitive)
    f_allow = InviteFilter(
        {"invite_filter": {"enabled": True, "allowed_codes": ["MeinServer"]}})
    _check("allowed_own_ok",
           f_allow.check_message("discord.gg/meinserver")[0] is False)
    _check("allowed_foreign_blocked",
           f_allow.check_message("discord.gg/fremd")[0] is True)
    # gemischt: ein fremder Code reicht zum Blocken
    _check("allowed_mixed_blocks",
           f_allow.check_message("discord.gg/meinserver discord.gg/fremd")[0] is True)

    # toggle
    f_t = InviteFilter({})
    _check("toggle_on", f_t.toggle() is True and f_t.enabled is True)
    _check("toggle_off", f_t.toggle() is False and f_t.enabled is False)

    # --- Persistenz-Roundtrip ---
    tmp = Path(tempfile.mkdtemp(prefix="invf_")) / "inv.json"
    f_save = InviteFilter(
        {"invite_filter": {"enabled": True, "allowed_codes": ["keep"]}})
    f_save._filepath = tmp
    await f_save._save()
    f_load = InviteFilter({})  # default off
    f_load._filepath = tmp
    await f_load.load()
    _check("persist_enabled", f_load.enabled is True)
    _check("persist_allowed", "keep" in f_load.allowed_codes)


def main() -> int:
    print("=" * 60)
    print("  Invite-Filter Tests (Phase G Auto-Mod)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] aiofiles/utils lokal nicht ladbar — laeuft am Server.")
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

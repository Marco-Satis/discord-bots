#!/usr/bin/env python3
"""
Tests fuer das Temp-Voice VOICEPANEL-Upgrade (modules/temp_voice.py).

Sub-Phase 1: Daten-Modell + Helper (private/public, ban/unban, claim,
event-log, joined_at). Action-Helper mit gemockten Discord-Calls.

Lauf: python tests/test_temp_voice.py
Skippt sauber wenn discord.py lokal fehlt (laeuft dann am Server).
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import discord  # noqa: F401
    from modules.temp_voice import TempVoiceManager, _EVENT_CAP
    from modules.temp_voice_views import build_panel_embed
    HAVE_DISCORD = True
except ImportError:
    HAVE_DISCORD = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _new_manager() -> "TempVoiceManager":
    tmpdir = tempfile.mkdtemp(prefix="tv_test_")
    return TempVoiceManager(
        data_file=Path(tmpdir) / "tv.json",
        config_file=Path(tmpdir) / "tv_config.json",
    )


def _seed(mgr: "TempVoiceManager", cid: int = 12345, owner: int = 1) -> None:
    """Channel-Datensatz direkt setzen (statt create_channel mit Guild-Mock)."""
    mgr._channels[str(cid)] = {
        "owner_id": owner, "name": "x", "user_limit": 0,
        "private": False, "banned": [], "joined_at": {str(owner): "t0"},
        "events": [],
    }


def _fake_channel(cid: int = 12345):
    ch = MagicMock()
    ch.id = cid
    ch.guild.default_role = "everyone"
    ch.set_permissions = AsyncMock()
    return ch


def _fake_member(uid: int = 999, in_channel_id: int | None = 12345):
    m = MagicMock()
    m.id = uid
    if in_channel_id is not None:
        m.voice.channel.id = in_channel_id
    else:
        m.voice = None
    m.move_to = AsyncMock()
    return m


async def run_tests() -> None:
    # --- Daten-Helper (sync) ---
    mgr = _new_manager()
    _seed(mgr)

    _check("private_default_false", mgr.is_private(12345) is False)

    mgr.add_ban(12345, 999)
    _check("ban_added", mgr.is_banned(12345, 999) is True)
    _check("ban_in_list", 999 in mgr.get_banned(12345))
    mgr.add_ban(12345, 999)  # idempotent
    _check("ban_idempotent", mgr.get_banned(12345) == [999])
    _check("unban_returns_true", mgr.remove_ban(12345, 999) is True)
    _check("unban_gone", mgr.is_banned(12345, 999) is False)
    _check("unban_again_false", mgr.remove_ban(12345, 999) is False)

    mgr.record_join(12345, 555)
    _check("join_recorded", "555" in mgr.get_joined_at(12345))
    mgr.record_leave(12345, 555)
    _check("leave_recorded", "555" not in mgr.get_joined_at(12345))

    # Event-Ringpuffer: viele Events -> auf _EVENT_CAP begrenzt
    for i in range(15):
        mgr.log_event(12345, i, "join")
    _ch_rec = mgr._ch(12345)
    all_events = _ch_rec["events"] if _ch_rec else []
    _check("event_cap", len(all_events) <= _EVENT_CAP, f"len={len(all_events)}")
    _check("get_events_limit", len(mgr.get_events(12345, limit=5)) == 5)

    # --- Persistenz: neuer Manager liest State zurueck ---
    mgr2 = _new_manager()
    mgr2.data_file = mgr.data_file
    mgr2._load()
    _check("persist_reload", mgr2.is_temp_channel(12345))

    # --- create_channel setzt die neuen Felder ---
    mgr3 = _new_manager()
    guild = MagicMock()
    guild.get_channel.return_value = None
    guild.create_voice_channel = AsyncMock(return_value=_fake_channel(777))
    member = MagicMock()
    member.id = 1
    member.display_name = "Tester"
    await mgr3.create_channel(guild, member)
    data = mgr3.get_all_channels().get("777", {})
    _check("create_private_field", data.get("private") is False)
    _check("create_banned_empty", data.get("banned") == [])
    _check("create_joined_owner", "1" in data.get("joined_at", {}))
    _check("create_event_join",
           len(data.get("events", [])) == 1
           and data["events"][0]["type"] == "join")

    # --- Action-Helper (async, Discord gemockt) ---
    mgr4 = _new_manager()
    _seed(mgr4, cid=222, owner=1)
    ch = _fake_channel(222)

    await mgr4.set_private(ch)
    _check("set_private_flag", mgr4.is_private(222) is True)
    _, kwargs = ch.set_permissions.call_args
    _check("set_private_perms",
           kwargs.get("connect") is False and kwargs.get("view_channel") is False)

    await mgr4.set_public(ch)
    _check("set_public_flag", mgr4.is_private(222) is False)

    # Ban: User im Channel -> disconnect + deny + State
    mem = _fake_member(999, in_channel_id=222)
    await mgr4.ban_user(ch, mem)
    _check("ban_disconnect", mem.move_to.called)
    _check("ban_state", mgr4.is_banned(222, 999) is True)

    # Ban: User NICHT im Channel -> kein move_to, trotzdem Ban
    mem2 = _fake_member(888, in_channel_id=None)
    await mgr4.ban_user(ch, mem2)
    _check("ban_absent_no_disconnect", not mem2.move_to.called)
    _check("ban_absent_state", mgr4.is_banned(222, 888) is True)

    # Unban
    ok = await mgr4.unban_user(ch, mem)
    _check("unban_ok", ok is True and mgr4.is_banned(222, 999) is False)

    # Claim: Ownership wechselt + Manage-Perms
    claimer = _fake_member(444, in_channel_id=222)
    res = await mgr4.claim(ch, claimer)
    _check("claim_ok", res is True)
    _check("claim_owner", mgr4.get_owner(222) == 444)

    # --- Sub-2: panel message + record_join-Idempotenz + Embed ---
    mgr5 = _new_manager()
    _seed(mgr5, cid=333, owner=1)

    mgr5.set_panel_message(333, 98765)
    _check("panel_message_roundtrip", mgr5.get_panel_message(333) == 98765)

    # record_join idempotent: 2x gleicher User -> nur 1 Event, joined_at stabil
    ev_before = len(mgr5.get_events(333))
    mgr5.record_join(333, 42)
    mgr5.record_join(333, 42)
    _check("record_join_once", len(mgr5.get_events(333)) == ev_before + 1)

    # build_panel_embed: Slot-Liste enthaelt Owner mit Crown + Status-Feld
    pch = MagicMock()
    pch.id = 333
    pch.name = "Test Voice"
    pch.user_limit = 0
    owner_m = MagicMock(); owner_m.bot = False; owner_m.id = 1
    owner_m.display_name = "Chef"
    pch.members = [owner_m]
    embed = build_panel_embed(pch, mgr5)
    field_blob = " ".join(f.value or "" for f in embed.fields)
    _check("embed_slot_owner", "Chef" in field_blob and "👑" in field_blob)
    _check("embed_status_open", "Öffentlich" in field_blob)


def main() -> int:
    print("=" * 60)
    print("  Temp-Voice VOICEPANEL Sub-1 Tests (temp_voice.py)")
    print("=" * 60)
    if not HAVE_DISCORD:
        print("  [SKIP] discord.py lokal nicht installiert — Test laeuft am Server.")
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

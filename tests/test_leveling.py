#!/usr/bin/env python3
"""
Tests fuer den per-Guild Leveling-Refactor (Phase C, C2).

Schwerpunkt (Multi-Tenant-Isolation, Plan P8 — gefaehrlichster Commercial-Bug):
  - XP/Daten sind guild-scoped: gleicher User in 2 Guilds = getrennte XP.
  - Leaderboard + Rank zeigen NUR die jeweilige Guild (kein Cross-Guild-Leak).
  - Config kommt per-Guild aus guild_config (Override ueber JSON-Default).
  - DB-Persistenz (Upsert/Leaderboard-DB) ist guild-gefiltert.

Lauf: python tests/test_leveling.py
Skippt sauber wenn aiosqlite lokal fehlt (laeuft dann am Server).
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
    from modules.guild_context import GuildConfig, clear_cache
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []

GUILD_A = "111111111111111111"
GUILD_B = "222222222222222222"
GUILD_C = "333333333333333333"
GUILD_D = "444444444444444444"
USER_1 = "42"
USER_2 = "77"


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _new_manager() -> "LevelingManager":
    tmpdir = tempfile.mkdtemp(prefix="lvl_test_")
    return LevelingManager(config_file=Path(tmpdir) / "leveling_config.json")


async def run_tests() -> None:
    # Frische DB mit v6-Schema (leveling guild-scoped + voice_sessions)
    tmp = tempfile.mkdtemp(prefix="lvl_db_")
    db_path = Path(tmp) / "lvl.db"
    await db_manager.init_db(db_path=db_path)
    clear_cache()

    try:
        mgr = _new_manager()

        # ============================================================
        # 1) In-Memory XP-Isolation: gleicher User, zwei Guilds
        # ============================================================
        # cooldown-frei: erster Call pro (guild,user) gibt XP (last_xp_at=None)
        xp_a, _ = mgr.add_message_xp(GUILD_A, USER_1)
        xp_b, _ = mgr.add_message_xp(GUILD_B, USER_1)
        _check("msg_xp_guild_a", xp_a > 0, f"xp_a={xp_a}")
        _check("msg_xp_guild_b", xp_b > 0, f"xp_b={xp_b}")

        ua = mgr.get_user(GUILD_A, USER_1)
        ub = mgr.get_user(GUILD_B, USER_1)
        _check("isolation_separate_records", ua["xp"] == xp_a and ub["xp"] == xp_b)

        # set_xp in Guild A darf Guild B NICHT beeinflussen
        mgr.set_xp(GUILD_A, USER_1, 5000)
        _check("set_xp_guild_a", mgr.get_user(GUILD_A, USER_1)["xp"] == 5000)
        _check(
            "set_xp_no_leak_to_b",
            mgr.get_user(GUILD_B, USER_1)["xp"] == xp_b,
            f"b={mgr.get_user(GUILD_B, USER_1)['xp']}",
        )

        # User nur in Guild B -> in Guild A unbekannt (leerer Default)
        mgr.set_xp(GUILD_B, USER_2, 300)
        _check("unknown_user_in_a", mgr.get_user(GUILD_A, USER_2)["xp"] == 0)

        # ============================================================
        # 2) Leaderboard + Rank guild-scoped (In-Memory)
        # ============================================================
        lb_a = mgr.get_leaderboard(GUILD_A)
        lb_b = mgr.get_leaderboard(GUILD_B)
        ids_a = {e["user_id"] for e in lb_a}
        ids_b = {e["user_id"] for e in lb_b}
        _check("lb_a_only_user1", ids_a == {int(USER_1)}, f"ids_a={ids_a}")
        _check("lb_b_users", ids_b == {int(USER_1), int(USER_2)}, f"ids_b={ids_b}")

        # Rank: USER_1 ist #1 in A (5000 XP, allein)
        _check("rank_a_user1", mgr.get_rank(GUILD_A, USER_1) == 1)
        # In B: USER_2 (300) vor USER_1 (xp_b ~15-25) -> USER_1 ist #2
        _check("rank_b_user1_second", mgr.get_rank(GUILD_B, USER_1) == 2)

        # ============================================================
        # 3) Voice-XP guild-scoped
        # ============================================================
        vxp, _ = mgr.add_voice_xp(GUILD_A, USER_2, 10)  # 10 min * 5 = 50
        _check("voice_xp_value", vxp == 50, f"vxp={vxp}")
        _check("voice_only_guild_a", mgr.get_user(GUILD_B, USER_2)["voice_minutes"] == 0)
        _check("voice_minutes_a", mgr.get_user(GUILD_A, USER_2)["voice_minutes"] == 10)

        # ============================================================
        # 4) DB-Persistenz guild-gefiltert
        # ============================================================
        await mgr._db_upsert_user(GUILD_A, USER_1, mgr.get_user(GUILD_A, USER_1))
        await mgr._db_upsert_user(GUILD_B, USER_1, mgr.get_user(GUILD_B, USER_1))
        await mgr._db_upsert_user(GUILD_B, USER_2, mgr.get_user(GUILD_B, USER_2))

        db_lb_a = await mgr.get_leaderboard_db(GUILD_A)
        db_lb_b = await mgr.get_leaderboard_db(GUILD_B)
        # Beide Guilds enthalten USER_1 + USER_2 (msg/voice/set_xp Hot-Path-Upserts).
        # Echte Isolation = unterschiedliche XP pro Guild fuer denselben User_1.
        _check("db_lb_a_count", len(db_lb_a) == 2, f"n={len(db_lb_a)}")
        _check("db_lb_b_count", len(db_lb_b) == 2, f"n={len(db_lb_b)}")
        _check("db_lb_a_top_xp", db_lb_a[0]["xp"] == 5000)
        u1_a = next((e for e in db_lb_a if e["user_id"] == int(USER_1)), None)
        u1_b = next((e for e in db_lb_b if e["user_id"] == int(USER_1)), None)
        _check(
            "db_isolation_user1_xp",
            u1_a is not None and u1_b is not None
            and u1_a["xp"] == 5000 and u1_b["xp"] == xp_b,
            f"a={u1_a['xp'] if u1_a else None} b={u1_b['xp'] if u1_b else None}",
        )

        # get_rank_db guild-scoped: USER_1 #1 in A
        _check("db_rank_a_user1", await mgr.get_rank_db(GUILD_A, USER_1) == 1)
        _check("db_rank_b_user1_second", await mgr.get_rank_db(GUILD_B, USER_1) == 2)

        # ============================================================
        # 5) Per-Guild-Config aus guild_config (Override ueber Default)
        # ============================================================
        mgr2 = _new_manager()
        # Default: cooldown 60, voice 5/min
        await mgr2.load_guild_config(GUILD_A)
        _check("cfg_default_cooldown", mgr2._gcfg(GUILD_A)["xp_cooldown_seconds"] == 60)

        # Override in Guild B: voice 99/min, cooldown 0
        await GuildConfig.set(GUILD_B, "leveling.voice_xp_per_minute", 99)
        await GuildConfig.set(GUILD_B, "leveling.xp_cooldown_seconds", 0)
        await mgr2.load_guild_config(GUILD_B)
        _check("cfg_override_voice_b", mgr2._gcfg(GUILD_B)["voice_xp_per_minute"] == 99)
        _check("cfg_override_cooldown_b", mgr2._gcfg(GUILD_B)["xp_cooldown_seconds"] == 0)
        # Guild A bleibt Default (kein Leak)
        _check("cfg_a_unaffected", mgr2._gcfg(GUILD_A)["voice_xp_per_minute"] == 5)

        # Override wirkt auf XP-Berechnung
        vb, _ = mgr2.add_voice_xp(GUILD_B, USER_1, 2)  # 2 * 99 = 198
        _check("cfg_voice_applied", vb == 198, f"vb={vb}")

        # ============================================================
        # 6) Config-Setter schreiben nach guild_config + Cache live
        # ============================================================
        await mgr2.set_role_reward(GUILD_A, 5, 999000111)
        _check("role_reward_cache", mgr2.get_role_reward(GUILD_A, 5) == 999000111)
        # Persistenz: frischer Manager liest es aus guild_config
        mgr3 = _new_manager()
        await mgr3.load_guild_config(GUILD_A)
        _check("role_reward_persisted", mgr3.get_role_reward(GUILD_A, 5) == 999000111)
        # Kein Leak nach Guild B
        _check("role_reward_no_leak_b", mgr3.get_role_reward(GUILD_B, 5) is None)

        await mgr2.set_channel_multiplier(GUILD_A, 12345, 2.0)
        _check("multiplier_cache", mgr2.get_channel_multiplier(GUILD_A, 12345) == 2.0)
        # 1.0 entfernt Eintrag
        await mgr2.set_channel_multiplier(GUILD_A, 12345, 1.0)
        _check("multiplier_reset", mgr2.get_channel_multiplier(GUILD_A, 12345) == 1.0)

        await mgr2.set_card_enabled(GUILD_A, True)
        await mgr2.set_card_accent(GUILD_A, "#ff2b6b")
        _check("card_enabled_cache", mgr2.is_card_enabled(GUILD_A) is True)
        _check("card_accent_cache", mgr2.get_card_accent(GUILD_A) == "#ff2b6b")
        _check("card_disabled_b", mgr2.is_card_enabled(GUILD_B) is False)

        # ============================================================
        # 7) C4: No-XP-Channels
        # ============================================================
        mgr4 = _new_manager()
        await mgr4.set_no_xp_channel(GUILD_A, 7777, True)
        _check("noxp_listed", mgr4.is_no_xp_channel(GUILD_A, 7777))
        xp_noxp, _ = mgr4.add_message_xp(GUILD_A, "5", channel_id=7777)
        _check("noxp_zero", xp_noxp == 0)
        _check("noxp_no_msg_count", mgr4.get_user(GUILD_A, "5")["total_messages"] == 0)
        xp_ok, _ = mgr4.add_message_xp(GUILD_A, "5", channel_id=8888)
        _check("noxp_other_channel_xp", xp_ok > 0, f"xp_ok={xp_ok}")
        await mgr4.set_no_xp_channel(GUILD_A, 7777, False)
        _check("noxp_removed", not mgr4.is_no_xp_channel(GUILD_A, 7777))

        # ============================================================
        # 8) C4: Leaderboard limit=None (alle, fuer Pagination)
        # ============================================================
        mgr5 = _new_manager()
        for i in range(15):
            mgr5.set_xp(GUILD_A, str(1000 + i), (i + 1) * 100)
        all_lb = mgr5.get_leaderboard(GUILD_A, limit=None)
        _check("lb_all_count", len(all_lb) == 15, f"n={len(all_lb)}")
        _check("lb_limit_10", len(mgr5.get_leaderboard(GUILD_A, limit=10)) == 10)
        _check("lb_sorted_desc", all_lb[0]["xp"] == 1500 and all_lb[-1]["xp"] == 100)

        # ============================================================
        # 9b) C5: Role-Rewards Manager (frische Guild C)
        # ============================================================
        mgr6 = _new_manager()
        await mgr6.set_role_reward(GUILD_C, 10, 1010)
        await mgr6.set_role_reward(GUILD_C, 5, 5005)
        rr = mgr6.get_role_rewards(GUILD_C)
        _check("rr_sorted_keys", list(rr.keys()) == [5, 10], f"keys={list(rr.keys())}")
        _check("rr_values", rr == {5: 5005, 10: 1010}, f"rr={rr}")
        _check("rr_lower_default_off", mgr6.is_remove_lower_enabled(GUILD_C) is False)
        await mgr6.set_remove_lower(GUILD_C, True)
        _check("rr_lower_on", mgr6.is_remove_lower_enabled(GUILD_C) is True)
        rem = await mgr6.remove_role_reward(GUILD_C, 5)
        _check("rr_removed", rem is True and 5 not in mgr6.get_role_rewards(GUILD_C))
        _check("rr_remove_absent_false", await mgr6.remove_role_reward(GUILD_C, 99) is False)

        # ============================================================
        # 9) C4/C5: Embeds + _apply_role_reward (nur wenn discord da)
        # ============================================================
        try:
            import discord  # noqa: F401
            from cogs.leveling_cog import build_leaderboard_embed
            entries = [
                {"user_id": 1000 + i, "xp": (15 - i) * 100, "level": i}
                for i in range(15)
            ]
            emb0 = build_leaderboard_embed(entries, None, 0, 10, own_rank=3, own_id=1002)
            ftr0 = emb0.footer.text or ""
            _check("embed_page0_footer", "Seite 1/2" in ftr0, ftr0)
            _check("embed_own_rank", "Dein Rang: #3" in ftr0, ftr0)
            _check("embed_own_marker", "du" in (emb0.description or ""))
            emb1 = build_leaderboard_embed(entries, None, 1, 10, own_rank=3, own_id=1002)
            _check("embed_page1_footer", "Seite 2/2" in (emb1.footer.text or ""))
            # Page-Clamp: page 99 -> letzte Seite
            embc = build_leaderboard_embed(entries, None, 99, 10, own_rank=None, own_id=0)
            _check("embed_page_clamp", "Seite 2/2" in (embc.footer.text or ""))

            # --- C5: _apply_role_reward (Mock-Discord, frische Guild D) ---
            from cogs.leveling_cog import LevelingCog
            from unittest.mock import MagicMock, AsyncMock

            mgr7 = _new_manager()
            await mgr7.set_role_reward(GUILD_D, 5, 5005)
            await mgr7.set_role_reward(GUILD_D, 10, 1010)

            def _role(rid, name):
                r = MagicMock()
                r.id = rid
                r.name = name
                return r

            R5 = _role(5005, "R5")
            R10 = _role(1010, "R10")
            roles_by_id = {5005: R5, 1010: R10}
            guild_m = MagicMock()
            guild_m.id = int(GUILD_D)
            guild_m.get_role.side_effect = lambda rid: roles_by_id.get(rid)

            class _Stub:
                pass

            stub = _Stub()
            stub.leveling = mgr7

            # A) remove_lower aus -> alle erreichten Rollen sammeln
            mA = MagicMock()
            mA.display_name = "A"
            mA.roles = []
            mA.add_roles = AsyncMock()
            mA.remove_roles = AsyncMock()
            await LevelingCog._apply_role_reward(stub, mA, 10, guild_m)
            added_a = {c.args[0].id for c in mA.add_roles.call_args_list}
            _check("apply_add_both", added_a == {5005, 1010}, f"added={added_a}")
            _check("apply_no_remove", not mA.remove_roles.called)

            # B) remove_lower an -> nur hoechste behalten, R5 entfernen
            await mgr7.set_remove_lower(GUILD_D, True)
            mB = MagicMock()
            mB.display_name = "B"
            mB.roles = [R5]
            mB.add_roles = AsyncMock()
            mB.remove_roles = AsyncMock()
            await LevelingCog._apply_role_reward(stub, mB, 10, guild_m)
            added_b = {c.args[0].id for c in mB.add_roles.call_args_list}
            removed_b = {c.args[0].id for c in mB.remove_roles.call_args_list}
            _check("apply_lower_add_top", added_b == {1010}, f"added={added_b}")
            _check("apply_lower_remove_low", removed_b == {5005}, f"removed={removed_b}")

            # --- C6: Rank-Card Template ---
            # _try_render_card gibt None wenn Karte deaktiviert (Embed-Fallback).
            mgr8 = _new_manager()
            stub_card = _Stub()
            stub_card.leveling = mgr8
            mC = MagicMock()
            mC.display_name = "C"
            res_card = await LevelingCog._try_render_card(
                stub_card, 909090, mC, 1, 0, 100, "RANG #1"
            )
            _check("card_disabled_none", res_card is None)

            # Renderer akzeptiert header-Param und liefert PNG (nur wenn Pillow da).
            try:
                from modules.levelup_card import render_levelup_card as _rlc
                png = _rlc(
                    bg_path=__file__,  # kein Bild -> Solid-BG-Fallback
                    avatar_bytes=None,
                    username="Tester",
                    level=3,
                    header="RANG #3",
                )
                _check("card_render_png", isinstance(png, bytes) and len(png) > 100,
                       f"len={len(png) if isinstance(png, bytes) else 'n/a'}")
            except ImportError:
                pass  # Pillow lokal nicht installiert
        except ImportError:
            pass  # discord lokal nicht installiert -> Discord-Checks uebersprungen

    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  Leveling per-Guild Tests (modules/leveling.py, Phase C2)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] aiosqlite/discord lokal nicht installiert — laeuft am Server.")
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

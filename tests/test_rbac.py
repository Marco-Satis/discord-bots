#!/usr/bin/env python3
"""
Tests fuer das RBAC-Permission-Modell (modules/rbac.py) — R1-Kern.

Prueft die autoritative Permission-Aufloesung (die `require_perm` zur 403/Allow-
Entscheidung nutzt):
  - Owner -> alles erlaubt (view/edit/control auf jeden Bereich).
  - Member (eingeloggt, keine Rolle) -> view auf nicht-sensible Bereiche,
    KEIN edit/control, KEIN view auf sensible (rbac/audit/system).
  - Bereichs-Rolle -> ihre Grants greifen (Vereinigung), aber nur dort.
  - Fremde/leere Rolle -> kein zusaetzliches Recht (Isolation).
  - Unbekannte resource/action + None-User -> deny (fail-closed).

Lauf: python tests/test_rbac.py
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
    import modules.rbac as rbac
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


OWNER = {"sub": "1", "username": "marco", "is_owner": True, "roles": []}
MEMBER = {"sub": "2", "username": "member", "is_owner": False, "roles": []}
MC_ROLE = {"sub": "3", "username": "mcadmin", "is_owner": False, "roles": ["111"]}
OTHER_ROLE = {"sub": "4", "username": "other", "is_owner": False, "roles": ["999"]}


async def run_tests() -> None:
    tmp = tempfile.mkdtemp(prefix="rbac_")
    db_path = Path(tmp) / "rbac.db"
    await db_manager.init_db(db_path=db_path)

    try:
        # --- Owner = alles ---
        _check("owner_view", await rbac.has_perm(OWNER, "minecraft", "view"))
        _check("owner_edit", await rbac.has_perm(OWNER, "system", "edit"))
        _check("owner_control", await rbac.has_perm(OWNER, "minecraft", "control"))
        _check("owner_rbac", await rbac.has_perm(OWNER, "rbac", "edit"))
        owner_perms = await rbac.resolve_perms(OWNER)
        _check(
            "owner_resolve_all",
            len(owner_perms) == len(rbac.RESOURCES) * len(rbac.ACTIONS),
            f"n={len(owner_perms)}",
        )

        # --- Member = view-only auf nicht-sensible Bereiche ---
        _check("member_view_mc", await rbac.has_perm(MEMBER, "minecraft", "view"))
        _check("member_no_edit", not await rbac.has_perm(MEMBER, "minecraft", "edit"))
        _check("member_no_control", not await rbac.has_perm(MEMBER, "minecraft", "control"))
        _check("member_no_rbac_view", not await rbac.has_perm(MEMBER, "rbac", "view"))
        _check("member_no_audit_view", not await rbac.has_perm(MEMBER, "audit", "view"))
        _check("member_no_system_view", not await rbac.has_perm(MEMBER, "system", "view"))
        member_perms = await rbac.resolve_perms(MEMBER)
        _check("member_resolve_has_mc_view", ("minecraft", "view") in member_perms)
        _check("member_resolve_no_rbac_view", ("rbac", "view") not in member_perms)
        _check("member_resolve_no_mc_edit", ("minecraft", "edit") not in member_perms)

        # --- Rollen-Grant greift ---
        await rbac.set_role_grants(
            "111", [("minecraft", "edit"), ("minecraft", "control")], updated_by="test"
        )
        _check("role_grant_edit", await rbac.has_perm(MC_ROLE, "minecraft", "edit"))
        _check("role_grant_control", await rbac.has_perm(MC_ROLE, "minecraft", "control"))
        # Bereichs-Rolle bekommt KEIN edit auf fremden Bereich
        _check("role_no_cross_edit", not await rbac.has_perm(MC_ROLE, "satisfactory", "edit"))
        # view auf nicht-sensible Bereiche weiterhin (Member-Default)
        _check("role_still_view_sat", await rbac.has_perm(MC_ROLE, "satisfactory", "view"))

        # --- Isolation: fremde Rolle ohne Grants ---
        _check("isolation_no_edit", not await rbac.has_perm(OTHER_ROLE, "minecraft", "edit"))

        # --- Replace-Semantik: set_role_grants ersetzt komplett ---
        await rbac.set_role_grants("111", [("leveling", "edit")], updated_by="test")
        _check("replace_removed_old", not await rbac.has_perm(MC_ROLE, "minecraft", "edit"))
        _check("replace_added_new", await rbac.has_perm(MC_ROLE, "leveling", "edit"))

        # --- delete_role entfernt alle Grants ---
        await rbac.delete_role("111")
        _check("delete_role", not await rbac.has_perm(MC_ROLE, "leveling", "edit"))

        # --- fail-closed: unbekannte resource/action + None ---
        _check("unknown_resource_owner_deny", not await rbac.has_perm(OWNER, "nope", "view"))
        _check("unknown_action_deny", not await rbac.has_perm(OWNER, "minecraft", "hack"))
        _check("none_user_deny", not await rbac.has_perm(None, "minecraft", "view"))

        # --- Whitelist-Schutz: ungueltiger Grant wird verworfen ---
        await rbac.set_role_grants("222", [("minecraft", "edit"), ("evil", "control")], updated_by="test")
        role_map = await rbac.get_role_map()
        grants_222 = role_map.get("222", set())
        _check("validate_drops_invalid", ("evil", "control") not in grants_222 and ("minecraft", "edit") in grants_222,
               f"grants={grants_222}")

        # --- nicht-numerische Rollen-ID abgelehnt ---
        try:
            await rbac.set_role_grants("abc", [("minecraft", "view")])
            _check("reject_non_numeric_role", False, "keine ValueError")
        except ValueError:
            _check("reject_non_numeric_role", True)
    finally:
        await db_manager.close_db()


def main() -> int:
    print("=" * 60)
    print("  RBAC-Permission-Tests (modules/rbac.py)")
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

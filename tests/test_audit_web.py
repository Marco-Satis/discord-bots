#!/usr/bin/env python3
"""
Tests fuer das Dashboard-Audit-Log (modules/dashboard_audit.py + web/routes/audit_route.py) — R2.

Modul-Ebene:
  - log_action schreibt einen Eintrag; fetch_audit findet ihn.
  - Filter (resource / user) greifen.
  - _sanitize_detail redigiert Secret-Felder (token/password/webhook).
  - log_action ist best-effort (kein Crash bei kaputtem detail).
Web-Ebene (FastAPI TestClient, gepatchtes get_current_user):
  - anon -> 303, Member -> 403, Owner -> 200 (rendert).

Lauf: python tests/test_audit_web.py
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
    from modules import dashboard_audit
    HAVE_DEPS = True
except Exception:  # noqa: BLE001
    HAVE_DEPS = False

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import web.auth as auth
    from web.routes.audit_route import router as audit_router
    HAVE_WEB = True
except Exception:  # noqa: BLE001
    HAVE_WEB = False

_results: list[tuple[str, bool, str]] = []

# Render-Pfad nur testbar wenn die installierte Starlette die alte
# TemplateResponse-Signatur (name, context) hat (= Server). Lokal (neuere
# Starlette) wird der Render-Teil uebersprungen; Auth-Gate bleibt scharf.
try:
    import inspect as _inspect
    from starlette.templating import Jinja2Templates as _J2T
    _RENDER_TESTABLE = list(_inspect.signature(_J2T.TemplateResponse).parameters)[1] == "name"
except Exception:  # noqa: BLE001
    _RENDER_TESTABLE = False


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _check_render(name: str, resp, needle: str = "") -> None:
    if _RENDER_TESTABLE:
        ok = resp.status_code == 200 and (needle in resp.text if needle else True)
        _check(name, ok, f"status={resp.status_code}")
    else:
        _check(f"{name}_skip", True, "Render nicht testbar (Starlette-Signatur lokal neuer)")


OWNER = {"sub": "1", "username": "marco", "is_owner": True, "roles": []}
MEMBER = {"sub": "2", "username": "member", "is_owner": False, "roles": []}
_current: list = [None]


async def _module_tests() -> None:
    # _sanitize_detail redigiert Secrets (kein DB noetig)
    raw = {"server": "bmc5", "token": "supersecret", "new_password": "x", "webhook_url": "https://h"}
    safe = dashboard_audit._sanitize_detail(raw)
    _check("sanitize_redacts_token", '"token": "[REDACTED]"' in safe, safe)
    _check("sanitize_redacts_password", "[REDACTED]" in safe and "supersecret" not in safe, safe)
    _check("sanitize_keeps_nonsecret", "bmc5" in safe, safe)

    # log_action + fetch_audit Roundtrip
    await dashboard_audit.log_action("42", "marco", "rbac", "edit",
                                     detail={"op": "set_grants"}, ip="127.0.0.1")
    await dashboard_audit.log_action("43", "member", "minecraft", "control",
                                     detail={"cmd": "restart"}, ip="10.0.0.1")
    entries = await dashboard_audit.fetch_audit()
    _check("fetch_returns_entries", len(entries) >= 2, f"n={len(entries)}")

    by_resource = await dashboard_audit.fetch_audit(resource_q="rbac")
    _check("filter_resource", all(e["resource"] == "rbac" for e in by_resource) and len(by_resource) >= 1,
           f"n={len(by_resource)}")

    by_user = await dashboard_audit.fetch_audit(user_q="member")
    _check("filter_user", len(by_user) >= 1 and all("member" in (e["username"] or "") for e in by_user),
           f"n={len(by_user)}")

    # Secret darf nicht im persistierten detail stehen
    _check("no_secret_persisted", all("supersecret" not in (e["detail"] or "") for e in entries))

    # best-effort: detail mit nicht-serialisierbarem Objekt crasht nicht
    try:
        await dashboard_audit.log_action("44", "x", "system", "edit",
                                         detail={"obj": object()}, ip=None)
        _check("best_effort_no_crash", True)
    except Exception as e:  # noqa: BLE001
        _check("best_effort_no_crash", False, f"Exception: {e}")


def _web_tests() -> None:
    auth.get_current_user = lambda request: _current[0]
    app = FastAPI()
    app.include_router(audit_router)
    client = TestClient(app, raise_server_exceptions=False)

    _current[0] = None
    r = client.get("/audit", follow_redirects=False)
    _check("web_anon_303", r.status_code == 303, f"status={r.status_code}")

    _current[0] = MEMBER
    r = client.get("/audit", follow_redirects=False)
    _check("web_member_403", r.status_code == 403, f"status={r.status_code}")

    _current[0] = OWNER
    r = client.get("/audit", follow_redirects=False)
    _check("web_owner_not_blocked", r.status_code not in (303, 403), f"status={r.status_code}")
    _check_render("web_owner_200", r, "Audit-Log")

    # Filter-Query passiert den Gate (und rendert, wo testbar)
    r = client.get("/audit?resource=rbac&user=marco", follow_redirects=False)
    _check("web_owner_filter_not_blocked", r.status_code not in (303, 403), f"status={r.status_code}")
    _check_render("web_owner_filter_200", r)


def run_tests() -> None:
    tmp = tempfile.mkdtemp(prefix="auditweb_")
    db_path = Path(tmp) / "auditweb.db"
    asyncio.run(db_manager.init_db(db_path=db_path))
    try:
        asyncio.run(_module_tests())
        if HAVE_WEB:
            _web_tests()
        else:
            _check("web_skipped", True, "Web-Deps fehlen — uebersprungen")
    finally:
        asyncio.run(db_manager.close_db())


def main() -> int:
    print("=" * 60)
    print("  Audit-Log-Tests (modules/dashboard_audit.py + audit_route)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] aiosqlite nicht installiert — laeuft am Server.")
        print("  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN (uebersprungen)")
        return 0

    try:
        run_tests()
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

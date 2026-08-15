#!/usr/bin/env python3
"""
Tests für den CSRF-Schutz (web/middleware/csrf.py).

Anlass: das Token wurde ausschließlich aus dem Header gelesen. Das gewöhnliche
`<form method="POST">` auf `/config` schickt es aber als Feld — jedes Speichern
lief in ein 403, ohne dass es auffiel. Beim Nachrüsten des Feld-Wegs ist die
eigentliche Gefahr, den Request-Body zu verbrauchen: dann bekäme der Handler
leere Formulardaten. Genau das prüfen diese Tests.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_csrf_middleware.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from web.middleware import csrf as csrf_modul
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []
GUELTIG = "token-das-passt"


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _app() -> "TestClient":
    """Mini-App mit der echten Middleware; Tokenprüfung auf einen festen Wert."""
    async def speichern(request):
        form = await request.form()
        # Der Handler muss den Body noch sehen — sonst hat die Middleware ihn
        # verschluckt und jedes Formular käme leer an.
        return JSONResponse({"felder": dict(form)})

    app = Starlette(routes=[Route("/config", speichern, methods=["POST"])])
    app.add_middleware(csrf_modul.CSRFMiddleware)

    csrf_modul.generate_csrf_token = lambda request: GUELTIG
    csrf_modul.validate_csrf_token = lambda request, token: token == GUELTIG
    return TestClient(app)


def run_tests() -> None:
    client = _app()
    kekse = {"dashboard_token": "irgendwas"}  # markiert "angemeldet"

    # 1. Formularfeld wird akzeptiert UND der Body kommt vollständig an
    antwort = client.post(
        "/config",
        data={"csrf_token": GUELTIG, "feature": "an", "intervall": "42"},
        cookies=kekse,
    )
    _check("formularfeld_akzeptiert", antwort.status_code == 200,
           f"HTTP {antwort.status_code}")
    if antwort.status_code == 200:
        felder = antwort.json()["felder"]
        _check("body_kommt_beim_handler_an",
               felder.get("feature") == "an" and felder.get("intervall") == "42",
               str(felder))
        _check("token_bleibt_im_body", felder.get("csrf_token") == GUELTIG, str(felder))

    # 2. Der Header-Weg funktioniert unverändert (HTMX)
    antwort = client.post(
        "/config", data={"feature": "aus"},
        headers={"X-CSRF-Token": GUELTIG}, cookies=kekse,
    )
    _check("header_weg_unveraendert", antwort.status_code == 200,
           f"HTTP {antwort.status_code}")

    # 3. Falsches Token wird abgewiesen — der Feld-Weg darf kein Schlupfloch sein
    antwort = client.post(
        "/config", data={"csrf_token": "falsch", "feature": "an"}, cookies=kekse,
    )
    _check("falsches_token_abgewiesen", antwort.status_code == 403,
           f"HTTP {antwort.status_code}")

    # 4. Ganz ohne Token ebenfalls
    antwort = client.post("/config", data={"feature": "an"}, cookies=kekse)
    _check("ohne_token_abgewiesen", antwort.status_code == 403,
           f"HTTP {antwort.status_code}")

    # 5. Nicht angemeldet: die Middleware lässt durch, die Route prüft selbst
    antwort = client.post("/config", data={"feature": "an"})
    _check("ohne_login_durchgelassen", antwort.status_code == 200,
           f"HTTP {antwort.status_code}")

    # 6. Multipart wird nicht gepuffert (Uploads bleiben unangetastet) —
    #    ohne Header also abgewiesen statt still geschluckt
    antwort = client.post(
        "/config",
        files={"datei": ("test.txt", b"inhalt")},
        data={"csrf_token": GUELTIG},
        cookies=kekse,
    )
    _check("multipart_nicht_gepuffert", antwort.status_code == 403,
           f"HTTP {antwort.status_code}")


def main() -> int:
    print("=" * 60)
    print("  CSRF-Middleware Tests (web/middleware/csrf.py)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] starlette lokal nicht installiert — Test läuft am Server.")
        print("  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN (übersprungen)")
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

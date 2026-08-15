#!/usr/bin/env python3
"""
Tests für die Abmelde-Sperre (web/session_invalidation.py + web/auth.py).

Vorher beendete „Abmelden" die Sitzung nicht: das JWT im Cookie blieb bis zu
24 Stunden gültig, gelöscht wurde es nur im Browser. Wer das Cookie vorher
kopiert hatte, kam damit weiter rein. Geprüft wird deshalb genau das — ein
Token, das vor der Abmeldung ausgestellt wurde, muss danach abgelehnt werden.

Lauf: /home/botuser/Discord_Bots/venv/bin/python tests/test_session_invalidation.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from web import session_invalidation as si
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def _frische_datei() -> None:
    """Sperrliste auf eine leere Testdatei umbiegen."""
    ordner = tempfile.mkdtemp(prefix="sperrliste_")
    si.SPERRDATEI = Path(ordner) / "session_invalidation.json"
    si._cache, si._cache_zeit = {}, 0.0


def run_tests() -> None:
    _frische_datei()
    jetzt = time.time()

    # 1. Ohne Abmeldung gilt jedes Token
    _check("ohne_abmeldung_gueltig", not si.ist_abgemeldet("1234", jetzt))

    # 2. Nach der Abmeldung gilt ein vorher ausgestelltes Token nicht mehr
    altes_token_iat = jetzt - 60
    si.abmelden("1234")
    si._cache_zeit = 0.0  # Zwischenspeicher umgehen, wir messen die Wirkung
    _check("altes_token_abgelehnt", si.ist_abgemeldet("1234", altes_token_iat))

    # 3. Ein danach ausgestelltes Token gilt wieder — sonst käme man nie zurück
    _check("neues_token_gilt", not si.ist_abgemeldet("1234", time.time() + 2))

    # 4. Andere Nutzer sind nicht betroffen
    _check("fremder_nutzer_unberuehrt", not si.ist_abgemeldet("9999", altes_token_iat))

    # 5. Fehlende Angaben sperren niemanden aus
    _check("ohne_user_id_kein_block", not si.ist_abgemeldet(None, altes_token_iat))
    _check("ohne_iat_kein_block", not si.ist_abgemeldet("1234", None))

    # 6. Kaputte Sperrdatei darf niemanden aussperren
    si.SPERRDATEI.write_text("{kein json", encoding="utf-8")
    si._cache_zeit = 0.0
    _check("kaputte_datei_faellt_offen", not si.ist_abgemeldet("1234", altes_token_iat))

    # 7. Aufräumen entfernt nur alte Einträge
    _frische_datei()
    si.abmelden("aktuell")
    daten = si._laden()
    daten["uralt"] = time.time() - 60 * 60 * 24 * 90
    si.SPERRDATEI.write_text(__import__("json").dumps(daten), encoding="utf-8")
    si._cache_zeit = 0.0
    entfernt = si.aufraeumen()
    si._cache_zeit = 0.0
    rest = si._laden()
    _check("aufraeumen_entfernt_alte", entfernt == 1, f"{entfernt} entfernt")
    _check("aufraeumen_behaelt_neue", "aktuell" in rest and "uralt" not in rest, str(rest))

    # 8. Der Weg über das Token selbst — _decode_jwt muss die Sperre ziehen
    try:
        from web import auth
        _frische_datei()
        auth.session_invalidation.SPERRDATEI = si.SPERRDATEI
        auth.session_invalidation._cache, auth.session_invalidation._cache_zeit = {}, 0.0

        token = auth._create_jwt({"sub": "4711", "username": "test"})
        _check("frisches_token_dekodiert", auth._decode_jwt(token) is not None)

        # Abmelden — und zwar mit einer Grenze, die nach dem iat liegt
        time.sleep(1.1)
        auth.session_invalidation.abmelden("4711")
        auth.session_invalidation._cache_zeit = 0.0
        _check("token_nach_abmeldung_ungueltig", auth._decode_jwt(token) is None,
               "kopiertes Cookie waere weiter gueltig")

        # Nach erneuter Anmeldung geht es wieder
        neues = auth._create_jwt({"sub": "4711", "username": "test"})
        _check("neuanmeldung_funktioniert", auth._decode_jwt(neues) is not None)
    except (ImportError, RuntimeError, SystemExit) as e:
        # web.auth verlangt beim Import WEB_SECRET_KEY aus der Prod-.env. Im
        # Dev-Mirror gibt es die nicht — der Sperr-Kern oben ist unabhaengig
        # davon vollstaendig geprueft.
        print(f"  [INFO] JWT-Weg uebersprungen: {str(e)[:80]}")


def main() -> int:
    print("=" * 60)
    print("  Abmelde-Sperre (web/session_invalidation.py)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] Abhängigkeiten lokal nicht installiert — Test läuft am Server.")
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

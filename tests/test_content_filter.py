#!/usr/bin/env python3
"""
Tests fuer den Content-Filter (D4 Auto-Mod): Mass-Caps + Zalgo.

  - is_mass_caps: lange GROSS-Nachrichten triggern, kurze/normale nicht.
  - is_zalgo: viele kombinierende Unicode-Zeichen triggern, Akzente nicht.
  - ContentFilter: Schwellwerte aus Config, check_caps/check_zalgo.

Lauf: python tests/test_content_filter.py
Rein synchron, keine externen Deps.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.moderation.content_filter import (  # noqa: E402
    ContentFilter,
    is_mass_caps,
    is_zalgo,
)

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def run_tests() -> None:
    # --- is_mass_caps ---
    _check("caps_loud", is_mass_caps("HELLO EVERYONE LOOK HERE NOW") is True)
    _check("caps_normal", is_mass_caps("hallo zusammen wie geht es euch") is False)
    _check("caps_mixed", is_mass_caps("Hallo Welt das ist ganz normal hier") is False)
    # kurze Rufe (< min_len Buchstaben) ignorieren
    _check("caps_short_ok", is_mass_caps("OK!") is False)
    _check("caps_short_hello", is_mass_caps("HELLO") is False)  # 5 < 10
    # Zahlen/Satzzeichen zaehlen nicht als Buchstaben
    _check("caps_with_numbers", is_mass_caps("STOP SPAMMING 12345 PLEASE") is True)
    _check("caps_empty", is_mass_caps("") is False)
    _check("caps_none", is_mass_caps(None) is False)
    # ratio-Parameter
    _check("caps_ratio_strict", is_mass_caps("HELLO world this here", ratio=0.9) is False)

    # --- is_zalgo ---
    zalgo = "a" + "̀́̂̃̄̅" * 3  # viele combining
    _check("zalgo_detected", is_zalgo(zalgo) is True)
    _check("zalgo_normal", is_zalgo("ganz normaler Text ohne Tricks") is False)
    # einzelne Akzente (precomposed) sind kein Zalgo
    _check("zalgo_accents_ok", is_zalgo("café naïve résumé") is False)
    # ein einzelnes combining (< min_combining) ist ok
    _check("zalgo_one_combining_ok", is_zalgo("café") is False)
    _check("zalgo_empty", is_zalgo("") is False)
    _check("zalgo_none", is_zalgo(None) is False)

    # --- ContentFilter (Schwellwerte aus Config) ---
    cf = ContentFilter({})
    _check("cf_default_caps", cf.check_caps("THIS IS A LOUD MESSAGE HERE") is True)
    _check("cf_default_zalgo", cf.check_zalgo(zalgo) is True)
    _check("cf_clean", cf.check_caps("alles ruhig hier") is False)

    # Config-Override: niedrigere min_len -> kuerzere Caps triggern
    cf2 = ContentFilter({"content_filter": {"caps_min_len": 3, "caps_ratio": 0.7}})
    _check("cf_override_short", cf2.check_caps("HEY") is True)
    _check("cf_default_short_off", cf.check_caps("HEY") is False)


def main() -> int:
    print("=" * 60)
    print("  Content-Filter Tests (D4: Mass-Caps + Zalgo)")
    print("=" * 60)
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

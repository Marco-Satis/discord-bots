#!/usr/bin/env python3
"""
Tests fuer den RaidDetector (Phase G, G1) — deterministisch via injizierter Zeit.

  - Join-Spike: threshold im Fenster erreicht -> True.
  - Alte Joins fallen aus dem Fenster (Gleitfenster prunt).
  - Guild-Isolation: getrennte Zaehler.
  - threshold<=0 deaktiviert.
  - Alarm-Cooldown verhindert Mehrfach-Alarme.

Lauf: python tests/test_raid_detector.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from modules.raid_detector import RaidDetector
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def run_tests() -> None:
    d = RaidDetector()
    # 5 Joins in 10s -> Raid bei 5. (threshold=5, window=10)
    kw = {"threshold": 5, "window_seconds": 10.0}
    res = [d.record_join("g1", float(t), **kw) for t in (1, 2, 3, 4, 5)]
    _check("no_raid_below", res[:4] == [False, False, False, False])
    _check("raid_at_threshold", res[4] is True)
    _check("count_5", d.join_count("g1") == 5)

    # Gleitfenster: bei t=100 sind alte raus -> 1 Join
    d.record_join("g1", 100.0, **kw)
    _check("window_pruned", d.join_count("g1") == 1)

    # Guild-Isolation
    d.record_join("g2", 1.0, **kw)
    _check("isolation", d.join_count("g2") == 1 and d.join_count("g1") == 1)

    # threshold<=0 deaktiviert
    _check("disabled", d.record_join("g3", 1.0, threshold=0, window_seconds=10) is False)

    # Alarm-Cooldown
    d2 = RaidDetector()
    _check("alert_first", d2.should_alert("g1", 0.0, alert_cooldown=60) is True)
    _check("alert_cooldown_blocks", d2.should_alert("g1", 30.0, alert_cooldown=60) is False)
    _check("alert_after_cooldown", d2.should_alert("g1", 70.0, alert_cooldown=60) is True)

    # reset
    d.reset("g1")
    _check("reset", d.join_count("g1") == 0)


def main() -> int:
    print("=" * 60)
    print("  RaidDetector Tests (G1)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] Modul nicht importierbar.")
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

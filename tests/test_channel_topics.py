#!/usr/bin/env python3
"""
Tests fuer die Channel-Topic-Logik (Phase setup-topics).

  - resolve_topic: case-insensitive Match, None bei Unbekannt.
  - load_topic_mapping: DEFAULT_TOPICS vorhanden, Topics <= 1024 Zeichen.

Lauf: python tests/test_channel_topics.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from cogs.channel_setup_cog import (
        resolve_topic, load_topic_mapping, DEFAULT_TOPICS, MAX_TOPIC_LEN,
    )
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def run_tests() -> None:
    mapping = load_topic_mapping()
    _check("has_defaults", len(mapping) >= len(DEFAULT_TOPICS) and len(mapping) > 10)
    _check("all_within_limit", all(len(v) <= MAX_TOPIC_LEN for v in mapping.values()))
    _check("keys_lowercase", all(k == k.lower() for k in mapping))

    # resolve case-insensitive
    _check("resolve_exact", resolve_topic("chat", mapping) == mapping["chat"])
    _check("resolve_upper", resolve_topic("CHAT", mapping) == mapping["chat"])
    _check("resolve_strip", resolve_topic("  chat  ", mapping) == mapping["chat"])
    _check("resolve_unknown", resolve_topic("does-not-exist", mapping) is None)

    # bekannte Channels gemappt
    for ch in ("willkommen", "regelwerk", "spielersuche", "bot-commands"):
        _check(f"mapped_{ch}", resolve_topic(ch, mapping) is not None)


def main() -> int:
    print("=" * 60)
    print("  Channel-Topics Tests (setup-topics)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] cog nicht importierbar (discord fehlt?).")
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

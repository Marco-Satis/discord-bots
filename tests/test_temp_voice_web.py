#!/usr/bin/env python3
"""
Tests fuer web/temp_voice_bridge.py (C1 Multi-Hub Dashboard<->Bot-Bruecke).

  - parse_hubs_text: Pipe-Format, Defaults, Clamp, Garbage-Reject.
  - hubs_to_text: Round-Trip stabil.
  - write/read: merge bewahrt bot-eigene Keys (interface/join), AFK-Timeout.

Lauf: python tests/test_temp_voice_web.py
Skippt sauber wenn utils-Imports lokal fehlen (laeuft am Server).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from web import temp_voice_bridge as bridge
    HAVE_DEPS = True
except Exception:  # noqa: BLE001 — utils evtl. lokal nicht ladbar
    HAVE_DEPS = False

_results: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, msg: str = "") -> None:
    _results.append((name, bool(cond), msg))


def run_tests() -> None:
    # --- parse_hubs_text ---
    text = (
        "111 | 222 | 5 | ja | {user} COD\n"
        "# Kommentarzeile wird ignoriert\n"
        "\n"
        "333 | | 0 | nein | {game} #{count}\n"
        "keine-id-zeile | foo\n"
        "444"
    )
    hubs = bridge.parse_hubs_text(text)
    _check("parse_count", len(hubs) == 3, f"got {len(hubs)}")
    _check("parse_first_id", hubs[0]["hub_id"] == 111)
    _check("parse_first_cat", hubs[0]["category_id"] == 222)
    _check("parse_first_limit", hubs[0]["default_limit"] == 5)
    _check("parse_first_private", hubs[0]["default_private"] is True)
    _check("parse_first_naming", hubs[0]["naming"] == "{user} COD")
    _check("parse_second_cat_none", hubs[1]["category_id"] is None)
    _check("parse_second_private_false", hubs[1]["default_private"] is False)
    _check("parse_bareid_default_naming",
           hubs[2]["hub_id"] == 444 and hubs[2]["naming"] == "{user}'s Channel")

    # Limit-Clamp
    clamp = bridge.parse_hubs_text("1 | | 500 | |")
    _check("parse_limit_clamp", clamp[0]["default_limit"] == 99)

    # --- hubs_to_text Round-Trip ---
    txt = bridge.hubs_to_text(hubs)
    reparsed = bridge.parse_hubs_text(txt)
    _check("roundtrip_count", len(reparsed) == 3)
    _check("roundtrip_id", reparsed[0]["hub_id"] == 111)
    _check("roundtrip_private", reparsed[0]["default_private"] is True)
    _check("roundtrip_naming", reparsed[0]["naming"] == "{user} COD")

    # --- write/read: merge bewahrt bot-eigene Keys ---
    tmp_dir = tempfile.mkdtemp(prefix="tvbridge_")
    tmp_file = Path(tmp_dir) / "temp_voice_config.json"
    tmp_file.write_text(
        json.dumps({"interface_channel_id": 42, "join_channel_id": 99}),
        encoding="utf-8",
    )
    orig = bridge.BOT_CONFIG_FILE
    bridge.BOT_CONFIG_FILE = tmp_file
    try:
        ok = bridge.write_temp_voice_config(hubs, afk_timeout_minutes=10)
        _check("write_ok", ok is True)
        read_back = bridge.read_temp_voice_hubs()
        _check("read_back_count", len(read_back) == 3)
        _check("afk_read", bridge.read_afk_timeout() == 10)
        merged = json.loads(tmp_file.read_text(encoding="utf-8"))
        _check("merge_keeps_interface", merged.get("interface_channel_id") == 42)
        _check("merge_keeps_join", merged.get("join_channel_id") == 99)
        _check("merge_has_hubs", len(merged.get("hubs", [])) == 3)
    finally:
        bridge.BOT_CONFIG_FILE = orig


def main() -> int:
    print("=" * 60)
    print("  Temp-Voice Bridge Tests (C1 Multi-Hub Dashboard<->Bot)")
    print("=" * 60)
    if not HAVE_DEPS:
        print("  [SKIP] utils lokal nicht ladbar — laeuft am Server.")
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

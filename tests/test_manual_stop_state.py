#!/usr/bin/env python3
"""
test_manual_stop_state.py — Unit-Tests fuer modules/monitoring/manual_stop_state.py

Deckt ab (F02 aus Review v5-sweep_2026-05-28):
  - mark_stopped / mark_started / is_manually_stopped Roundtrip
  - Service-Name ↔ server_id Mapping (is_service_manually_stopped)
  - Atomic-Write (File existiert + valides JSON)
  - Concurrent-Marks ohne Lost-Update (F03-Regression-Guard)
  - Unbekannte Server/Services → kein Over-Block
  - get_stopped_servers / stopped_at

manual_stop_state ist stdlib-only → laeuft lokal ohne Server-Deps.

Aufruf:
    python tests/test_manual_stop_state.py
    pytest tests/test_manual_stop_state.py
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

# Projekt-Root (eine Ebene ueber tests/)
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from modules.monitoring import manual_stop_state as mss  # noqa: E402


def _fresh_state_file():
    """Setzt STATE_FILE auf eine frische Temp-Datei + raeumt Reststand."""
    tmpdir = tempfile.mkdtemp(prefix="mss_test_")
    mss.STATE_FILE = Path(tmpdir) / "manual_stop_state.json"
    return mss.STATE_FILE


def test_roundtrip_mark_and_clear():
    _fresh_state_file()
    assert mss.is_manually_stopped("mc_bmc") is False
    asyncio.run(mss.mark_stopped("mc_bmc"))
    assert mss.is_manually_stopped("mc_bmc") is True
    asyncio.run(mss.mark_started("mc_bmc"))
    assert mss.is_manually_stopped("mc_bmc") is False


def test_service_name_mapping():
    _fresh_state_file()
    asyncio.run(mss.mark_stopped("mc_vanilla"))
    # via Service-Name (Watchdog-Pfad)
    assert mss.is_service_manually_stopped("minecraft-vanilla.service") is True
    assert mss.is_service_manually_stopped("minecraft-bmc.service") is False
    # unbekannter Service → kein Over-Block
    assert mss.is_service_manually_stopped("nginx.service") is False


def test_atomic_write_produces_valid_json():
    sf = _fresh_state_file()
    asyncio.run(mss.mark_stopped("satisfactory"))
    assert sf.exists()
    data = json.loads(sf.read_text(encoding="utf-8"))
    assert "satisfactory" in data
    # Timestamp ist ISO-parsebar
    from datetime import datetime
    datetime.fromisoformat(data["satisfactory"])


def test_concurrent_marks_no_lost_update():
    """F03-Regression: gleichzeitige Marks duerfen sich nicht ueberschreiben."""
    _fresh_state_file()

    async def _run():
        await asyncio.gather(
            mss.mark_stopped("mc_bmc"),
            mss.mark_stopped("mc_vanilla"),
            mss.mark_stopped("satisfactory"),
        )

    asyncio.run(_run())
    stopped = mss.get_stopped_servers()
    assert set(stopped.keys()) == {"mc_bmc", "mc_vanilla", "satisfactory"}, (
        f"Lost-Update: erwartet 3 Eintraege, bekam {list(stopped.keys())}"
    )


def test_mark_started_idempotent_on_missing():
    _fresh_state_file()
    # clear auf nicht-gesetztem Server darf nicht crashen
    asyncio.run(mss.mark_started("mc_bmc"))
    assert mss.is_manually_stopped("mc_bmc") is False


def test_unknown_server_not_stopped():
    _fresh_state_file()
    assert mss.is_manually_stopped("does_not_exist") is False
    assert mss.stopped_at("does_not_exist") is None


def test_stopped_at_returns_timestamp():
    _fresh_state_file()
    asyncio.run(mss.mark_stopped("mc_bmc"))
    ts = mss.stopped_at("mc_bmc")
    assert ts is not None
    from datetime import datetime
    datetime.fromisoformat(ts)


def test_corrupt_state_file_returns_empty():
    sf = _fresh_state_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text("{ not valid json", encoding="utf-8")
    # darf nicht crashen, faellt auf leeren State zurueck
    assert mss.is_manually_stopped("mc_bmc") is False


# ---------------------------------------------------------------------------
# Standalone-Runner (ohne pytest)
# ---------------------------------------------------------------------------
def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())

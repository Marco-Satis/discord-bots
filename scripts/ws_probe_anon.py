"""Anon-WS-Probe: prueft dass /ws OHNE Auth-Cookie zwar handshaked (101)
aber sofort geschlossen wird (1008) und KEINE dashboard_update-Daten leakt.
Erwartet: ANON_HANDSHAKE_OK gefolgt von ANON_CLOSED (auth-gate greift).
Lauf mit botuser-venv-python (hat websockets)."""
import asyncio

import websockets


async def main() -> None:
    uri = "ws://127.0.0.1:8080/ws"
    try:
        ws = await websockets.connect(uri, open_timeout=5)
    except Exception as e:  # noqa: BLE001 - Probe soll jeden Fehler zeigen
        print(f"ANON_CONNECT_FAILED {type(e).__name__}: {e}")
        return
    print("ANON_HANDSHAKE_OK (101 Switching Protocols)")
    try:
        msg = await asyncio.wait_for(ws.recv(), timeout=7)
        print(f"ANON_RECEIVED (SECURITY-LEAK?): {str(msg)[:160]}")
    except websockets.exceptions.ConnectionClosed as e:
        print(f"ANON_CLOSED ({e!r}) -> auth-gate OK, kein Daten-Leak")
    except asyncio.TimeoutError:
        print("ANON_TIMEOUT -> blieb offen, kein Close, keine Daten (unerwartet)")
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


asyncio.run(main())

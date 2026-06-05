"""Trust-bewusste Client-IP-Ermittlung (Audit-Fix M15 / B3).

Hinter dem nginx-Reverse-Proxy (laeuft auf 127.0.0.1) setzt nginx
`X-Real-IP = $remote_addr` (echter Client, von nginx ueberschrieben → NICHT
client-faelschbar) und `X-Forwarded-For = $proxy_add_x_forwarded_for` (haengt
den echten Client ans Ende an). Ein direkter Client kann beliebige
X-Forwarded-For-Header senden → das ERSTE XFF-Element ist faelschbar.

Regel: Proxy-Header nur vertrauen wenn der direkte TCP-Peer in der
Trusted-Proxy-Liste ist. Dann X-Real-IP bevorzugen, sonst das LETZTE
XFF-Element (das von nginx angehaengte). Sonst direkter Peer.

Haertung (Review S1/S4, 2026-06-05): Peer- und Proxy-Header-Vergleich laeuft
ueber `ipaddress`-normalisierte Werte (robust gegen IPv6-Schreibweisen wie
`::1` vs `0:0:0:0:0:0:0:1`). Vom Proxy gelieferte Werte werden nur uebernommen
wenn sie ein gueltiges IP-Literal sind — Garbage/Format-Injection faellt auf
den (vertrauenswuerdigen) Peer zurueck.
"""

import ipaddress

from utils.config import get_env


def _norm_ip(value):
    """Normalisiert eine IP fuer den Vergleich; None wenn kein gueltiges IP-Literal."""
    try:
        return ipaddress.ip_address(value.strip())
    except (ValueError, AttributeError):
        return None


# Vertrauenswuerdige Proxy-IPs (nginx lokal). Override via ENV (Komma-Liste).
# Als normalisierte IP-Objekte gehalten → korrekter IPv6-Vergleich.
_raw = get_env("WEB_TRUSTED_PROXIES", "127.0.0.1,::1")
TRUSTED_PROXIES: set = {
    ip for ip in (_norm_ip(p) for p in str(_raw).split(",")) if ip is not None
}


def client_ip_from_scope(scope) -> str:
    """Echte Client-IP aus dem ASGI-Scope, trust-bewusst.

    Akzeptiert ein Starlette-Request via `request.scope`.
    """
    client = scope.get("client")
    peer = client[0] if client else None
    peer_ip = _norm_ip(peer) if peer else None

    if peer_ip is not None and peer_ip in TRUSTED_PROXIES:
        x_real_ip = None
        x_forwarded_for = None
        for key, value in scope.get("headers", []):
            if key == b"x-real-ip":
                x_real_ip = value.decode("utf-8", errors="replace").strip()
            elif key == b"x-forwarded-for":
                x_forwarded_for = value.decode("utf-8", errors="replace")
        # Proxy-Header nur uebernehmen wenn gueltiges IP-Literal.
        if x_real_ip and _norm_ip(x_real_ip) is not None:
            return x_real_ip
        if x_forwarded_for:
            parts = [p.strip() for p in x_forwarded_for.split(",") if p.strip()]
            # nginx haengt den echten Client als letztes Element an.
            if parts and _norm_ip(parts[-1]) is not None:
                return parts[-1]

    return peer or "unknown"

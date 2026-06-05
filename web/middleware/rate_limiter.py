"""
F48: Rate-Limiting Middleware fuer Dashboard-API — Token-Bucket Algorithmus

Eigene Implementierung ohne externe Abhaengigkeiten.
Pro IP-Adresse wird ein Token-Bucket gefuehrt mit unterschiedlichen
Limits je nach Endpoint-Gruppe:

- Login:   5 req/min
- Actions: 10 req/min  (POST auf /api/*/action)
- Lesen:   60 req/min  (GET-Requests)
- Health:  60 req/min

Inaktive Buckets (>10 Min ohne Request) werden alle 5 Minuten bereinigt.

WICHTIG: Pure ASGI Middleware (kein BaseHTTPMiddleware), um
Body-Forwarding-Probleme bei POST-Requests zu vermeiden.
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Tuple

from starlette.responses import Response, JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from utils.logger import get_logger

logger = get_logger("web.rate_limiter")

# ---------------------------------------------------------------------------
# Konfiguration: Endpoint-Gruppen mit (max_tokens, refill_rate_per_sekunde)
# ---------------------------------------------------------------------------
# max_tokens  = Burst-Groesse (gleichzeitig verfuegbare Tokens)
# refill_rate = Tokens pro Sekunde die nachgefuellt werden

LOGIN_LIMIT: Tuple[int, float] = (5, 5 / 60)         # 5 req/min
ACTION_LIMIT: Tuple[int, float] = (10, 10 / 60)       # 10 req/min
READ_LIMIT: Tuple[int, float] = (60, 60 / 60)         # 60 req/min
HEALTH_LIMIT: Tuple[int, float] = (60, 60 / 60)       # 60 req/min

# Regex fuer Action-Endpunkte: /api/<beliebig>/action
ACTION_PATTERN: re.Pattern = re.compile(r"^/api/.+/action$")

# Bereinigung: Intervall und Inaktivitaets-Schwelle
CLEANUP_INTERVAL: float = 300.0    # 5 Minuten
INACTIVE_THRESHOLD: float = 600.0  # 10 Minuten

# Pfade die komplett vom Rate-Limiting ausgenommen sind
EXEMPT_PATHS = {
    "/static",
    "/favicon.ico",
    "/ws",
}


# ---------------------------------------------------------------------------
# Token-Bucket Datenstruktur
# ---------------------------------------------------------------------------
@dataclass
class TokenBucket:
    """
    Ein Token-Bucket der kontinuierlich Tokens nachfuellt.

    Tokens werden bei jedem Zugriff lazy nachgefuellt basierend
    auf der verstrichenen Zeit seit dem letzten Zugriff.
    """

    max_tokens: float
    refill_rate: float  # Tokens pro Sekunde
    tokens: float = field(init=False)
    last_refill: float = field(init=False)
    last_access: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = self.max_tokens
        self.last_refill = time.monotonic()
        self.last_access = time.monotonic()

    def _refill(self) -> None:
        """Fuellt Tokens basierend auf verstrichener Zeit nach."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def consume(self) -> Tuple[bool, float]:
        """
        Versucht ein Token zu verbrauchen.

        Returns:
            Tuple aus (erlaubt, retry_after_sekunden).
            Bei Erfolg ist retry_after 0.0.
        """
        self._refill()
        self.last_access = time.monotonic()

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True, 0.0

        # Berechne wie lange bis wieder ein Token verfuegbar ist
        deficit = 1.0 - self.tokens
        retry_after = deficit / self.refill_rate if self.refill_rate > 0 else 60.0
        return False, retry_after


# ---------------------------------------------------------------------------
# Bucket-Speicher: Schluessel = (IP-Adresse, Endpoint-Gruppe)
# ---------------------------------------------------------------------------
class BucketStore:
    """
    Verwaltet Token-Buckets pro IP und Endpoint-Gruppe.

    Jede Kombination aus IP-Adresse und Endpoint-Gruppe erhaelt
    einen eigenen Bucket. Inaktive Buckets werden periodisch entfernt.
    """

    def __init__(self) -> None:
        self._buckets: Dict[Tuple[str, str], TokenBucket] = {}
        self._last_cleanup: float = time.monotonic()

    def get_or_create(
        self, ip: str, group: str, max_tokens: float, refill_rate: float
    ) -> TokenBucket:
        """Gibt den Bucket fuer IP + Gruppe zurueck, erstellt ihn bei Bedarf."""
        key = (ip, group)
        bucket = self._buckets.get(key)

        if bucket is None:
            bucket = TokenBucket(max_tokens=max_tokens, refill_rate=refill_rate)
            self._buckets[key] = bucket

        return bucket

    def cleanup(self) -> int:
        """
        Entfernt Buckets die laenger als INACTIVE_THRESHOLD inaktiv sind.

        Returns:
            Anzahl der entfernten Buckets.
        """
        now = time.monotonic()
        stale_keys = [
            key
            for key, bucket in self._buckets.items()
            if (now - bucket.last_access) > INACTIVE_THRESHOLD
        ]
        for key in stale_keys:
            del self._buckets[key]
        return len(stale_keys)

    @property
    def size(self) -> int:
        """Aktuelle Anzahl gespeicherter Buckets."""
        return len(self._buckets)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def _get_client_ip(scope: Scope) -> str:
    """Ermittelt die echte Client-IP (trust-bewusst, B3/M15-Fix).

    Vorher: erstes X-Forwarded-For-Element (client-faelschbar → Rate-Limit-Bypass
    + IP-Spoofing). Jetzt: Proxy-Header nur von trusted Peer (nginx 127.0.0.1).
    """
    from utils.client_ip import client_ip_from_scope
    return client_ip_from_scope(scope)


def _classify_request(path: str, method: str) -> Tuple[str, float, float]:
    """
    Klassifiziert einen Request in eine Endpoint-Gruppe.

    Returns:
        Tuple aus (gruppenname, max_tokens, refill_rate).
    """
    # Login-Endpunkt
    if path == "/auth/login":
        return "login", *LOGIN_LIMIT

    # Health-Endpunkt
    if path.startswith("/api/health"):
        return "health", *HEALTH_LIMIT

    # Action-Endpunkte (nur POST)
    if method == "POST" and ACTION_PATTERN.match(path):
        return "action", *ACTION_LIMIT

    # Alles andere: Lese-Requests (GET und sonstige)
    return "read", *READ_LIMIT


# Globaler Bucket-Store (wird von der Middleware-Instanz verwendet)
_global_store = BucketStore()
_cleanup_task: asyncio.Task | None = None


async def _periodic_cleanup() -> None:
    """Hintergrund-Task der inaktive Buckets periodisch entfernt."""
    try:
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)
            removed = _global_store.cleanup()
            if removed > 0:
                logger.debug(
                    f"Bucket-Bereinigung: {removed} inaktive Buckets entfernt, "
                    f"{_global_store.size} verbleibend"
                )
    except asyncio.CancelledError:
        logger.debug("Bucket-Bereinigung gestoppt")


# ---------------------------------------------------------------------------
# Rate-Limiting Middleware (Pure ASGI)
# ---------------------------------------------------------------------------
class RateLimitMiddleware:
    """
    Pure ASGI Rate-Limiting Middleware mit Token-Bucket Algorithmus.

    - Pro IP-Adresse und Endpoint-Gruppe wird ein separater Bucket gefuehrt
    - Bei Ueberschreitung wird HTTP 429 mit Retry-After Header zurueckgegeben
    - Inaktive Buckets werden automatisch alle 5 Minuten bereinigt
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        global _cleanup_task
        # Cleanup-Task beim ersten Request starten
        if _cleanup_task is None:
            _cleanup_task = asyncio.create_task(_periodic_cleanup())

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        # Ausgenommene Pfade ueberspringen
        for exempt in EXEMPT_PATHS:
            if path.startswith(exempt):
                await self.app(scope, receive, send)
                return

        # Client-IP ermitteln
        client_ip = _get_client_ip(scope)

        # Request klassifizieren
        group, max_tokens, refill_rate = _classify_request(path, method)

        # Bucket holen und Token verbrauchen
        bucket = _global_store.get_or_create(client_ip, group, max_tokens, refill_rate)
        allowed, retry_after = bucket.consume()

        if not allowed:
            retry_after_int = int(retry_after) + 1  # Aufrunden auf ganze Sekunden
            logger.warning(
                f"Rate-Limit erreicht: {client_ip} -> {method} {path} "
                f"(Gruppe: {group}, Retry-After: {retry_after_int}s)"
            )

            # JSON-Response wenn Client JSON akzeptiert, sonst Plaintext
            accept_header = ""
            for key, value in scope.get("headers", []):
                if key == b"accept":
                    accept_header = value.decode("utf-8", errors="replace")
                    break

            if "application/json" in accept_header:
                response = JSONResponse(
                    {
                        "detail": "Zu viele Anfragen. Bitte spaeter erneut versuchen.",
                        "retry_after": retry_after_int,
                    },
                    status_code=429,
                    headers={"Retry-After": str(retry_after_int)},
                )
            else:
                response = Response(
                    "429 Too Many Requests — Bitte spaeter erneut versuchen.",
                    status_code=429,
                    media_type="text/plain",
                    headers={"Retry-After": str(retry_after_int)},
                )
            await response(scope, receive, send)
            return

        # Request weiterleiten (Body bleibt unangetastet!)
        await self.app(scope, receive, send)

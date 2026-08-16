"""
Phase 13a: Authentifizierung — Discord OAuth2 + Fallback-Login

Unterstuetzt zwei Anmeldemethoden:
  1. Discord OAuth2 (primaer) — prueft Guild-Mitgliedschaft und Rollen
  2. Benutzername/Passwort (Fallback) — bcrypt-geprueft, fuer Notfaelle
"""

import time
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import jwt
import httpx
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from web.templates_setup import erstelle_templates

from utils.config import load_env, get_env, get_config
from utils.logger import get_logger
from web import session_invalidation
from utils.client_ip import client_ip_from_scope

logger = get_logger("web.auth")

# Templates-Verzeichnis
TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)

auth_router = APIRouter(prefix="/auth", tags=["Authentifizierung"])

# --- Konfiguration aus Umgebungsvariablen ---

DISCORD_CLIENT_ID = get_env("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = get_env("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = get_env("DISCORD_REDIRECT_URI", "http://localhost:8080/auth/discord/callback")
WEB_SECRET_KEY = get_env("WEB_SECRET_KEY", "")
if not WEB_SECRET_KEY:
    # M34-Fix: im Prod (WEB_HTTPS=true) ist ein fester Key Pflicht — sonst
    # divergieren Session-/JWT-Keys ueber Worker/Neustarts (alle Sessions brechen,
    # Multi-Worker-JWT-Verify schlaegt fehl). Fail-closed statt stiller Zufalls-Key.
    if get_env("WEB_HTTPS", "true", cast=bool):
        raise RuntimeError(
            "WEB_SECRET_KEY fehlt in config/.env — Pflicht im Prod-Betrieb "
            "(WEB_HTTPS=true). Setze einen festen WEB_SECRET_KEY."
        )
    import secrets as _secrets
    WEB_SECRET_KEY = _secrets.token_hex(32)
    logger.warning("WEB_SECRET_KEY nicht gesetzt — temporaerer Dev-Key generiert (nur ohne WEB_HTTPS).")
WEB_ADMIN_USER = get_env("WEB_ADMIN_USER", "admin")
WEB_ADMIN_PASS_HASH = get_env("WEB_ADMIN_PASS_HASH", "")
GUILD_ID = get_env("GUILD_ID", "")
WEB_HTTPS = get_env("WEB_HTTPS", "true", cast=bool)
# B2/M13-Fix: Fallback-Passwort-Login (Owner-Rechte) standardmaessig AUS.
# Nur aktiv wenn WEB_FALLBACK_LOGIN=true gesetzt ist.
WEB_FALLBACK_LOGIN = get_env("WEB_FALLBACK_LOGIN", "false", cast=bool)

# Discord OAuth2 Endpunkte
DISCORD_AUTH_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"  # nosec B105 - oeffentliche OAuth2-Endpoint-URL, kein Secret
DISCORD_API_URL = "https://discord.com/api/v10"

# JWT Konfiguration
JWT_ALGORITHM = "HS256"
# Absolutes Session-Cap (JWT-`exp` + Cookie-max_age) = 24 h fuer aktive Nutzung. Das
# eigentliche Auslog-Verhalten steuert der ZWEISTUFIGE Idle-Timeout (Marco 2026-07-14):
# 10 min Soft-Logout (zur Login-Seite, Cookie BLEIBT) / 60 min Hard-Logout (Cookie geloescht)
# in web/middleware/session_timeout.py. prompt=consent bleibt entfernt → stiller Re-Login.
JWT_EXPIRY_HOURS = 24

# Erlaubte Rollen und Benutzer — bevorzugt aus der .env, sonst aus config.json.
#
# Warum die ENV zuerst kommt: `config/config.json` ist im Repo getrackt, und
# das Repo wird oeffentlich gestellt. Discord-IDs sind zwar kein Geheimnis im
# engeren Sinne, verraten aber Server, Rollen und Personen — sie gehoeren zu
# den uebrigen Zugangsdaten in die `.env`, die nie getrackt wird.
#
# Der config.json-Weg bleibt als Rueckfall bestehen, damit bestehende
# Installationen nicht ueber Nacht ausgesperrt werden.
_config = get_config()


def _id_liste(env_name: str, config_key: str) -> list[str]:
    """Kommagetrennte IDs aus der ENV, sonst die Liste aus config.json."""
    roh = get_env(env_name, "")
    if roh:
        return [t.strip() for t in str(roh).split(",") if t.strip()]
    return _config.get(config_key, [])


WEB_ALLOWED_ROLE_IDS = _id_liste("WEB_ALLOWED_ROLE_IDS", "web_allowed_role_ids")
WEB_ALLOWED_USER_IDS = _id_liste("WEB_ALLOWED_USER_IDS", "web_allowed_user_ids")

# Eine leere Liste bedeutet in der Auswertung weiter unten NICHT "niemand",
# sondern "jedes Guild-Mitglied darf rein" (Zeile ~410). Genau das war der
# Zustand bis zum 2026-08-15, weil beide Schluessel in config.json fehlten.
# Deshalb hier eine deutliche Warnung beim Start statt eines stillen Defaults.
if not WEB_ALLOWED_ROLE_IDS and not WEB_ALLOWED_USER_IDS:
    logger.warning(
        "WEB_ALLOWED_ROLE_IDS und WEB_ALLOWED_USER_IDS sind beide leer — "
        "damit darf JEDES Guild-Mitglied ins Dashboard. In config/.env setzen."
    )


# --- Rate Limiting (einfach, dict-basiert) ---

# INVARIANTE (Review 2026-06-12): prozess-lokaler RAM-Store — Login-Bruteforce-
# Limit gilt nur korrekt bei uvicorn --workers 1 (so in
# systemd/web-dashboard.service konfiguriert). Bei >1 Worker verwaessert das
# Limit auf N x RATE_LIMIT_MAX → vorher gemeinsamen Store einfuehren.
# Struktur: { ip: [timestamp, timestamp, ...] }
_login_attempts: dict[str, list[float]] = {}
RATE_LIMIT_MAX = 5           # Maximal 5 Versuche
RATE_LIMIT_WINDOW = 900      # innerhalb von 15 Minuten (900 Sekunden)
# M36-Fix: zeitbasiertes Cleanup-Intervall — leere/abgelaufene IP-Keys nicht
# erst bei >1000 Eintraegen aufraeumen (Slow-Leak ueber lange Laufzeit).
_RL_CLEANUP_INTERVAL = 300   # alle 5 Minuten
_last_rl_cleanup = 0.0


def _cleanup_rate_limit_dict() -> None:
    """Entfernt abgelaufene IPs aus dem Rate-Limit-Dict (verhindert Memory-Leak)."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    expired = [ip for ip, attempts in _login_attempts.items()
               if not attempts or attempts[-1] < cutoff]
    for ip in expired:
        del _login_attempts[ip]


def _check_rate_limit(ip: str) -> bool:
    """
    Prueft ob die IP das Login-Limit ueberschritten hat.
    Gibt True zurueck wenn der Zugriff erlaubt ist, False wenn blockiert.
    """
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW

    # Periodisch alte IPs aufraeumen — bei >1000 Eintraegen ODER alle 5 Min (M36).
    global _last_rl_cleanup
    if len(_login_attempts) > 1000 or (now - _last_rl_cleanup) > _RL_CLEANUP_INTERVAL:
        _cleanup_rate_limit_dict()
        _last_rl_cleanup = now

    if ip not in _login_attempts:
        _login_attempts[ip] = []

    # Alte Eintraege entfernen
    _login_attempts[ip] = [t for t in _login_attempts[ip] if t > cutoff]

    if len(_login_attempts[ip]) >= RATE_LIMIT_MAX:
        return False

    return True


def _record_attempt(ip: str):
    """Zeichnet einen Login-Versuch fuer die gegebene IP auf."""
    now = time.time()
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append(now)


# --- JWT-Hilfsfunktionen ---

def _create_jwt(user_data: dict) -> str:
    """Erstellt ein JWT-Token mit Benutzerdaten und Ablaufzeit."""
    payload = {
        **user_data,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, WEB_SECRET_KEY, algorithm=JWT_ALGORITHM)


def _decode_jwt(token: str) -> Optional[dict]:
    """Dekodiert ein JWT-Token. Gibt None zurueck bei ungueltigem Token.

    Prueft zusaetzlich die Abmelde-Sperre: ein Token, das vor der letzten
    Abmeldung des Nutzers ausgestellt wurde, gilt nicht mehr. Ohne diesen
    Schritt bliebe ein kopiertes Cookie bis zum Ablauf gueltig — Abmelden
    loescht es nur im Browser.
    """
    try:
        payload = jwt.decode(token, WEB_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
    if session_invalidation.ist_abgemeldet(payload.get("sub"), payload.get("iat")):
        return None
    return payload


# --- Oeffentliche Hilfsfunktionen ---

def get_current_user(request: Request) -> Optional[dict]:
    """
    Liest den aktuellen Benutzer aus dem JWT-Cookie.
    Gibt ein User-Dict zurueck oder None falls nicht angemeldet.
    """
    token = request.cookies.get("dashboard_token")
    if not token:
        return None
    return _decode_jwt(token)


def get_ws_user(websocket) -> Optional[dict]:
    """
    Authentifiziert eine WebSocket-Verbindung über das JWT-Cookie.

    Pendant zu require_auth_api für WebSockets (der WS-Push-Kanal des Dashboards
    streamt Server-/System-/Bot-Daten und muss wie die alten SSE-Endpoints
    auth-gated sein). Gibt User-Dict oder None (Caller schließt die Verbindung).
    """
    token = websocket.cookies.get("dashboard_token")
    if not token:
        return None
    return _decode_jwt(token)


async def require_auth(request: Request):
    """
    FastAPI-Dependency fuer HTML-Endpoints: Leitet zur Login-Seite weiter wenn nicht angemeldet.
    Gibt den User-Dict zurueck wenn angemeldet.

    Verwendung: HTML-Seiten (z. B. /config, /marshal-bot, /dashboard).
    Browser folgt der 303-Redirect-Header automatisch zur Login-Seite.
    """
    user = get_current_user(request)
    if user is None:
        raise HTTPException(
            status_code=303,
            headers={"Location": "/auth/login"}
        )
    return user


async def require_auth_api(request: Request) -> dict:
    """
    FastAPI-Dependency fuer JSON-API-Endpoints: gibt HTTP 401 JSON zurueck wenn nicht angemeldet.

    Verwendung: alle /api/*-Endpoints (z. B. /api/analytics/*, /api/export/*, /api/system/*).
    JSON-Clients (HTMX, Fetch, curl) erhalten klares 401 statt 303-Redirect.
    """
    user = get_current_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Nicht authentifiziert",
        )
    return user


def require_perm(resource: str, action: str):
    """
    Dependency-Factory fuer RBAC: erzwingt das Recht (`resource`, `action`).

    Server-seitig autoritativ (Spec §3). Verwendung an HTML-Routen:
        @router.get("/rbac", dependencies=[Depends(require_perm("rbac", "edit"))])
        # oder als Wert: current_user = Depends(require_perm("audit", "view"))

    Verhalten:
      - nicht eingeloggt  -> 303 Redirect /auth/login (HTML-Flow, Browser folgt)
      - eingeloggt, aber kein Recht -> 403
      - Owner -> immer erlaubt (modules.rbac.has_perm)

    Die eigentliche Permission-Logik liegt in `modules.rbac` (Owner=alles,
    Member-Default=view auf nicht-sensible Bereiche, sonst Rollen-Grants aus
    `rbac_role_map`). Import erfolgt lazy, um die Import-Kette beim App-Start
    schlank zu halten.
    """
    async def _perm_dependency(request: Request) -> dict:
        user = get_current_user(request)
        if user is None:
            raise HTTPException(
                status_code=303,
                headers={"Location": "/auth/login"},
            )
        from modules.rbac import has_perm
        if not await has_perm(user, resource, action):
            raise HTTPException(
                status_code=403,
                detail="Keine Berechtigung fuer diese Aktion",
            )
        return user

    return _perm_dependency


def allow_anon():
    """
    Markiert eine Route bewusst als public (kein Auth).

    Verwendung als Dependency-Comment-Marker:
        router = APIRouter(dependencies=[Depends(allow_anon)])
        # oder pro Endpoint mit Dokumentations-Kommentar

    Wird nicht enforced — dient nur als expliziter Marker dass die Route
    intentional unauthenticated ist (Health-Checks, HMAC-authenticated Webhooks).
    """
    return None


# --- Login-Seite ---

@auth_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = "", reason: str = ""):
    """Zeigt die Login-Seite an. `reason` (inactive/timeout) → Sitzungs-Ablauf-Hinweis."""
    oauth_configured = bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET)
    return templates.TemplateResponse(request, "login.html", {
        "request": request,
        "error": error,
        "reason": reason,
        "oauth_configured": oauth_configured,
    })


# --- Discord OAuth2 Flow ---

@auth_router.get("/discord")
async def discord_oauth_redirect(request: Request):
    """Leitet den Benutzer zur Discord-OAuth2-Autorisierung weiter."""
    if not DISCORD_CLIENT_ID:
        return RedirectResponse(url="/auth/login?error=OAuth2+nicht+konfiguriert", status_code=302)

    # State-Token gegen CSRF generieren und in Session speichern
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state

    # KEIN prompt=consent (Marco 2026-07-14): sonst zeigt Discord bei JEDEM Login den
    # Autorisierungs-Dialog erneut. Ohne prompt merkt Discord die Zustimmung → stiller
    # Re-Login (Guild-/Rollen-Check laeuft weiter, Auth-Sicherheit unveraendert).
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds guilds.members.read",
        "state": state,
    }
    query = "&".join(f"{k}={httpx.QueryParams({k: v})}" for k, v in params.items())
    # Saubere URL zusammenbauen
    auth_url = f"{DISCORD_AUTH_URL}?client_id={DISCORD_CLIENT_ID}"
    auth_url += f"&redirect_uri={DISCORD_REDIRECT_URI}"
    auth_url += f"&response_type=code&scope=identify+guilds+guilds.members.read"
    auth_url += f"&state={state}"

    logger.info("Benutzer wird zu Discord OAuth2 weitergeleitet")
    return RedirectResponse(url=auth_url, status_code=302)


@auth_router.get("/discord/callback")
async def discord_oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Verarbeitet den OAuth2-Callback von Discord."""
    if error:
        logger.warning(f"Discord OAuth2 Fehler: {error}")
        return RedirectResponse(url="/auth/login?error=Discord+Autorisierung+abgelehnt", status_code=302)

    # CSRF State pruefen
    stored_state = request.session.pop("oauth_state", None)
    if not stored_state or stored_state != state:
        logger.warning("OAuth2 State-Mismatch — moeglicher CSRF-Angriff")
        return RedirectResponse(url="/auth/login?error=Ungueltiger+State", status_code=302)

    if not code:
        return RedirectResponse(url="/auth/login?error=Kein+Code+erhalten", status_code=302)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            # Access-Token anfordern
            token_resp = await client.post(DISCORD_TOKEN_URL, data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            }, headers={"Content-Type": "application/x-www-form-urlencoded"})

            if token_resp.status_code != 200:
                logger.error(f"Token-Anfrage fehlgeschlagen: {token_resp.status_code}")
                return RedirectResponse(url="/auth/login?error=Token-Anfrage+fehlgeschlagen", status_code=302)

            token_data = token_resp.json()
            access_token = token_data.get("access_token")

            if not access_token:
                return RedirectResponse(url="/auth/login?error=Kein+Access-Token+erhalten", status_code=302)

            headers = {"Authorization": f"Bearer {access_token}"}

            # Benutzer-Informationen abrufen
            user_resp = await client.get(f"{DISCORD_API_URL}/users/@me", headers=headers)
            if user_resp.status_code != 200:
                return RedirectResponse(url="/auth/login?error=Benutzerinfo+fehlgeschlagen", status_code=302)

            discord_user = user_resp.json()
            user_id = discord_user.get("id", "")
            username = discord_user.get("username", "Unbekannt")
            avatar_hash = discord_user.get("avatar", "")

            # Avatar-URL zusammenbauen
            if avatar_hash:
                avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png?size=64"
            else:
                avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"

            # Guild-Mitgliedschaft und Rollen pruefen (falls GUILD_ID gesetzt)
            is_authorized = False
            # Discord-Rollen-IDs des Users — fuer RBAC (modules/rbac.py).
            # Werden ins JWT geschrieben, damit require_perm sie ohne erneuten
            # Discord-Call kennt.
            member_roles: list = []

            # Direkter User-ID Check
            if str(user_id) in [str(uid) for uid in WEB_ALLOWED_USER_IDS]:
                is_authorized = True

            # Guild-Mitgliedschaft + Rollen holen (immer wenn GUILD_ID gesetzt —
            # auch fuer schon per User-ID autorisierte User, damit RBAC die
            # Rollen kennt). Die Autorisierungs-Logik bleibt unveraendert.
            if GUILD_ID:
                member_resp = await client.get(
                    f"{DISCORD_API_URL}/users/@me/guilds/{GUILD_ID}/member",
                    headers=headers
                )
                if member_resp.status_code == 200:
                    member_data = member_resp.json()
                    member_roles = member_data.get("roles", []) or []

                    # Rollen-Check: Hat der Benutzer eine erlaubte Rolle?
                    if not is_authorized:
                        if WEB_ALLOWED_ROLE_IDS:
                            for role_id in member_roles:
                                if str(role_id) in [str(r) for r in WEB_ALLOWED_ROLE_IDS]:
                                    is_authorized = True
                                    break
                        else:
                            # Keine Rollen konfiguriert — jedes Guild-Mitglied darf rein
                            is_authorized = True
                elif not is_authorized:
                    logger.warning(f"Benutzer {username} ({user_id}) ist kein Mitglied der Guild {GUILD_ID}")

            # Fallback: Wenn keine Guild konfiguriert und keine User-IDs, erlauben
            if not GUILD_ID and not WEB_ALLOWED_USER_IDS:
                is_authorized = True

            if not is_authorized:
                logger.warning(f"Zugriff verweigert fuer Discord-Benutzer {username} ({user_id})")
                return RedirectResponse(
                    url="/auth/login?error=Keine+Berechtigung.+Fehlende+Rolle+oder+Guild-Mitgliedschaft.",
                    status_code=302
                )

            # JWT erstellen und als Cookie setzen
            jwt_data = {
                "sub": str(user_id),
                "username": username,
                "avatar": avatar_url,
                "auth_method": "discord",
                "is_owner": str(user_id) == str(get_env("OWNER_ID", "")),
                # Discord-Rollen-IDs fuer RBAC (modules/rbac.py / require_perm)
                "roles": [str(r) for r in member_roles],
            }
            token = _create_jwt(jwt_data)
            # Idle-Uhr zuruecksetzen — sonst bounct die Session-Timeout-Middleware direkt
            # nach dem Re-Login erneut zur Login-Seite (last_seen waere noch alt).
            request.session["last_seen"] = time.time()

            logger.info(f"Discord-Login erfolgreich: {username} ({user_id})")

            response = RedirectResponse(url="/home", status_code=302)
            response.set_cookie(
                key="dashboard_token",
                value=token,
                httponly=True,
                samesite="lax",
                max_age=JWT_EXPIRY_HOURS * 3600,
                secure=WEB_HTTPS,
            )
            return response

    except Exception as e:
        logger.error(f"OAuth2-Callback Fehler: {e}")
        return RedirectResponse(url="/auth/login?error=Interner+Fehler", status_code=302)


# --- Fallback: Benutzername/Passwort Login ---

@auth_router.post("/login")
async def login_post(request: Request, username: str = Form(""), password: str = Form("")):
    """Verarbeitet den Fallback-Login mit Benutzername und Passwort (bcrypt)."""
    client_ip = client_ip_from_scope(request.scope)  # B3/M15-Fix: trust-bewusst

    # Rate-Limit pruefen
    if not _check_rate_limit(client_ip):
        logger.warning(f"Rate-Limit erreicht fuer IP {client_ip}")
        return templates.TemplateResponse(request, "login.html", {
            "request": request,
            "error": "Zu viele Login-Versuche. Bitte 15 Minuten warten.",
            "oauth_configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
        }, status_code=429)

    _record_attempt(client_ip)

    # B2/M13-Fix: Fallback-Login nur bei explizit gesetztem Flag (Owner-Total-Compromise-Schutz)
    if not WEB_FALLBACK_LOGIN:
        logger.warning(f"[AUDIT] Fallback-Login-Versuch bei deaktiviertem Flag von {client_ip}")
        return templates.TemplateResponse(request, "login.html", {
            "request": request,
            "error": "Passwort-Login ist deaktiviert. Bitte ueber Discord anmelden.",
            "oauth_configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
        }, status_code=403)

    if not username or not password:
        return templates.TemplateResponse(request, "login.html", {
            "request": request,
            "error": "Benutzername und Passwort erforderlich.",
            "oauth_configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
        }, status_code=400)

    # Benutzername pruefen
    if username != WEB_ADMIN_USER:
        logger.warning(f"Fehlgeschlagener Login-Versuch: Benutzer '{username}' von {client_ip}")
        return templates.TemplateResponse(request, "login.html", {
            "request": request,
            "error": "Ungueltiger Benutzername oder Passwort.",
            "oauth_configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
        }, status_code=401)

    # Passwort mit bcrypt pruefen
    if not WEB_ADMIN_PASS_HASH:
        logger.error("WEB_ADMIN_PASS_HASH ist nicht gesetzt — Fallback-Login deaktiviert")
        return templates.TemplateResponse(request, "login.html", {
            "request": request,
            "error": "Passwort-Login ist nicht konfiguriert.",
            "oauth_configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
        }, status_code=500)

    try:
        import bcrypt
        if not bcrypt.checkpw(password.encode("utf-8"), WEB_ADMIN_PASS_HASH.encode("utf-8")):
            logger.warning(f"Falsches Passwort fuer '{username}' von {client_ip}")
            return templates.TemplateResponse(request, "login.html", {
                "request": request,
                "error": "Ungueltiger Benutzername oder Passwort.",
                "oauth_configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
            }, status_code=401)
    except Exception as e:
        logger.error(f"bcrypt-Fehler: {e}")
        return templates.TemplateResponse(request, "login.html", {
            "request": request,
            "error": "Interner Fehler bei der Passwort-Pruefung.",
            "oauth_configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
        }, status_code=500)

    # Login erfolgreich — JWT erstellen
    jwt_data = {
        "sub": f"local:{username}",
        "username": username,
        "avatar": "",
        "auth_method": "password",
        "is_owner": True,  # Fallback-Login ist immer Owner
    }
    token = _create_jwt(jwt_data)
    request.session["last_seen"] = time.time()  # Idle-Uhr zuruecksetzen (siehe OAuth-Callback)

    # B2/M13-Fix: Fallback-Login (Owner-Rechte) prominent auditieren
    logger.warning(f"[AUDIT] Fallback-Passwort-Login mit Owner-Rechten: {username} von {client_ip}")
    try:
        from modules.dashboard_audit import log_action
        await log_action(
            discord_id=f"local:{username}", username=username,
            resource="system", action="control",
            detail={"event": "fallback_login"}, ip=client_ip,
        )
    except Exception as e:  # noqa: BLE001 — Audit darf den Login nicht kippen
        logger.debug(f"Audit-Log Fallback-Login fehlgeschlagen: {e}")

    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="dashboard_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=JWT_EXPIRY_HOURS * 3600,
        secure=WEB_HTTPS,
    )
    return response


# --- Logout ---

@auth_router.post("/logout")
async def logout(request: Request):
    """Meldet den Benutzer ab — auf allen Geraeten.

    POST statt GET: als GET liess sich die Abmeldung von fremden Seiten
    ausloesen (`<img src=".../auth/logout">`), und die CSRF-Middleware greift
    nur bei schreibenden Methoden.
    """
    user = get_current_user(request)
    if user:
        # Erst die Sperre setzen, dann das Cookie loeschen. Umgekehrt bliebe
        # ein Zeitfenster, in dem ein kopiertes Token noch gilt.
        session_invalidation.abmelden(str(user.get("sub", "")))
        logger.info(f"Benutzer abgemeldet: {user.get('username', 'Unbekannt')}")

    response = RedirectResponse(url="/auth/login", status_code=303)
    response.delete_cookie("dashboard_token")
    request.session.clear()
    return response

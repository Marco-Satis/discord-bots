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
from fastapi.templating import Jinja2Templates

from utils.config import load_env, get_env, get_config
from utils.logger import get_logger

logger = get_logger("web.auth")

# Templates-Verzeichnis
TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

auth_router = APIRouter(prefix="/auth", tags=["Authentifizierung"])

# --- Konfiguration aus Umgebungsvariablen ---

DISCORD_CLIENT_ID = get_env("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = get_env("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = get_env("DISCORD_REDIRECT_URI", "http://localhost:8080/auth/discord/callback")
WEB_SECRET_KEY = get_env("WEB_SECRET_KEY", "")
if not WEB_SECRET_KEY:
    import secrets as _secrets
    WEB_SECRET_KEY = _secrets.token_hex(32)
    logger.warning("WEB_SECRET_KEY nicht in .env gesetzt — temporaerer Key generiert.")
WEB_ADMIN_USER = get_env("WEB_ADMIN_USER", "admin")
WEB_ADMIN_PASS_HASH = get_env("WEB_ADMIN_PASS_HASH", "")
GUILD_ID = get_env("GUILD_ID", "")
WEB_HTTPS = get_env("WEB_HTTPS", "true", cast=bool)

# Discord OAuth2 Endpunkte
DISCORD_AUTH_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"  # nosec B105 - oeffentliche OAuth2-Endpoint-URL, kein Secret
DISCORD_API_URL = "https://discord.com/api/v10"

# JWT Konfiguration
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

# Erlaubte Rollen und Benutzer aus config.json
_config = get_config()
WEB_ALLOWED_ROLE_IDS = _config.get("web_allowed_role_ids", [])
WEB_ALLOWED_USER_IDS = _config.get("web_allowed_user_ids", [])


# --- Rate Limiting (einfach, dict-basiert) ---

# Struktur: { ip: [timestamp, timestamp, ...] }
_login_attempts: dict[str, list[float]] = {}
RATE_LIMIT_MAX = 5           # Maximal 5 Versuche
RATE_LIMIT_WINDOW = 900      # innerhalb von 15 Minuten (900 Sekunden)


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

    # Periodisch alte IPs aufraeumen (alle 100 Aufrufe oder bei >1000 Eintraegen)
    if len(_login_attempts) > 1000:
        _cleanup_rate_limit_dict()

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
    """Dekodiert ein JWT-Token. Gibt None zurueck bei ungueltigem Token."""
    try:
        return jwt.decode(token, WEB_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


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


async def require_auth(request: Request):
    """
    FastAPI-Dependency: Leitet zur Login-Seite weiter wenn nicht angemeldet.
    Gibt den User-Dict zurueck wenn angemeldet.
    """
    user = get_current_user(request)
    if user is None:
        raise HTTPException(
            status_code=303,
            headers={"Location": "/auth/login"}
        )
    return user


# --- Login-Seite ---

@auth_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    """Zeigt die Login-Seite an."""
    oauth_configured = bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": error,
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

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds guilds.members.read",
        "state": state,
        "prompt": "consent",
    }
    query = "&".join(f"{k}={httpx.QueryParams({k: v})}" for k, v in params.items())
    # Saubere URL zusammenbauen
    auth_url = f"{DISCORD_AUTH_URL}?client_id={DISCORD_CLIENT_ID}"
    auth_url += f"&redirect_uri={DISCORD_REDIRECT_URI}"
    auth_url += f"&response_type=code&scope=identify+guilds+guilds.members.read"
    auth_url += f"&state={state}&prompt=consent"

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

            # Direkter User-ID Check
            if str(user_id) in [str(uid) for uid in WEB_ALLOWED_USER_IDS]:
                is_authorized = True

            # Guild-Mitgliedschaft pruefen
            if GUILD_ID and not is_authorized:
                member_resp = await client.get(
                    f"{DISCORD_API_URL}/users/@me/guilds/{GUILD_ID}/member",
                    headers=headers
                )
                if member_resp.status_code == 200:
                    member_data = member_resp.json()
                    member_roles = member_data.get("roles", [])

                    # Rollen-Check: Hat der Benutzer eine erlaubte Rolle?
                    if WEB_ALLOWED_ROLE_IDS:
                        for role_id in member_roles:
                            if str(role_id) in [str(r) for r in WEB_ALLOWED_ROLE_IDS]:
                                is_authorized = True
                                break
                    else:
                        # Keine Rollen konfiguriert — jedes Guild-Mitglied darf rein
                        is_authorized = True
                else:
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
            }
            token = _create_jwt(jwt_data)

            logger.info(f"Discord-Login erfolgreich: {username} ({user_id})")

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

    except Exception as e:
        logger.error(f"OAuth2-Callback Fehler: {e}")
        return RedirectResponse(url="/auth/login?error=Interner+Fehler", status_code=302)


# --- Fallback: Benutzername/Passwort Login ---

@auth_router.post("/login")
async def login_post(request: Request, username: str = Form(""), password: str = Form("")):
    """Verarbeitet den Fallback-Login mit Benutzername und Passwort (bcrypt)."""
    client_ip = request.client.host if request.client else "unknown"

    # Rate-Limit pruefen
    if not _check_rate_limit(client_ip):
        logger.warning(f"Rate-Limit erreicht fuer IP {client_ip}")
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Zu viele Login-Versuche. Bitte 15 Minuten warten.",
            "oauth_configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
        }, status_code=429)

    _record_attempt(client_ip)

    if not username or not password:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Benutzername und Passwort erforderlich.",
            "oauth_configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
        }, status_code=400)

    # Benutzername pruefen
    if username != WEB_ADMIN_USER:
        logger.warning(f"Fehlgeschlagener Login-Versuch: Benutzer '{username}' von {client_ip}")
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Ungueltiger Benutzername oder Passwort.",
            "oauth_configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
        }, status_code=401)

    # Passwort mit bcrypt pruefen
    if not WEB_ADMIN_PASS_HASH:
        logger.error("WEB_ADMIN_PASS_HASH ist nicht gesetzt — Fallback-Login deaktiviert")
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Passwort-Login ist nicht konfiguriert.",
            "oauth_configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
        }, status_code=500)

    try:
        import bcrypt
        if not bcrypt.checkpw(password.encode("utf-8"), WEB_ADMIN_PASS_HASH.encode("utf-8")):
            logger.warning(f"Falsches Passwort fuer '{username}' von {client_ip}")
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Ungueltiger Benutzername oder Passwort.",
                "oauth_configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
            }, status_code=401)
    except Exception as e:
        logger.error(f"bcrypt-Fehler: {e}")
        return templates.TemplateResponse("login.html", {
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

    logger.info(f"Passwort-Login erfolgreich: {username} von {client_ip}")

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

@auth_router.get("/logout")
async def logout(request: Request):
    """Meldet den Benutzer ab und loescht die Session."""
    user = get_current_user(request)
    if user:
        logger.info(f"Benutzer abgemeldet: {user.get('username', 'Unbekannt')}")

    response = RedirectResponse(url="/auth/login", status_code=302)
    response.delete_cookie("dashboard_token")
    request.session.clear()
    return response

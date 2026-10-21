"""
Phase 13a/13g: Konfigurations-Panel — Bearbeitung der config.json

Ermöglicht das Anzeigen und Ändern der Bot-Konfiguration
über das Web-Dashboard. Nur für Owner zugänglich.

Phase 13g erweitert:
  - Benachrichtigungs-Routing (Notification Matrix)
  - Login-Verwaltung (Berechtigte Rollen und Benutzer)
  - Bot-Profile (Namen, Status, Avatare)
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from web.templates_setup import erstelle_templates

from utils.config import get_config, get_env, save_config
from utils.logger import get_logger
from web.auth import require_auth

logger = get_logger("web.routes.config")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)

router = APIRouter(tags=["Konfiguration"], dependencies=[Depends(require_auth)])

# --- Standard-Benachrichtigungs-Routing ---
DEFAULT_NOTIFICATION_ROUTING = {
    "server_start": {"channel": "admin", "email": False},
    "server_stop": {"channel": "admin", "email": False},
    "server_crash": {"channel": "admin", "email": True},
    "backup_success": {"channel": "log", "email": False},
    "backup_failed": {"channel": "admin", "email": True},
    "player_join": {"channel": "log", "email": False},
    "player_leave": {"channel": "log", "email": False},
    "mod_warn": {"channel": "mod", "email": False},
    "mod_ban": {"channel": "mod", "email": False},
    "cpu_warning": {"channel": "admin", "email": True},
    "disk_warning": {"channel": "admin", "email": True},
    "update_available": {"channel": "spieler", "email": False},
    "config_change": {"channel": "admin", "email": False},
}

# --- Standard-Bot-Profile ---
DEFAULT_BOT_PROFILES = {
    "gameserver": {"display_name": "GameServer Bot", "status": "Verwaltet Server", "avatar_url": ""},
    "monitor": {"display_name": "Monitor Bot", "status": "Überwacht alles", "avatar_url": ""},
    "admin": {"display_name": "Admin Bot", "status": "Community Management", "avatar_url": ""},
}

# --- Erlaubte Kanal-Werte für Benachrichtigungen ---
ALLOWED_CHANNELS = ["admin", "spieler", "log", "mod", "aus"]

# --- Kategorien für Benachrichtigungs-Ereignisse (für Gruppierung) ---
NOTIFICATION_CATEGORIES = {
    "Server": ["server_start", "server_stop", "server_crash"],
    "Backups": ["backup_success", "backup_failed"],
    "Spieler": ["player_join", "player_leave"],
    "Moderation": ["mod_warn", "mod_ban"],
    "Performance": ["cpu_warning", "disk_warning"],
    "Updates": ["update_available"],
    "System": ["config_change"],
}

# --- Lesbare Event-Labels ---
NOTIFICATION_LABELS = {
    "server_start": "Server gestartet",
    "server_stop": "Server gestoppt",
    "server_crash": "Server-Absturz",
    "backup_success": "Backup erfolgreich",
    "backup_failed": "Backup fehlgeschlagen",
    "player_join": "Spieler beigetreten",
    "player_leave": "Spieler verlassen",
    "mod_warn": "Moderation: Warnung",
    "mod_ban": "Moderation: Bann",
    "cpu_warning": "CPU-Warnung",
    "disk_warning": "Speicherplatz-Warnung",
    "update_available": "Update verfügbar",
    "config_change": "Konfigurationsänderung",
}


def _is_owner(user: dict) -> bool:
    """Prüft ob der Benutzer Owner-Rechte hat."""
    return user.get("is_owner") in (True, "True", "true")


# --- Einstellungs-Seiten (je Bereich eine eigene Seite, Reihenfolge = Navigation) ---
EINSTELLUNGEN = {
    "toggles": ("Funktionen", "Welche Bot-Funktionen laufen. Änderungen gelten sofort."),
    "intervals": ("Zeitpläne", "Neustart, Backups, Update-Prüfung und Countdowns."),
    "thresholds": ("Warnschwellen", "Ab welchen Werten die Bots im Admin-Log warnen."),
    "channels": ("Kanäle der Bots", "Wohin die Bots schreiben."),
    "notifications": ("Benachrichtigungen", "Welches Ereignis in welchem Kanal landet."),
    "botprofiles": ("Bot-Profile", "Namen, Status und Avatare der Bots."),
}

# Funktionsschalter auf der Seite „Funktionen“. word_filter und anti_spam fehlen
# bewusst: sie werden unter Automod (/moderation) je Server geschaltet, und diese
# Regel ueberstimmt den globalen Schalter (cogs/moderation_cog.py:_rule_on). Zwei
# Schalter fuer dieselbe Sache waren eine Falle; der globale Wert bleibt unangetastet.
FEATURE_KEYS = [
    "player_tracking", "auto_backup", "onedrive_backup",
    "email_notifications", "auto_update", "daily_restart",
    "voice_stats", "status_embed", "chat_bridge",
    "login_audit", "auto_cleanup", "steam_changelog",
]
# Audit 2026-08-18 (C-10, Marco-Entscheid): `savegame_protection` und
# `graceful_degradation` stehen hier bewusst NICHT. Beide sind Sicherheitsnetze —
# der Speicherstand-Schutz greift bei Absturzschleifen, die Degradation haelt den
# Betrieb bei Teilausfaellen aufrecht. Ein Kaestchen, das sie abschaltet, ist eine
# Fussangel: der Schaden faellt erst auf, wenn das Netz gebraucht wird.

THRESHOLD_KEYS = ["cpu_warning", "ram_warning", "disk_warning", "disk_critical",
                  "tick_rate_warning", "warning_cooldown"]

SCHEDULER_INT_KEYS = [
    "daily_restart_hour", "daily_restart_minute",
    "min_uptime_for_restart_hours",
    "auto_backup_interval_hours", "max_local_backups",
    "max_cloud_backups", "update_check_interval_hours",
    "auto_update_hour", "daily_report_hour",
    "weekly_report_day", "weekly_report_hour",
    "config_backup_hour", "auto_cleanup_hour",
    "backup_verify_day", "backup_verify_hour",
]
SCHEDULER_BOOL_KEYS = [
    "daily_restart_enabled", "auto_backup_enabled",
    "update_check_enabled", "auto_update_enabled",
    "auto_update_require_empty", "daily_report_enabled",
    "config_backup_enabled", "auto_cleanup_enabled",
    "backup_before_restart",
]


def _bereich(wert: str) -> str:
    """Gueltigen Einstellungs-Bereich bestimmen (Default: Funktionen)."""
    return wert if wert in EINSTELLUNGEN else "toggles"


def _bot_kanaele() -> list[dict]:
    """
    Kanaele, die die Bots tatsaechlich benutzen (aus der .env), mit Namen aus dem
    Kanal-Snapshot. IDs werden nie angezeigt — nur Name oder Zustand.
    """
    from modules.server_registry import minecraft_server
    from web.channel_bridge import get_channels

    namen = {str(c.get("id")): c.get("name", "") for c in get_channels().get("channels", [])}
    eintraege = [
        ("ADMIN_LOG_CHANNEL_ID", "Admin-Log", "Fehler, Warnungen, Neustarts und Backups aller Bots"),
        ("STATUS_EMBED_CHANNEL_ID", "Live-Status", "Status-Anzeige der Game-Server"),
        ("PUBLIC_STATUS_CHANNEL_ID", "Satisfactory-Spielerkanal", "Update-Hinweise für Satisfactory-Spieler"),
    ]
    for srv in minecraft_server():
        eintraege.append((f"{srv.env_praefix}GAME_CHAT_CHANNEL_ID", f"Chat-Brücke {srv.label}",
                          "Spiel-Chat ↔ Discord"))

    kanaele = []
    for env_key, titel, zweck in eintraege:
        wert = str(get_env(env_key, "") or "").strip()
        if not wert or wert == "0":
            name, zustand = "", "fehlt"
        elif wert in namen:
            name, zustand = namen[wert], "ok"
        else:
            name, zustand = "", "unbekannt"
        kanaele.append({"titel": titel, "zweck": zweck, "name": name, "zustand": zustand})
    return kanaele


def _seite(request: Request, user: dict, bereich: str, config: dict,
           success: str = "", error: str = "") -> HTMLResponse:
    """Eine Einstellungs-Seite rendern."""
    titel, lead = EINSTELLUNGEN[bereich]
    return templates.TemplateResponse(request, "config.html", {
        "request": request,
        "user": user,
        "config": config,
        "bereich": bereich,
        "titel": titel,
        "lead": lead,
        "kanaele": _bot_kanaele() if bereich == "channels" else [],
        "success": success,
        "error": error,
    })


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request, current_user: dict = Depends(require_auth)):
    """Eine Einstellungs-Seite (?tab=<bereich>). Nur für Owner zugänglich."""
    if not _is_owner(current_user):
        raise HTTPException(403, "Nur der Owner darf die Konfiguration aendern")
    bereich = _bereich(request.query_params.get("tab", ""))
    return _seite(request, current_user, bereich, get_config())


def _uebernehmen(bereich: str, form, config: dict) -> None:
    """
    Formularwerte EINES Bereichs in die Konfiguration schreiben.

    Nur die Felder des abgeschickten Bereichs werden angefasst: Checkboxen fehlen
    im Formular, wenn sie aus sind — wuerde jeder Bereich alle Schalter lesen,
    schaltete das Speichern einer anderen Seite sie still ab.
    """
    if bereich == "toggles":
        features = config.setdefault("features", {})
        for key in FEATURE_KEYS:
            features[key] = f"features.{key}" in form

    elif bereich == "thresholds":
        thresholds = config.setdefault("thresholds", {})
        for key in THRESHOLD_KEYS:
            field_name = f"thresholds.{key}"
            if field_name in form:
                try:
                    thresholds[key] = int(form[field_name])
                except (ValueError, TypeError):
                    pass  # Ungueltige Werte ignorieren

    elif bereich == "intervals":
        scheduler = config.setdefault("scheduler", {})
        for key in SCHEDULER_INT_KEYS:
            field_name = f"scheduler.{key}"
            if field_name in form:
                try:
                    scheduler[key] = int(form[field_name])
                except (ValueError, TypeError):
                    pass
        for key in SCHEDULER_BOOL_KEYS:
            scheduler[key] = f"scheduler.{key}" in form

        restart_timer = config.setdefault("restart_timer", {})
        for key in ("default_duration", "stop_duration"):
            field_name = f"restart_timer.{key}"
            if field_name in form:
                try:
                    restart_timer[key] = int(form[field_name])
                except (ValueError, TypeError):
                    pass

        config["auto_restart"] = "auto_restart" in form
        if "restart_delay" in form:
            try:
                config["restart_delay"] = int(form["restart_delay"])
            except (ValueError, TypeError):
                pass


@router.post("/config", response_class=HTMLResponse)
async def config_save(request: Request, current_user: dict = Depends(require_auth)):
    """Speichert die Felder EINES Einstellungs-Bereichs in config.json."""
    if not _is_owner(current_user):
        raise HTTPException(403, "Nur der Owner darf die Konfiguration aendern")

    bereich = "toggles"
    try:
        form = await request.form()
        bereich = _bereich(str(form.get("bereich", "")))
        if bereich not in ("toggles", "thresholds", "intervals"):
            raise ValueError(f"Bereich {bereich} wird nicht ueber /config gespeichert")
        config = get_config()
        _uebernehmen(bereich, form, config)
        save_config(config)
        logger.info(f"Konfiguration ({bereich}) gespeichert von {current_user.get('username', 'Unbekannt')}")
        return _seite(request, current_user, bereich, config, success="Gespeichert. Die Bots übernehmen es sofort.")

    except Exception as e:
        logger.error(f"Fehler beim Speichern der Konfiguration: {e}")
        return _seite(request, current_user, bereich, get_config(),
                      error="Fehler beim Speichern der Einstellungen. Details im Server-Log.")


# ==============================================================
#  Phase 13g: Benachrichtigungs-Routing
# ==============================================================

@router.get("/config/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request, current_user: dict = Depends(require_auth)):
    """Benachrichtigungs-Routing Matrix — zeigt alle Event-Typen mit Kanal- und E-Mail-Zuordnung."""
    if not _is_owner(current_user):
        raise HTTPException(403, "Nur der Owner darf das Benachrichtigungs-Routing aendern")

    config = get_config()
    routing = config.get("notification_routing", DEFAULT_NOTIFICATION_ROUTING)

    return templates.TemplateResponse(request, "partials/config_notifications.html", {
        "request": request,
        "user": current_user,
        "routing": routing,
        "categories": NOTIFICATION_CATEGORIES,
        "labels": NOTIFICATION_LABELS,
        "channels": ALLOWED_CHANNELS,
        "success": "",
        "error": "",
    })


@router.post("/config/notifications", response_class=HTMLResponse)
async def save_notifications(request: Request, current_user: dict = Depends(require_auth)):
    """Speichert Benachrichtigungs-Routing in config.json."""
    if not _is_owner(current_user):
        raise HTTPException(403, "Nur der Owner darf das Benachrichtigungs-Routing aendern")

    user = current_user
    try:
        form = await request.form()
        config = get_config()

        routing = {}
        for event_key in DEFAULT_NOTIFICATION_ROUTING:
            channel_field = f"routing.{event_key}.channel"
            email_field = f"routing.{event_key}.email"

            channel = form.get(channel_field, "aus")
            if channel not in ALLOWED_CHANNELS:
                channel = "aus"

            routing[event_key] = {
                "channel": channel,
                "email": email_field in form,
            }

        config["notification_routing"] = routing
        save_config(config)
        logger.info(f"Benachrichtigungs-Routing gespeichert von {user.get('username', 'Unbekannt')}")

        return templates.TemplateResponse(request, "partials/config_notifications.html", {
            "request": request,
            "user": user,
            "routing": routing,
            "categories": NOTIFICATION_CATEGORIES,
            "labels": NOTIFICATION_LABELS,
            "channels": ALLOWED_CHANNELS,
            "success": "Benachrichtigungs-Routing erfolgreich gespeichert.",
            "error": "",
        })

    except Exception as e:
        logger.error(f"Fehler beim Speichern des Benachrichtigungs-Routings: {e}")
        config = get_config()
        routing = config.get("notification_routing", DEFAULT_NOTIFICATION_ROUTING)
        return templates.TemplateResponse(request, "partials/config_notifications.html", {
            "request": request,
            "user": user,
            "routing": routing,
            "categories": NOTIFICATION_CATEGORIES,
            "labels": NOTIFICATION_LABELS,
            "channels": ALLOWED_CHANNELS,
            "success": "",
            "error": "Fehler beim Speichern der Einstellungen. Details im Server-Log.",
        })


# ==============================================================
#  Phase 13g: Login-Verwaltung
# ==============================================================

@router.get("/zugang", response_class=HTMLResponse)
async def zugang_page(request: Request, current_user: dict = Depends(require_auth)):
    """Eigene Seite „Wer sich anmelden darf“ (Inhalt lädt per HTMX aus /config/login)."""
    if not _is_owner(current_user):
        raise HTTPException(403, "Nur der Owner darf die Login-Verwaltung anpassen")
    return templates.TemplateResponse(request, "zugang.html", {
        "request": request,
        "user": current_user,
    })


@router.get("/config/login", response_class=HTMLResponse)
async def login_management(request: Request, current_user: dict = Depends(require_auth)):
    """Dashboard-Login-Verwaltung — Berechtigte Rollen und Benutzer anzeigen."""
    if not _is_owner(current_user):
        raise HTTPException(403, "Nur der Owner darf die Login-Verwaltung anpassen")

    config = get_config()
    allowed_roles = config.get("web_allowed_role_ids", [])
    allowed_users = config.get("web_allowed_user_ids", [])

    return templates.TemplateResponse(request, "partials/config_login.html", {
        "request": request,
        "user": current_user,
        "allowed_roles": allowed_roles,
        "allowed_users": allowed_users,
        "success": "",
        "error": "",
    })


@router.post("/config/login", response_class=HTMLResponse)
async def save_login_settings(request: Request, current_user: dict = Depends(require_auth)):
    """Speichert Login-Einstellungen (berechtigte Rollen- und Benutzer-IDs)."""
    if not _is_owner(current_user):
        raise HTTPException(403, "Nur der Owner darf die Login-Verwaltung anpassen")

    user = current_user
    try:
        form = await request.form()
        config = get_config()

        # Rollen-IDs einlesen (kommasepariert oder einzelne Felder)
        roles_raw = form.get("allowed_role_ids", "")
        allowed_roles = [
            rid.strip() for rid in roles_raw.split(",")
            if rid.strip()
        ]

        # Benutzer-IDs einlesen (kommasepariert oder einzelne Felder)
        users_raw = form.get("allowed_user_ids", "")
        allowed_users = [
            uid.strip() for uid in users_raw.split(",")
            if uid.strip()
        ]

        config["web_allowed_role_ids"] = allowed_roles
        config["web_allowed_user_ids"] = allowed_users
        save_config(config)
        logger.info(f"Login-Einstellungen gespeichert von {user.get('username', 'Unbekannt')}")

        return templates.TemplateResponse(request, "partials/config_login.html", {
            "request": request,
            "user": user,
            "allowed_roles": allowed_roles,
            "allowed_users": allowed_users,
            "success": "Login-Einstellungen erfolgreich gespeichert.",
            "error": "",
        })

    except Exception as e:
        logger.error(f"Fehler beim Speichern der Login-Einstellungen: {e}")
        config = get_config()
        return templates.TemplateResponse(request, "partials/config_login.html", {
            "request": request,
            "user": user,
            "allowed_roles": config.get("web_allowed_role_ids", []),
            "allowed_users": config.get("web_allowed_user_ids", []),
            "success": "",
            "error": "Fehler beim Speichern der Einstellungen. Details im Server-Log.",
        })


# ==============================================================
#  Phase 13g: Bot-Profile
# ==============================================================

@router.get("/config/bot-profiles", response_class=HTMLResponse)
async def bot_profiles(request: Request, current_user: dict = Depends(require_auth)):
    """Bot-Profile verwalten (Namen, Avatare, Status)."""
    if not _is_owner(current_user):
        raise HTTPException(403, "Nur der Owner darf Bot-Profile anpassen")

    config = get_config()
    profiles = config.get("bot_profiles", DEFAULT_BOT_PROFILES)

    return templates.TemplateResponse(request, "partials/config_bot_profiles.html", {
        "request": request,
        "user": current_user,
        "profiles": profiles,
        "success": "",
        "error": "",
    })


@router.post("/config/bot-profiles", response_class=HTMLResponse)
async def save_bot_profiles(request: Request, current_user: dict = Depends(require_auth)):
    """Speichert Bot-Profile (Anzeigename, Status, Avatar-URL)."""
    if not _is_owner(current_user):
        raise HTTPException(403, "Nur der Owner darf Bot-Profile anpassen")

    user = current_user
    try:
        form = await request.form()
        config = get_config()

        profiles = {}
        for bot_key in DEFAULT_BOT_PROFILES:
            display_name = form.get(f"profiles.{bot_key}.display_name", DEFAULT_BOT_PROFILES[bot_key]["display_name"])
            status = form.get(f"profiles.{bot_key}.status", DEFAULT_BOT_PROFILES[bot_key]["status"])
            avatar_url = form.get(f"profiles.{bot_key}.avatar_url", "")

            profiles[bot_key] = {
                "display_name": display_name.strip(),
                "status": status.strip(),
                "avatar_url": avatar_url.strip(),
            }

        config["bot_profiles"] = profiles
        save_config(config)
        logger.info(f"Bot-Profile gespeichert von {user.get('username', 'Unbekannt')}")

        return templates.TemplateResponse(request, "partials/config_bot_profiles.html", {
            "request": request,
            "user": user,
            "profiles": profiles,
            "success": "Bot-Profile erfolgreich gespeichert.",
            "error": "",
        })

    except Exception as e:
        logger.error(f"Fehler beim Speichern der Bot-Profile: {e}")
        config = get_config()
        profiles = config.get("bot_profiles", DEFAULT_BOT_PROFILES)
        return templates.TemplateResponse(request, "partials/config_bot_profiles.html", {
            "request": request,
            "user": user,
            "profiles": profiles,
            "success": "",
            "error": "Fehler beim Speichern der Einstellungen. Details im Server-Log.",
        })

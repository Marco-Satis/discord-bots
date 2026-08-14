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

from utils.config import get_config, save_config
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


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request, current_user: dict = Depends(require_auth)):
    """
    Zeigt die aktuelle Konfiguration in einem bearbeitbaren Formular.
    Nur für Owner zugänglich.
    """
    if not _is_owner(current_user):
        raise HTTPException(403, "Nur der Owner darf die Konfiguration aendern")

    config = get_config()

    return templates.TemplateResponse(request, "config.html", {
        "request": request,
        "user": current_user,
        "config": config,
        "success": "",
        "error": "",
    })


@router.post("/config", response_class=HTMLResponse)
async def config_save(request: Request, current_user: dict = Depends(require_auth)):
    """
    Speichert die geaenderte Konfiguration.
    Liest alle Formularfelder und aktualisiert config.json.
    """
    if not _is_owner(current_user):
        raise HTTPException(403, "Nur der Owner darf die Konfiguration aendern")

    user = current_user  # Alias fuer Backward-Compatibility im Body
    try:
        form = await request.form()
        config = get_config()

        # --- Features (Booleans) ---
        if "features" not in config:
            config["features"] = {}
        feature_keys = [
            "player_tracking", "auto_backup", "onedrive_backup",
            "email_notifications", "auto_update", "daily_restart",
            "voice_stats", "status_embed", "chat_bridge", "word_filter",
            "anti_spam", "login_audit", "auto_cleanup",
            "savegame_protection", "graceful_degradation", "steam_changelog",
        ]
        for key in feature_keys:
            field_name = f"features.{key}"
            # Checkbox: vorhanden = True, nicht vorhanden = False
            config["features"][key] = field_name in form

        # --- Thresholds (Zahlen) ---
        if "thresholds" not in config:
            config["thresholds"] = {}
        threshold_keys = {
            "cpu_warning": int,
            "ram_warning": int,
            "disk_warning": int,
            "disk_critical": int,
            "tick_rate_warning": int,
            "warning_cooldown": int,
        }
        for key, cast_type in threshold_keys.items():
            field_name = f"thresholds.{key}"
            if field_name in form:
                try:
                    config["thresholds"][key] = cast_type(form[field_name])
                except (ValueError, TypeError):
                    pass  # Ungueltige Werte ignorieren

        # --- Scheduler / Intervalle ---
        if "scheduler" not in config:
            config["scheduler"] = {}
        scheduler_int_keys = [
            "daily_restart_hour", "daily_restart_minute",
            "min_uptime_for_restart_hours",
            "auto_backup_interval_hours", "max_local_backups",
            "max_cloud_backups", "update_check_interval_hours",
            "auto_update_hour", "daily_report_hour",
            "weekly_report_day", "weekly_report_hour",
            "config_backup_hour", "auto_cleanup_hour",
            "backup_verify_day", "backup_verify_hour",
        ]
        for key in scheduler_int_keys:
            field_name = f"scheduler.{key}"
            if field_name in form:
                try:
                    config["scheduler"][key] = int(form[field_name])
                except (ValueError, TypeError):
                    pass

        scheduler_bool_keys = [
            "daily_restart_enabled", "auto_backup_enabled",
            "update_check_enabled", "auto_update_enabled",
            "auto_update_require_empty", "daily_report_enabled",
            "config_backup_enabled", "auto_cleanup_enabled",
            "backup_before_restart",
        ]
        for key in scheduler_bool_keys:
            field_name = f"scheduler.{key}"
            config["scheduler"][key] = field_name in form

        # --- Restart Timer ---
        if "restart_timer" not in config:
            config["restart_timer"] = {}
        restart_keys = ["default_duration", "stop_duration"]
        for key in restart_keys:
            field_name = f"restart_timer.{key}"
            if field_name in form:
                try:
                    config["restart_timer"][key] = int(form[field_name])
                except (ValueError, TypeError):
                    pass

        # --- Auto Restart & Delay ---
        config["auto_restart"] = "auto_restart" in form
        if "restart_delay" in form:
            try:
                config["restart_delay"] = int(form["restart_delay"])
            except (ValueError, TypeError):
                pass

        # Konfiguration speichern
        save_config(config)
        logger.info(f"Konfiguration gespeichert von {user.get('username', 'Unbekannt')}")

        return templates.TemplateResponse(request, "config.html", {
            "request": request,
            "user": user,
            "config": config,
            "success": "Konfiguration erfolgreich gespeichert.",
            "error": "",
        })

    except Exception as e:
        logger.error(f"Fehler beim Speichern der Konfiguration: {e}")
        config = get_config()
        return templates.TemplateResponse(request, "config.html", {
            "request": request,
            "user": user,
            "config": config,
            "success": "",
            "error": "Fehler beim Speichern der Einstellungen. Details im Server-Log.",
        })


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

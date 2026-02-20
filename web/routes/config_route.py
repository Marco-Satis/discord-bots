"""
Phase 13a: Konfigurations-Panel — Bearbeitung der config.json

Ermoeglicht das Anzeigen und Aendern der Bot-Konfiguration
ueber das Web-Dashboard. Nur fuer Owner zugaenglich.
"""

import json
from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.config import get_config, save_config
from utils.logger import get_logger
from web.auth import get_current_user

logger = get_logger("web.routes.config")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Konfiguration"])


def _is_owner(user: dict) -> bool:
    """Prueft ob der Benutzer Owner-Rechte hat."""
    return user.get("is_owner") in (True, "True", "true")


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    """
    Zeigt die aktuelle Konfiguration in einem bearbeitbaren Formular.
    Nur fuer Owner zugaenglich.
    """
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    if not _is_owner(user):
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "user": user,
            "servers": [],
            "system": {},
            "bots": [],
            "events": [],
            "error": "Nur der Owner darf die Konfiguration aendern.",
        })

    config = get_config()

    return templates.TemplateResponse("config.html", {
        "request": request,
        "user": user,
        "config": config,
        "success": "",
        "error": "",
    })


@router.post("/config", response_class=HTMLResponse)
async def config_save(request: Request):
    """
    Speichert die geaenderte Konfiguration.
    Liest alle Formularfelder und aktualisiert config.json.
    """
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    if not _is_owner(user):
        return RedirectResponse(url="/", status_code=302)

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

        return templates.TemplateResponse("config.html", {
            "request": request,
            "user": user,
            "config": config,
            "success": "Konfiguration erfolgreich gespeichert.",
            "error": "",
        })

    except Exception as e:
        logger.error(f"Fehler beim Speichern der Konfiguration: {e}")
        config = get_config()
        return templates.TemplateResponse("config.html", {
            "request": request,
            "user": user,
            "config": config,
            "success": "",
            "error": f"Fehler beim Speichern: {e}",
        })

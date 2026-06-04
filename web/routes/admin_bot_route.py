"""
Phase 13f: Admin Bot Setup — Konfigurationsseite fuer alle Admin-Bot-Module.

10 Tabs fuer die Verwaltung aller Admin-Bot-Features:
Temp Voice, TeamSpeak, WordFilter, AntiSpam, Warn-System,
Reaction Roles, Leveling, Tickets, Audit-Logging, Giveaways.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from utils.config import PROJECT_ROOT, DATA_DIR, get_config, save_config
from utils.logger import get_logger
from web.auth import require_auth
from web.guild_config_bridge import read_leveling_config, write_leveling_config
from web.temp_voice_bridge import (
    hubs_to_text,
    parse_hubs_text,
    read_afk_timeout,
    read_temp_voice_hubs,
    write_temp_voice_config,
)

logger = get_logger("web.routes.admin_bot")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Admin Bot Setup"], dependencies=[Depends(require_auth)])

# Zuordnung Tab-Name → Datenverzeichnis und Dateiname
MODULE_FILE_MAP = {
    "temp_voice":     ("temp_voice",     "config.json"),
    "teamspeak":      ("teamspeak",      "config.json"),
    "wordfilter":     ("moderation",     "wordfilter.json"),
    "antispam":       ("moderation",     "antispam.json"),
    "warn":           ("moderation",     "warn_config.json"),
    "reaction_roles": ("reaction_roles", "config.json"),
    "leveling":       ("leveling",       "config.json"),
    "tickets":        ("tickets",        "config.json"),
    "audit":          ("audit",          "config.json"),
    "giveaways":      ("giveaway",       "config.json"),
    "embeds":         ("admin",          "embeds_config.json"),
}

# Standard-Konfigurationen fuer jedes Modul (Fallback wenn Datei fehlt)
DEFAULT_CONFIGS = {
    "temp_voice": {
        # Multi-Hub (C1): Hubs als editierbarer Text (eine Zeile pro Hub).
        # Wird ueber web.temp_voice_bridge in die echte Bot-Config geschrieben.
        "hubs_text": "",
        "afk_timeout_minutes": 5,
    },
    "teamspeak": {
        "ts_enabled": False,
        "host": "",
        "port": 10011,
        "username": "",
        "password": "",  # nosec B105 - leeres Default-Schema, echter Wert kommt aus Config
        "virtual_server_id": 1,
        "chat_bridge": {
            "discord_channel_id": "",
            "ts_channel_id": "",
            "enabled": False,
        },
        "auto_channels": [],
    },
    "wordfilter": {
        "exact_words": [],
        "partial_words": [],
        "regex_patterns": [],
        "action": "delete",
        "exempt_roles": [],
    },
    "antispam": {
        "max_messages": 5,
        "interval_seconds": 5,
        "duplicate_detection": False,
        "duplicate_threshold": 3,
        "mention_limit": 5,
        "emoji_limit": 10,
        "action": "mute",
        "mute_duration_minutes": 10,
        "exempt_roles": [],
    },
    "warn": {
        "levels": [
            {"points": 1, "action": "warn", "duration": 0},
            {"points": 3, "action": "mute", "duration": 60},
            {"points": 5, "action": "kick", "duration": 0},
            {"points": 7, "action": "ban", "duration": 0},
        ],
        "decay_days": 30,
        "log_channel_id": "",
    },
    "reaction_roles": {
        "allow_multiple": True,
        "dm_notification": True,
        "panels": [],
    },
    "leveling": {
        "xp_per_message_min": 15,
        "xp_per_message_max": 25,
        "xp_cooldown_seconds": 60,
        "voice_xp_per_minute": 5,
        "channel_multipliers": [],
        "role_rewards": [],
        "no_xp_channels": [],
        "remove_lower_rewards": False,
        "leaderboard_display_count": 10,
    },
    "tickets": {
        "button_channel_id": "",
        "support_role_ids": "",
        "ticket_category_id": "",
        "auto_close_hours": 0,
        "transcript_enabled": False,
        "transcript_channel_id": "",
    },
    "audit": {
        "moderation_log_channel": "",
        "join_leave_log_channel": "",
        "member_update_log_channel": "",
        "role_change_log_channel": "",
        "server_change_log_channel": "",
        "message_log_channel": "",
        "moderation_enabled": True,
        "join_leave_enabled": True,
        "member_update_enabled": True,
        "role_change_enabled": True,
        "server_change_enabled": True,
        "message_enabled": True,
    },
    "giveaways": {
        "default_duration_hours": 24,
        "default_winner_count": 1,
        "required_role_id": "",
        "allow_multiple_entries": False,
        "dm_winners": True,
        "giveaway_channel_id": "",
    },
    "embeds": {
        "last_channel_id": "",
        "saved_templates": [],
    },
}


def _load_module_config(module_name: str) -> dict:
    """Laedt die Konfiguration eines Moduls aus data/{module}/config.json."""
    if module_name not in MODULE_FILE_MAP:
        logger.warning(f"Unbekanntes Modul: {module_name}")
        return DEFAULT_CONFIGS.get(module_name, {})

    folder, filename = MODULE_FILE_MAP[module_name]
    config_path = DATA_DIR / folder / filename

    try:
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Standard-Werte ergaenzen fuer fehlende Schluessel
                defaults = DEFAULT_CONFIGS.get(module_name, {})
                for key, value in defaults.items():
                    if key not in data:
                        data[key] = value
                return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Konnte Modul-Config '{module_name}' nicht laden: {e}")

    return dict(DEFAULT_CONFIGS.get(module_name, {}))


def _save_module_config(module_name: str, config: dict) -> bool:
    """Speichert die Konfiguration eines Moduls."""
    if module_name not in MODULE_FILE_MAP:
        logger.error(f"Speichern fehlgeschlagen — unbekanntes Modul: {module_name}")
        return False

    folder, filename = MODULE_FILE_MAP[module_name]
    config_path = DATA_DIR / folder / filename
    config_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logger.info(f"Modul-Config '{module_name}' gespeichert: {config_path}")
        return True
    except IOError as e:
        logger.error(f"Fehler beim Speichern von '{module_name}': {e}")
        return False


def _parse_form_temp_voice(form) -> dict:
    """
    Parst Formular-Daten fuer das Temp-Voice-Modul (Multi-Hub, C1).

    Das Textarea `hubs_text` (eine Zeile pro Hub) wird zu einer Hub-Liste
    geparst; `hubs_text` bleibt fuer die Wiederanzeige im Formular erhalten.
    Die Hub-Liste landet ueber die Bruecke in der echten Bot-Config.
    """
    hubs_text = form.get("hubs_text", "")
    hubs = parse_hubs_text(hubs_text)
    return {
        # Normalisierte Anzeige aus den geparsten Hubs (raeumt Tippfehler auf)
        "hubs_text": hubs_to_text(hubs),
        "hubs": hubs,
        "afk_timeout_minutes": int(form.get("afk_timeout_minutes", 5) or 5),
    }


def _parse_form_teamspeak(form) -> dict:
    """Parst Formular-Daten fuer das TeamSpeak-Modul."""
    # Auto-Channels aus mehrzeiligem Textfeld parsen
    auto_channels_raw = form.get("auto_channels", "").strip()
    auto_channels = [ch.strip() for ch in auto_channels_raw.split("\n") if ch.strip()]

    return {
        "ts_enabled": "ts_enabled" in form,
        "host": form.get("host", "").strip(),
        "port": int(form.get("port", 10011) or 10011),
        "username": form.get("username", "").strip(),
        "password": form.get("password", "").strip(),
        "virtual_server_id": int(form.get("virtual_server_id", 1) or 1),
        "chat_bridge": {
            "discord_channel_id": form.get("bridge_discord_channel_id", "").strip(),
            "ts_channel_id": form.get("bridge_ts_channel_id", "").strip(),
            "enabled": "bridge_enabled" in form,
        },
        "auto_channels": auto_channels,
    }


def _parse_form_wordfilter(form) -> dict:
    """Parst Formular-Daten fuer den Wortfilter."""
    def _lines_to_list(text: str) -> list:
        return [w.strip() for w in text.split("\n") if w.strip()]

    exempt_raw = form.get("exempt_roles", "").strip()
    exempt_roles = [r.strip() for r in exempt_raw.split(",") if r.strip()]

    return {
        "exact_words": _lines_to_list(form.get("exact_words", "")),
        "partial_words": _lines_to_list(form.get("partial_words", "")),
        "regex_patterns": _lines_to_list(form.get("regex_patterns", "")),
        "action": form.get("action", "delete"),
        "exempt_roles": exempt_roles,
    }


def _parse_form_antispam(form) -> dict:
    """Parst Formular-Daten fuer das AntiSpam-Modul."""
    exempt_raw = form.get("exempt_roles", "").strip()
    exempt_roles = [r.strip() for r in exempt_raw.split(",") if r.strip()]

    return {
        "max_messages": int(form.get("max_messages", 5) or 5),
        "interval_seconds": int(form.get("interval_seconds", 5) or 5),
        "duplicate_detection": "duplicate_detection" in form,
        "duplicate_threshold": int(form.get("duplicate_threshold", 3) or 3),
        "mention_limit": int(form.get("mention_limit", 5) or 5),
        "emoji_limit": int(form.get("emoji_limit", 10) or 10),
        "action": form.get("action", "mute"),
        "mute_duration_minutes": int(form.get("mute_duration_minutes", 10) or 10),
        "exempt_roles": exempt_roles,
    }


def _parse_form_warn(form) -> dict:
    """Parst Formular-Daten fuer das Warn-System."""
    # Dynamische Warn-Level aus Formular lesen
    levels = []
    idx = 0
    while True:
        points_key = f"level_points_{idx}"
        action_key = f"level_action_{idx}"
        duration_key = f"level_duration_{idx}"
        if points_key not in form:
            break
        try:
            levels.append({
                "points": int(form.get(points_key, 1) or 1),
                "action": form.get(action_key, "warn"),
                "duration": int(form.get(duration_key, 0) or 0),
            })
        except (ValueError, TypeError):
            pass
        idx += 1

    # Fallback: Mindestens Standard-Level beibehalten
    if not levels:
        levels = DEFAULT_CONFIGS["warn"]["levels"]

    return {
        "levels": levels,
        "decay_days": int(form.get("decay_days", 30) or 30),
        "log_channel_id": form.get("log_channel_id", "").strip(),
    }


def _parse_form_reaction_roles(form) -> dict:
    """Parst Formular-Daten fuer Reaction Roles."""
    # Panels bleiben unveraendert (werden ueber Discord verwaltet)
    existing = _load_module_config("reaction_roles")
    return {
        "allow_multiple": "allow_multiple" in form,
        "dm_notification": "dm_notification" in form,
        "panels": existing.get("panels", []),
    }


def _parse_form_leveling(form) -> dict:
    """Parst Formular-Daten fuer das Leveling-System."""
    # Channel-Multipliers aus dynamischen Feldern lesen
    channel_multipliers = []
    idx = 0
    while True:
        ch_key = f"cm_channel_{idx}"
        mult_key = f"cm_multiplier_{idx}"
        if ch_key not in form:
            break
        ch_val = form.get(ch_key, "").strip()
        mult_val = form.get(mult_key, "1.0").strip()
        if ch_val:
            try:
                channel_multipliers.append({
                    "channel_id": ch_val,
                    "multiplier": float(mult_val or 1.0),
                })
            except (ValueError, TypeError):
                pass
        idx += 1

    # Role-Rewards aus dynamischen Feldern lesen
    role_rewards = []
    idx = 0
    while True:
        lvl_key = f"rr_level_{idx}"
        role_key = f"rr_role_{idx}"
        if lvl_key not in form:
            break
        lvl_val = form.get(lvl_key, "").strip()
        role_val = form.get(role_key, "").strip()
        if lvl_val and role_val:
            try:
                role_rewards.append({
                    "level": int(lvl_val),
                    "role_id": role_val,
                })
            except (ValueError, TypeError):
                pass
        idx += 1

    # No-XP-Channels: Textarea (eine ID pro Zeile, Komma toleriert)
    no_xp_raw = form.get("no_xp_channels", "") or ""
    no_xp_channels = [
        line.strip()
        for line in no_xp_raw.replace(",", "\n").splitlines()
        if line.strip()
    ]

    return {
        "xp_per_message_min": int(form.get("xp_per_message_min", 15) or 15),
        "xp_per_message_max": int(form.get("xp_per_message_max", 25) or 25),
        "xp_cooldown_seconds": int(form.get("xp_cooldown_seconds", 60) or 60),
        "voice_xp_per_minute": int(form.get("voice_xp_per_minute", 5) or 5),
        "channel_multipliers": channel_multipliers,
        "role_rewards": role_rewards,
        "no_xp_channels": no_xp_channels,
        "remove_lower_rewards": form.get("remove_lower_rewards") == "on",
        "leaderboard_display_count": int(form.get("leaderboard_display_count", 10) or 10),
    }


def _parse_form_tickets(form) -> dict:
    """Parst Formular-Daten fuer das Ticket-System."""
    return {
        "button_channel_id": form.get("button_channel_id", "").strip(),
        "support_role_ids": form.get("support_role_ids", "").strip(),
        "ticket_category_id": form.get("ticket_category_id", "").strip(),
        "auto_close_hours": int(form.get("auto_close_hours", 0) or 0),
        "transcript_enabled": "transcript_enabled" in form,
        "transcript_channel_id": form.get("transcript_channel_id", "").strip(),
    }


def _parse_form_audit(form) -> dict:
    """Parst Formular-Daten fuer das Audit-Logging."""
    return {
        "moderation_log_channel": form.get("moderation_log_channel", "").strip(),
        "join_leave_log_channel": form.get("join_leave_log_channel", "").strip(),
        "member_update_log_channel": form.get("member_update_log_channel", "").strip(),
        "role_change_log_channel": form.get("role_change_log_channel", "").strip(),
        "server_change_log_channel": form.get("server_change_log_channel", "").strip(),
        "message_log_channel": form.get("message_log_channel", "").strip(),
        "moderation_enabled": "moderation_enabled" in form,
        "join_leave_enabled": "join_leave_enabled" in form,
        "member_update_enabled": "member_update_enabled" in form,
        "role_change_enabled": "role_change_enabled" in form,
        "server_change_enabled": "server_change_enabled" in form,
        "message_enabled": "message_enabled" in form,
    }


def _parse_form_giveaways(form) -> dict:
    """Parst Formular-Daten fuer das Giveaway-Modul."""
    return {
        "default_duration_hours": int(form.get("default_duration_hours", 24) or 24),
        "default_winner_count": int(form.get("default_winner_count", 1) or 1),
        "required_role_id": form.get("required_role_id", "").strip(),
        "allow_multiple_entries": "allow_multiple_entries" in form,
        "dm_winners": "dm_winners" in form,
        "giveaway_channel_id": form.get("giveaway_channel_id", "").strip(),
    }


def _parse_form_embeds(form) -> dict:
    """
    Parst Formular-Daten fuer den Embed-Builder.
    Speichert die Embed-Daten als Queue-Eintrag,
    den der Admin Bot abholt und sendet.
    """
    # Farbe bestimmen
    color_val = form.get("embed_color", "#5865F2")
    if color_val == "custom":
        color_val = form.get("custom_color", "#5865F2").strip()
    if not color_val.startswith("#"):
        color_val = "#5865F2"

    # Felder sammeln
    fields = []
    for i in range(3):
        fname = form.get(f"field_name_{i}", "").strip()
        fvalue = form.get(f"field_value_{i}", "").strip()
        if fname and fvalue:
            fields.append({
                "name": fname,
                "value": fvalue,
                "inline": f"field_inline_{i}" in form,
            })

    embed_data = {
        "channel_id": form.get("channel_id", "").strip(),
        "title": form.get("embed_title", "").strip(),
        "description": form.get("embed_description", "").strip(),
        "color": color_val,
        "thumbnail_url": form.get("embed_thumbnail", "").strip(),
        "image_url": form.get("embed_image", "").strip(),
        "footer_text": form.get("embed_footer", "").strip(),
        "author_name": form.get("embed_author", "").strip(),
        "fields": fields,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }

    # Queue-Datei fuer den Admin Bot schreiben
    queue_dir = DATA_DIR / "admin" / "embed_queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    queue_file = queue_dir / f"embed_{int(datetime.now().timestamp())}.json"
    try:
        with open(queue_file, "w", encoding="utf-8") as f:
            json.dump(embed_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Embed in Queue geschrieben: {queue_file.name}")
    except IOError as e:
        logger.error(f"Fehler beim Schreiben der Embed-Queue: {e}")

    # Config mit letzter Channel-ID aktualisieren
    return {
        "last_channel_id": embed_data["channel_id"],
        "saved_templates": _load_module_config("embeds").get("saved_templates", []),
    }


# Zuordnung Modul-Name → Parser-Funktion
FORM_PARSERS = {
    "temp_voice":     _parse_form_temp_voice,
    "teamspeak":      _parse_form_teamspeak,
    "wordfilter":     _parse_form_wordfilter,
    "antispam":       _parse_form_antispam,
    "warn":           _parse_form_warn,
    "reaction_roles": _parse_form_reaction_roles,
    "leveling":       _parse_form_leveling,
    "tickets":        _parse_form_tickets,
    "audit":          _parse_form_audit,
    "giveaways":      _parse_form_giveaways,
    "embeds":         _parse_form_embeds,
}

# Zuordnung Tab-Name → Template-Partial-Dateiname
TAB_TEMPLATES = {
    "temp_voice":     "partials/admin_tab_temp_voice.html",
    "teamspeak":      "partials/admin_tab_teamspeak.html",
    "wordfilter":     "partials/admin_tab_wordfilter.html",
    "antispam":       "partials/admin_tab_antispam.html",
    "warn":           "partials/admin_tab_warn.html",
    "reaction_roles": "partials/admin_tab_reaction_roles.html",
    "leveling":       "partials/admin_tab_leveling.html",
    "tickets":        "partials/admin_tab_tickets.html",
    "audit":          "partials/admin_tab_audit.html",
    "giveaways":      "partials/admin_tab_giveaways.html",
    "embeds":         "partials/admin_tab_embeds.html",
}


async def _load_tab_config(tab_name: str) -> dict:
    """
    Tab-Config laden. Fuer Leveling die per-Guild `guild_config` ueber die
    JSON-Defaults legen (MVP-Config), sonst nur JSON.
    """
    config = await asyncio.to_thread(_load_module_config, tab_name)
    if tab_name == "leveling":
        try:
            per_guild = await read_leveling_config()
            if per_guild:
                config.update(per_guild)
        except Exception as e:  # noqa: BLE001 — JSON-Default als Fallback
            logger.warning(f"Leveling guild_config-Read fehlgeschlagen: {e}")
    elif tab_name == "temp_voice":
        # Multi-Hub (C1): Hubs aus der echten Bot-Config laden, damit das
        # Dashboard anzeigt was der Bot tatsaechlich nutzt (nicht die JSON-Leiche).
        try:
            hubs = await asyncio.to_thread(read_temp_voice_hubs)
            config["hubs_text"] = hubs_to_text(hubs)
            config["afk_timeout_minutes"] = await asyncio.to_thread(
                read_afk_timeout, config.get("afk_timeout_minutes", 5)
            )
        except Exception as e:  # noqa: BLE001 — JSON-Default als Fallback
            logger.warning(f"Temp-Voice Hub-Read fehlgeschlagen: {e}")
    return config


# --- Routen ---

@router.get("/admin-bot", response_class=HTMLResponse)
async def admin_bot_page(request: Request, current_user: dict = Depends(require_auth)):
    """
    Hauptseite des Admin Bot Setup mit 10 Tabs.
    Laedt standardmaessig den ersten Tab (Temp Voice).
    """
    user = current_user
    # Standard-Tab + Admin-Bot-Status parallel via to_thread (kein Event-Loop-Block)
    admin_status_file = DATA_DIR / "admin" / "bot_status.json"

    def _read_admin_status() -> str:
        try:
            if admin_status_file.exists():
                with open(admin_status_file, "r", encoding="utf-8") as f:
                    return json.load(f).get("status", "offline")
        except (json.JSONDecodeError, IOError):
            pass
        return "offline"

    config, admin_bot_status = await asyncio.gather(
        _load_tab_config("temp_voice"),
        asyncio.to_thread(_read_admin_status),
    )

    return templates.TemplateResponse("admin_bot.html", {
        "request": request,
        "user": user,
        "active_tab": "temp_voice",
        "config": config,
        "admin_bot_status": admin_bot_status,
        "guild_name": "",
        "success": "",
        "error": "",
    })


@router.get("/admin-bot/tab/{tab_name}", response_class=HTMLResponse)
async def admin_bot_tab(request: Request, tab_name: str, current_user: dict = Depends(require_auth)):
    """
    HTMX-Partial: Laedt den Inhalt eines einzelnen Tabs.
    Wird per HTMX-Request in den Tab-Content-Bereich geladen.
    """
    if tab_name not in TAB_TEMPLATES:
        return HTMLResponse(
            content='<div class="alert alert-danger">Unbekannter Tab.</div>',
            status_code=404,
        )

    config = await _load_tab_config(tab_name)
    template_name = TAB_TEMPLATES[tab_name]

    return templates.TemplateResponse(template_name, {
        "request": request,
        "config": config,
        "success": "",
        "error": "",
    })


@router.post("/admin-bot/save/{module_name}", response_class=HTMLResponse)
async def admin_bot_save(request: Request, module_name: str, current_user: dict = Depends(require_auth)):
    """
    Speichert die Einstellungen eines Moduls.
    Liest Formular-Daten, parst sie modulspezifisch und speichert als JSON.
    """
    user = current_user
    if module_name not in FORM_PARSERS or module_name not in TAB_TEMPLATES:
        return HTMLResponse(
            content='<div class="alert alert-danger">Unbekanntes Modul.</div>',
            status_code=404,
        )

    template_name = TAB_TEMPLATES[module_name]
    success_msg = ""
    error_msg = ""

    try:
        form = await request.form()
        parser = FORM_PARSERS[module_name]
        config = parser(form)

        if await asyncio.to_thread(_save_module_config, module_name, config):
            success_msg = "Einstellungen erfolgreich gespeichert."
            logger.info(
                f"Admin-Bot Modul '{module_name}' gespeichert von "
                f"{user.get('username', 'Unbekannt')}"
            )
            # MVP-Config: Leveling zusaetzlich per-Guild in guild_config schreiben,
            # damit der Bot (liest seit Phase C per-Guild) die Aenderung uebernimmt.
            if module_name == "leveling":
                try:
                    await write_leveling_config(
                        config, updated_by=user.get("username", "dashboard")
                    )
                except Exception as e:  # noqa: BLE001 — JSON-Save war schon erfolgreich
                    logger.error(f"Leveling guild_config-Bridge fehlgeschlagen: {e}")
            # Multi-Hub (C1): Hubs in die echte Bot-Config schreiben (sonst sieht
            # der Bot die Dashboard-Aenderung nie — getrennte JSON-Dateien).
            elif module_name == "temp_voice":
                try:
                    await asyncio.to_thread(
                        write_temp_voice_config,
                        config.get("hubs", []),
                        config.get("afk_timeout_minutes"),
                    )
                    # Anzeige aus der echten Bot-Config nachladen (Live-Wahrheit)
                    config["hubs_text"] = hubs_to_text(
                        await asyncio.to_thread(read_temp_voice_hubs)
                    )
                except Exception as e:  # noqa: BLE001 — JSON-Save war schon erfolgreich
                    logger.error(f"Temp-Voice Hub-Bridge fehlgeschlagen: {e}")
        else:
            error_msg = "Fehler beim Speichern der Einstellungen."

    except Exception as e:
        logger.error(f"Fehler beim Speichern von '{module_name}': {e}")
        error_msg = "Fehler beim Speichern der Einstellungen. Details im Server-Log."
        config = await asyncio.to_thread(_load_module_config, module_name)

    return templates.TemplateResponse(template_name, {
        "request": request,
        "config": config,
        "success": success_msg,
        "error": error_msg,
    })

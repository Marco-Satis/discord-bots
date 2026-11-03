"""
LFG-Seite — „Looking for Group"-Ping-Rolle im Dashboard konfigurieren.

Setzt die per-Guild-Config (`guild_config`-Keys `lfg.*`) die der LFG-Cog liest
(`/lfg ping`, Toggle-Button). Slash-Befehle (`/lfg setup`) bleiben gleichwertiger
Fallback (schreiben dieselben Keys). HTMX + globale CSRFMiddleware + require_auth.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from web.templates_setup import erstelle_templates

from modules.guild_context import GuildConfig, get_primary_guild_id
from utils.logger import get_logger
from web.auth import require_auth, require_perm

logger = get_logger("web.routes.lfg")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)

router = APIRouter(tags=["LFG"])

_DEFAULT_COOLDOWN = 300
_DEFAULT_TALK_PAUSE = 60


async def _load_cfg(guild_id: int) -> dict:
    """Aktuelle LFG-Config der Guild laden (für Anzeige)."""
    try:
        role_id = await GuildConfig.get(guild_id, "lfg.role_id", "") or ""
        channel_id = await GuildConfig.get(guild_id, "lfg.channel_id", "") or ""
        cooldown = await GuildConfig.get(
            guild_id, "lfg.cooldown_seconds", _DEFAULT_COOLDOWN
        )
    except Exception as e:  # noqa: BLE001 — nie fatal
        logger.error(f"LFG-Config laden fehlgeschlagen: {e}")
        role_id, channel_id, cooldown = "", "", _DEFAULT_COOLDOWN
    try:
        cooldown = int(cooldown)
    except (TypeError, ValueError):
        cooldown = _DEFAULT_COOLDOWN
    talk = {}
    try:
        talk = {
            "talk_aktiv": bool(await GuildConfig.get(guild_id, "lfg.talk_aktiv", False)),
            "talk_role_id": str(await GuildConfig.get(guild_id, "lfg.talk_role_id", "") or ""),
            "talk_channel_id": str(await GuildConfig.get(guild_id, "lfg.talk_channel_id", "") or ""),
            "talk_pause_minuten": int(
                await GuildConfig.get(guild_id, "lfg.talk_pause_minuten", _DEFAULT_TALK_PAUSE)
            ),
        }
    except Exception as e:  # noqa: BLE001 — nie fatal
        logger.error(f"Talk-Config laden fehlgeschlagen: {e}")
    return {
        "role_id": str(role_id),
        "channel_id": str(channel_id),
        "cooldown_seconds": cooldown,
        "talk_aktiv": False, "talk_role_id": "", "talk_channel_id": "",
        "talk_pause_minuten": _DEFAULT_TALK_PAUSE,
        **talk,
    }


@router.get("/lfg", response_class=HTMLResponse)
async def lfg_page(request: Request, current_user: dict = Depends(require_auth)):
    """LFG-Konfigurationsseite der Haupt-Guild anzeigen."""
    guild_id = get_primary_guild_id()
    cfg = await _load_cfg(guild_id) if guild_id is not None else {}
    return templates.TemplateResponse(request, "lfg.html", {
        "request": request,
        "user": current_user,
        "cfg": cfg,
        "guild_set": guild_id is not None,
        "success": "",
        "error": "",
    })


@router.post("/lfg/config", response_class=HTMLResponse)
async def lfg_config_save(
    request: Request, current_user: dict = Depends(require_perm("lfg", "edit"))
):
    """LFG-Config speichern (role_id/channel_id/cooldown nach guild_config)."""
    guild_id = get_primary_guild_id()
    success = ""
    error = ""
    cfg: dict = {}

    if guild_id is None:
        error = "Keine Haupt-Guild gesetzt (ENV GUILD_ID)."
    else:
        try:
            form = await request.form()
            role_id = str(form.get("role_id", "") or "").strip()
            channel_id = str(form.get("channel_id", "") or "").strip()
            cooldown_raw = str(form.get("cooldown_seconds", "") or "").strip()

            # Snowflake-Validierung (leer erlaubt = nicht gesetzt)
            if role_id and not role_id.isdigit():
                raise ValueError("Rollen-ID muss numerisch sein")
            if channel_id and not channel_id.isdigit():
                raise ValueError("Kanal-ID muss numerisch sein")
            cooldown = int(cooldown_raw) if cooldown_raw.isdigit() else _DEFAULT_COOLDOWN
            cooldown = max(0, min(3600, cooldown))

            # „Talk ist offen"
            talk_aktiv = "talk_aktiv" in form
            talk_role = str(form.get("talk_role_id", "") or "").strip()
            talk_channel = str(form.get("talk_channel_id", "") or "").strip()
            pause_raw = str(form.get("talk_pause_minuten", "") or "").strip()
            if (talk_role and not talk_role.isdigit()) or (talk_channel and not talk_channel.isdigit()):
                raise ValueError("Rolle oder Kanal für „Talk ist offen“ ungültig")
            if talk_aktiv and not (talk_role and talk_channel):
                raise ValueError("Für „Talk ist offen“ bitte Rolle und Kanal wählen")
            pause = int(pause_raw) if pause_raw.isdigit() else _DEFAULT_TALK_PAUSE
            pause = max(0, min(1440, pause))

            await GuildConfig.set(guild_id, "lfg.role_id", role_id, updated_by="dashboard")
            await GuildConfig.set(
                guild_id, "lfg.channel_id", channel_id, updated_by="dashboard"
            )
            await GuildConfig.set(
                guild_id, "lfg.cooldown_seconds", cooldown, updated_by="dashboard"
            )
            for key, wert in (
                ("talk_aktiv", talk_aktiv), ("talk_role_id", talk_role),
                ("talk_channel_id", talk_channel), ("talk_pause_minuten", pause),
            ):
                await GuildConfig.set(guild_id, f"lfg.{key}", wert, updated_by="dashboard")
            logger.info(
                f"LFG-Config gespeichert von {current_user.get('username', 'Unbekannt')}"
            )
            success = "LFG-Einstellungen gespeichert."
        except ValueError as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001
            logger.error(f"LFG-Config speichern fehlgeschlagen: {e}")
            error = "Fehler beim Speichern. Details im Server-Log."
        cfg = await _load_cfg(guild_id)

    return templates.TemplateResponse(request, "partials/lfg_config.html", {
        "request": request,
        "cfg": cfg,
        "guild_set": guild_id is not None,
        "success": success,
        "error": error,
    })

"""
Moderation-Seite (D4) — Auto-Mod-Toggles im Dashboard.

Schaltet die Auto-Mod-Regeln der Haupt-Guild per-Guild ein/aus
(`guild_config`-Keys `moderation.<rule>`). Der Bot liest dieselben Keys über
einen 15s-TTL-Cache (`ModerationCog._rule_on`) — eine Dashboard-Änderung greift
spätestens nach TTL ohne Bot-Neustart. Slash-Befehle (`/filter ...`) bleiben
gleichwertiger Fallback (schreiben dieselben Keys).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from web.templates_setup import erstelle_templates

from modules.guild_context import GuildConfig, get_primary_guild_id
from utils.logger import get_logger
from web.auth import require_auth, require_perm

logger = get_logger("web.routes.moderation")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)

router = APIRouter(tags=["Moderation"])

# Regel-Key -> (Label, Default). Default = bisheriges Bot-Verhalten.
RULES: list[tuple[str, str, bool]] = [
    ("word_filter", "Wortfilter", True),
    ("anti_spam", "Anti-Spam (Flood / Mention / Duplikat)", True),
    ("invite_filter", "Invite-Link-Filter (fremde Discord-Einladungen)", False),
    ("caps_filter", "Mass-Caps-Filter (durchgehend GROSS)", False),
    ("zalgo_filter", "Zalgo-Filter (zerfranster Unicode-Text)", False),
]


async def _load_rules(guild_id: int) -> list[dict]:
    """Aktuelle Toggle-Zustände aller Auto-Mod-Regeln der Guild laden."""
    rules: list[dict] = []
    for key, label, default in RULES:
        try:
            enabled = await GuildConfig.get(guild_id, f"moderation.{key}", default)
        except Exception as e:  # noqa: BLE001 — nie fatal, Default greift
            logger.error(f"moderation.{key} laden fehlgeschlagen: {e}")
            enabled = default
        rules.append({"key": key, "label": label, "enabled": bool(enabled)})
    return rules


@router.get("/moderation", response_class=HTMLResponse)
async def moderation_page(request: Request, current_user: dict = Depends(require_auth)):
    """Auto-Mod-Toggle-Seite der Haupt-Guild anzeigen."""
    guild_id = get_primary_guild_id()
    rules: list[dict] = []
    if guild_id is not None:
        rules = await _load_rules(guild_id)

    return templates.TemplateResponse(request, "moderation.html", {
        "request": request,
        "user": current_user,
        "rules": rules,
        "guild_set": guild_id is not None,
        "success": "",
        "error": "",
    })


@router.post("/moderation/config", response_class=HTMLResponse)
async def moderation_config_save(
    request: Request, current_user: dict = Depends(require_perm("moderation", "edit"))
):
    """
    Auto-Mod-Toggles speichern (Checkbox vorhanden = an).

    Schreibt je Regel `moderation.<key>` nach `guild_config`. Der Bot liest sie
    über den 15s-TTL-Cache (kein Neustart nötig).
    """
    guild_id = get_primary_guild_id()
    success = ""
    error = ""

    if guild_id is None:
        error = "Keine Haupt-Guild gesetzt (ENV GUILD_ID)."
        rules: list[dict] = []
    else:
        try:
            form = await request.form()
            for key, _label, _default in RULES:
                enabled = key in form
                await GuildConfig.set(
                    guild_id, f"moderation.{key}", enabled, updated_by="dashboard"
                )
            logger.info(
                f"Auto-Mod-Toggles gespeichert von "
                f"{current_user.get('username', 'Unbekannt')}"
            )
            success = "Auto-Mod-Einstellungen gespeichert."
        except Exception as e:  # noqa: BLE001
            logger.error(f"Auto-Mod-Toggles speichern fehlgeschlagen: {e}")
            error = "Fehler beim Speichern. Details im Server-Log."
        rules = await _load_rules(guild_id)

    return templates.TemplateResponse(request, "partials/moderation_config.html", {
        "request": request,
        "rules": rules,
        "guild_set": guild_id is not None,
        "success": success,
        "error": error,
    })

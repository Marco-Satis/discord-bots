"""
Level-Seite (D5) — Web-Leaderboard.

Zeigt die Top-Spieler der Haupt-Guild (XP/Level/Nachrichten/Voice) aus der
guild-scoped `leveling`-Tabelle (Migration v6). Read-only, Dashboard-Auth.

Hinweis: Der Web-Prozess kann Discord-Anzeigenamen nicht ohne Bot/API
aufloesen -> es wird die User-ID angezeigt (Namens-Anreicherung = spaeter,
z.B. via Username-Spalte in leveling oder Discord-API-Cache).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from web.templates_setup import erstelle_templates

from modules.database.db_manager import get_read_db
from modules.guild_context import GuildConfig, get_primary_guild_id
from modules.member_cache import get_members
from utils.config import ADMIN_DATA_DIR
from utils.logger import get_logger
from web.auth import require_auth, require_perm

logger = get_logger("web.routes.leveling")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)

router = APIRouter(tags=["Leveling"])

LEADERBOARD_LIMIT = 100


async def _load_levelup_config(guild_id: int) -> dict:
    """
    Level-Up-Benachrichtigungs- + Leaderboard-Config der Guild aus `guild_config`.

    Liefert announce_channel (str|"" fuer Template), levelup_ping (bool),
    leaderboard_card (bool) + leaderboard_bg (str|"" — Anzeige, Upload via Slash)
    und remove_lower (bool — nur hoechste Belohnungsrolle behalten).
    """
    default = {
        "announce_channel": "", "levelup_ping": True,
        "leaderboard_card": False, "leaderboard_bg": "", "remove_lower": False,
    }
    try:
        announce = await GuildConfig.get(guild_id, "leveling.announce_channel", None)
        ping = await GuildConfig.get(guild_id, "leveling.levelup_ping", True)
        lb_card = await GuildConfig.get(
            guild_id, "leveling.leaderboard_card_enabled", False
        )
        lb_bg = await GuildConfig.get(guild_id, "leveling.leaderboard_bg", None)
        remove_lower = await GuildConfig.get(
            guild_id, "leveling.remove_lower_rewards", False
        )
    except Exception as e:
        logger.error(f"Level-Up-Config laden fehlgeschlagen: {e}")
        return default
    return {
        "announce_channel": str(announce) if announce else "",
        "levelup_ping": bool(ping),
        "leaderboard_card": bool(lb_card),
        "leaderboard_bg": str(lb_bg) if lb_bg else "",
        "remove_lower": bool(remove_lower),
    }


def _format_voice(minutes: int) -> str:
    """Voice-Minuten in lesbares Format (z.B. '3h 20m')."""
    minutes = int(minutes or 0)
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m"


async def _load_leaderboard(guild_id: int) -> list[dict]:
    """Top-Spieler der Guild aus SQLite (XP absteigend)."""
    try:
        db = await get_read_db()
        cursor = await db.execute(
            "SELECT user_id, xp, level, messages, voice_minutes "
            "FROM leveling WHERE guild_id = ? "
            "ORDER BY xp DESC LIMIT ?",
            (str(guild_id), LEADERBOARD_LIMIT),
        )
        rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Leaderboard-Query fehlgeschlagen: {e}")
        return []

    # E3: Anzeigenamen + Avatare aus member_cache nachladen (Batch),
    # damit das Leaderboard Namen statt roher User-IDs zeigt.
    user_ids = [str(row[0]) for row in rows]
    cache: dict = {}
    try:
        cache = await get_members(guild_id, user_ids)
    except Exception as e:  # noqa: BLE001 — Anreicherung nie fatal
        logger.error(f"member_cache-Anreicherung fehlgeschlagen: {e}")

    entries: list[dict] = []
    for idx, row in enumerate(rows, start=1):
        uid = str(row[0])
        meta = cache.get(uid, {})
        entries.append({
            "rank": idx,
            "user_id": uid,
            "name": meta.get("display_name") or "",
            "avatar_url": meta.get("avatar_url") or "",
            "xp": row[1] or 0,
            "level": row[2] or 0,
            "messages": row[3] or 0,
            "voice": _format_voice(row[4] or 0),
        })
    return entries


@router.get("/leveling", response_class=HTMLResponse)
async def leveling_page(request: Request, current_user: dict = Depends(require_auth)):
    """Web-Leaderboard + Level-Up-Benachrichtigungs-Config der Haupt-Guild."""
    guild_id = get_primary_guild_id()
    entries: list[dict] = []
    cfg = {"announce_channel": "", "levelup_ping": True}
    if guild_id is not None:
        entries = await _load_leaderboard(guild_id)
        cfg = await _load_levelup_config(guild_id)

    return templates.TemplateResponse(request, "leveling.html", {
        "request": request,
        "user": current_user,
        "entries": entries,
        "guild_set": guild_id is not None,
        "total": len(entries),
        "cfg": cfg,
        **({k: v for k, v in _karte_kontext(request, guild_id, await _karte_config(guild_id)).items()
            if k not in ("request", "success", "error")} if guild_id is not None else {"karte": {}}),
        # Quittung des letzten Resets (leer, solange keiner lief)
        "status": _reset_status(),
        "success": "",
        "error": "",
    })


@router.post("/leveling/config", response_class=HTMLResponse)
async def leveling_config_save(
    request: Request, current_user: dict = Depends(require_perm("leveling", "edit"))
):
    """
    Level-Up-Benachrichtigung speichern: Announce-Channel, Member-Ping,
    Leaderboard-Karte und ob nur die hoechste Belohnungsrolle bleibt.

    Schreibt nach `guild_config` (Keys `leveling.announce_channel` /
    `leveling.levelup_ping` / `leveling.leaderboard_card_enabled` /
    `leveling.remove_lower_rewards`). Der Bot liest sie ueber den 15s-TTL-Cache —
    Aenderung greift spaetestens nach TTL ohne Bot-Neustart.
    """
    guild_id = get_primary_guild_id()
    success = ""
    error = ""

    if guild_id is None:
        error = "Keine Haupt-Guild gesetzt (ENV GUILD_ID)."
        cfg = {"announce_channel": "", "levelup_ping": True}
    else:
        try:
            form = await request.form()
            raw_channel = str(form.get("announce_channel", "")).strip()
            ping = "levelup_ping" in form
            lb_card = "leaderboard_card" in form
            remove_lower = "remove_lower" in form

            # Channel-ID validieren: leer = entfernen, sonst nur Ziffern (Snowflake)
            channel_val: int | None = None
            if raw_channel:
                if raw_channel.isdigit():
                    channel_val = int(raw_channel)
                else:
                    raise ValueError("Channel-ID muss eine reine Zahl sein.")

            await GuildConfig.set(
                guild_id, "leveling.announce_channel", channel_val,
                updated_by="dashboard",
            )
            await GuildConfig.set(
                guild_id, "leveling.levelup_ping", ping, updated_by="dashboard",
            )
            await GuildConfig.set(
                guild_id, "leveling.leaderboard_card_enabled", lb_card,
                updated_by="dashboard",
            )
            await GuildConfig.set(
                guild_id, "leveling.remove_lower_rewards", remove_lower,
                updated_by="dashboard",
            )
            logger.info(
                f"Level-Up-Config gespeichert von "
                f"{current_user.get('username', 'Unbekannt')}: "
                f"channel={channel_val} ping={ping} lb_card={lb_card} "
                f"remove_lower={remove_lower}"
            )
            success = "Level-Einstellungen gespeichert."
        except ValueError as e:
            error = f"Ungueltige Eingabe: {e}"
        except Exception as e:
            logger.error(f"Level-Up-Config speichern fehlgeschlagen: {e}")
            error = "Fehler beim Speichern. Details im Server-Log."

        cfg = await _load_levelup_config(guild_id)

    # Partial zurueck (HTMX-Swap des Config-Blocks) — CSRF-Header kommt via
    # body[hx-headers] aus base_v5.html, daher HTMX statt nativem Form-Post.
    return templates.TemplateResponse(request, "partials/leveling_config.html", {
        "request": request,
        "guild_set": guild_id is not None,
        "cfg": cfg,
        "success": success,
        "error": error,
    })


# ----------------------------------------------------------------------
# Kompletter Reset — Auftrag an den Bot
# ----------------------------------------------------------------------

RESET_QUEUE_DIR = ADMIN_DATA_DIR / "leveling_queue"
RESET_STATUS_FILE = ADMIN_DATA_DIR / "leveling_reset_status.json"

# S2 (docs/UMBAU_PLAN_2026-10-19.md): Reset bis zum Leveling-Umbau (Phase 5) gesperrt.
# Der Job nullt den Speicher vor der DB, laesst laufende Voice-Sitzungen stehen und
# loescht auch dann, wenn er Rollen nicht abnehmen kann. Ein Backup gibt es nicht.
RESET_GESPERRT = (
    "Der Reset ist bis zum Leveling-Umbau gesperrt: Der Ablauf ist noch fehlerhaft "
    "und legt kein Backup an."
)


def _reset_status() -> dict:
    """Quittung des letzten Resets lesen (leer, wenn noch keiner lief)."""
    try:
        if RESET_STATUS_FILE.exists():
            return json.loads(RESET_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Reset-Quittung unlesbar: {e}")
    return {}


@router.post("/leveling/reset", response_class=HTMLResponse)
async def leveling_reset(
    request: Request, current_user: dict = Depends(require_perm("leveling", "edit"))
):
    """
    Alle Levelstaende auf 0 setzen — als Auftrag an den Bot.

    Der Web-Prozess hat keine Discord-Verbindung und koennte die
    Belohnungsrollen nicht abnehmen. Deshalb wird hier nur ein Auftrag
    abgelegt; `cogs/leveling_cog.py` holt ihn binnen zehn Sekunden ab,
    nimmt erst die Rollen ab und loescht dann die Daten.

    Sicherung gegen Verklicken: das Formular muss das Wort RESET enthalten.
    """
    guild_id = get_primary_guild_id()
    success = ""
    error = ""

    if guild_id is None:
        error = "Keine Haupt-Guild gesetzt (ENV GUILD_ID)."
    elif RESET_GESPERRT:
        error = RESET_GESPERRT
    else:
        try:
            form = await request.form()
            if str(form.get("bestaetigung", "")).strip().upper() != "RESET":
                raise ValueError(
                    "Zur Bestaetigung RESET in das Feld schreiben."
                )

            RESET_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
            auftrag = {
                "guild_id": str(guild_id),
                "angefordert_von": current_user.get("username", "Dashboard"),
                "angefordert_am": datetime.now(timezone.utc).isoformat(),
            }
            ziel = RESET_QUEUE_DIR / f"reset_{int(datetime.now().timestamp())}.json"
            ziel.write_text(
                json.dumps(auftrag, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.warning(
                f"Leveling-Reset angefordert von "
                f"{current_user.get('username', 'Unbekannt')} -> {ziel.name}"
            )
            success = (
                "Reset beauftragt. Der Bot fuehrt ihn binnen zehn Sekunden aus "
                "und nimmt dabei die Belohnungsrollen ab."
            )
        except ValueError as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Reset-Auftrag fehlgeschlagen: {e}")
            error = "Auftrag konnte nicht abgelegt werden. Details im Server-Log."

    return templates.TemplateResponse(request, "partials/leveling_reset.html", {
        "request": request,
        "guild_set": guild_id is not None,
        "status": _reset_status(),
        "success": success,
        "error": error,
    })


# ----------------------------------------------------------------------
# Level-Up-Karte gestalten (21.10.2026)
# ----------------------------------------------------------------------
# Marshal rendert die Karte (cogs/leveling_cog.py::_karte_png) mit denselben
# Keys; der Web-Prozess rendert nur die Vorschau. Hintergrundbilder liegen in
# ADMIN_DATA_DIR/levelup_backgrounds, dort liest sie auch der Bot.

import asyncio  # noqa: E402
import io  # noqa: E402
import re  # noqa: E402

from fastapi.responses import Response  # noqa: E402

KARTE_BG_DIR = ADMIN_DATA_DIR / "levelup_backgrounds"
KARTE_BG_NAME = "bg.png"
KARTE_MAX_BYTES = 8 * 1024 * 1024
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
KARTE_STILE = {"neon": "Neon (Violett, Magenta, Cyan)", "akzent": "Eine Farbe (Akzent)"}
TITEL_MAX, TEXT_MAX = 20, 80


async def _karte_config(guild_id: int) -> dict:
    """Gespeicherte Karten-Gestaltung aus `guild_config` (Defaults wie modules/leveling.py)."""
    async def g(key, default):
        return await GuildConfig.get(guild_id, f"leveling.{key}", default)

    bg = await g("levelup_card_bg", None)
    return {
        "aktiv": bool(await g("levelup_card_enabled", False)),
        "stil": await g("levelup_card_stil", "neon") or "neon",
        "akzent": await g("levelup_card_accent", "#B84DFF") or "#B84DFF",
        "titel": await g("levelup_card_titel", "LEVEL UP") or "LEVEL UP",
        "text": await g("levelup_card_text", "") or "",
        "rolle_zeigen": bool(await g("levelup_card_rolle_zeigen", True)),
        "bild": bool(bg) and (KARTE_BG_DIR / str(bg)).exists(),
    }


def _karte_pruefen(stil: str, akzent: str, titel: str, text: str) -> tuple[str, str, str, str]:
    """Formularwerte begrenzen; ValueError mit verstaendlicher Meldung."""
    stil = stil if stil in KARTE_STILE else "neon"
    akzent = (akzent or "").strip()
    if not _HEX.match(akzent):
        raise ValueError("Die Akzentfarbe muss die Form #RRGGBB haben.")
    titel = (titel or "").strip() or "LEVEL UP"
    if len(titel) > TITEL_MAX:
        raise ValueError(f"Der Titel darf höchstens {TITEL_MAX} Zeichen haben.")
    text = (text or "").strip()
    if len(text) > TEXT_MAX:
        raise ValueError(f"Die Zeile darf höchstens {TEXT_MAX} Zeichen haben.")
    return stil, akzent, titel, text


def _bild_speichern(raw: bytes) -> None:
    """Upload neu als PNG kodieren (entfernt Metadaten, prueft dass es ein Bild ist)."""
    from PIL import Image

    im = Image.open(io.BytesIO(raw))
    im.verify()  # bricht bei kaputten/fremden Dateien ab
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    if im.width > 2400:
        im = im.resize((2400, round(im.height * 2400 / im.width)), Image.LANCZOS)
    KARTE_BG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = KARTE_BG_DIR / (KARTE_BG_NAME + ".tmp")
    im.save(tmp, "PNG")
    tmp.replace(KARTE_BG_DIR / KARTE_BG_NAME)


def _karte_kontext(request: Request, guild_id, cfg: dict, success: str = "", error: str = "") -> dict:
    return {
        "request": request, "guild_set": guild_id is not None, "karte": cfg,
        "stile": KARTE_STILE, "titel_max": TITEL_MAX, "text_max": TEXT_MAX,
        "stand": int(datetime.now().timestamp()), "success": success, "error": error,
    }


@router.get("/leveling/karte/vorschau.png")
async def leveling_karte_vorschau(
    request: Request, current_user: dict = Depends(require_perm("leveling", "edit"))
):
    """Vorschau der Karte; Formularwerte kommen als Query, das Bild ist das gespeicherte."""
    from modules.levelup_card import render_levelup_card
    from modules.platzhalter import render as platzhalter_render
    from modules.channel_snapshot import read_snapshot

    guild_id = get_primary_guild_id()
    cfg = await _karte_config(guild_id) if guild_id is not None else {}
    q = request.query_params
    try:
        stil, akzent, titel, text = _karte_pruefen(
            q.get("stil", cfg.get("stil", "neon")), q.get("akzent", cfg.get("akzent", "#B84DFF")),
            q.get("titel", cfg.get("titel", "LEVEL UP")), q.get("text", cfg.get("text", "")),
        )
    except ValueError:
        stil, akzent, titel, text = "neon", "#B84DFF", "LEVEL UP", ""
    rolle_zeigen = q.get("rolle", "1" if cfg.get("rolle_zeigen", True) else "0") == "1"
    name = str(current_user.get("username") or "Mitglied")
    snap = await asyncio.to_thread(read_snapshot)
    unterzeile = platzhalter_render(text, extra={
        "user": name, "user.name": name, "level": "5", "server": snap.get("guild_name") or "Server",
    }) if text else None
    bg = KARTE_BG_DIR / KARTE_BG_NAME if cfg.get("bild") else None
    png = await asyncio.to_thread(
        render_levelup_card, bg, None, name, 5, 62, 155, akzent, titel,
        stil=stil, unterzeile=unterzeile,
        rolle="Neuzugang | Level 5" if rolle_zeigen else None,
    )
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/leveling/karte", response_class=HTMLResponse)
async def leveling_karte_speichern(
    request: Request, current_user: dict = Depends(require_perm("leveling", "edit"))
):
    """Karten-Gestaltung speichern (HTMX, multipart wegen Bild-Upload)."""
    guild_id = get_primary_guild_id()
    success = error = ""
    if guild_id is None:
        return templates.TemplateResponse(request, "partials/leveling_karte.html",
                                          _karte_kontext(request, None, {}, error="Keine Haupt-Guild gesetzt."))
    user = current_user.get("username", "dashboard")
    try:
        form = await request.form()
        stil, akzent, titel, text = _karte_pruefen(
            str(form.get("stil", "neon")), str(form.get("akzent", "")),
            str(form.get("titel", "")), str(form.get("text", "")),
        )
        datei = form.get("bild")
        bild_neu = None
        if datei is not None and getattr(datei, "filename", ""):
            raw = await datei.read(KARTE_MAX_BYTES + 1)
            if len(raw) > KARTE_MAX_BYTES:
                raise ValueError("Das Bild ist größer als 8 MB.")
            try:
                await asyncio.to_thread(_bild_speichern, raw)
            except Exception:  # noqa: BLE001 — kein Bild oder kaputt
                raise ValueError("Die Datei ist kein lesbares Bild (PNG oder JPG).") from None
            bild_neu = KARTE_BG_NAME
        werte = {
            "levelup_card_enabled": "aktiv" in form,
            "levelup_card_stil": stil,
            "levelup_card_accent": akzent,
            "levelup_card_titel": titel,
            "levelup_card_text": text,
            "levelup_card_rolle_zeigen": "rolle_zeigen" in form,
        }
        if bild_neu:
            werte["levelup_card_bg"] = bild_neu
        elif "bild_entfernen" in form:
            werte["levelup_card_bg"] = None
        for key, wert in werte.items():
            await GuildConfig.set(guild_id, f"leveling.{key}", wert, updated_by=user)
        logger.info(f"Level-Up-Karte gespeichert von {user}: aktiv={werte['levelup_card_enabled']} stil={stil}")
        success = "Gespeichert. Level-Up und /rank nutzen die neue Gestaltung nach spätestens 15 Sekunden."
    except ValueError as e:
        error = str(e)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Level-Up-Karte speichern fehlgeschlagen: {e}")
        error = "Speichern fehlgeschlagen. Details im Server-Log."
    return templates.TemplateResponse(request, "partials/leveling_karte.html",
                                      _karte_kontext(request, guild_id, await _karte_config(guild_id), success, error))

"""
Modulübersicht für Marshal: eine Karte je Modul mit dem Zustand, den der Bot sieht.

Muster aus der Dashboard-Analyse (docs/recherche/DASHBOARDS_LIVE_2026-10-20.md):
Kartenraster mit Schalter und „Einstellen" (Maki), kaputte Verweise sichtbar machen
(Maki „Deleted Channel", Dyno-Diagnose).

Gelesen wird nur, was der Bot selbst liest: guild_config, guild_modules, die
JSON-Dateien unter data/admin und die SQLite-Tabellen. Die Dateien aus
`marshal_bot_route.MODULE_FILE_MAP` liest kein Bot und bleiben hier außen vor.

Schalter gibt es nur für Module, deren Bot-Code `GuildConfig.is_module_enabled`
abfragt (`SCHALTBAR`). Befund C-10: ein Schalter ohne Leser wirkt heil und ändert
nichts. `tests/test_modul_uebersicht.py` prüft deshalb jeden Leser.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from modules.database.db_manager import get_read_db
from modules.guild_context import GuildConfig, get_primary_guild_id
from utils.config import ADMIN_DATA_DIR, funktion_aktiv
from utils.logger import get_logger
from web.channel_bridge import get_channels
from web.routes.moderation_route import RULES as AUTOMOD_REGELN
from web.temp_voice_bridge import read_temp_voice_hubs

logger = get_logger("web.module_uebersicht")

# Modul -> Stelle im Bot, die den Schalter liest (für Anzeige und Test)
SCHALTBAR: dict[str, str] = {
    "leveling": "cogs/leveling_cog.py (_leveling_enabled)",
    "counting": "cogs/counting_cog.py (on_message, on_message_delete)",
    "audit": "modules/audit_logger.py (_send_embed)",
}

# Quellen, die die Bots lesen (Pfade wie in den jeweiligen Modulen)
AUDIT_DATEI = ADMIN_DATA_DIR / "audit_config.json"            # modules/audit_logger.py
TICKET_DATEI = ADMIN_DATA_DIR / "ticket_config.json"          # modules/tickets.py
BACKUP_AUTO_DATEI = ADMIN_DATA_DIR / "server_backup_auto.json"  # cogs/server_backup_cog.py
BACKUP_ORDNER = ADMIN_DATA_DIR / "server_backups"             # modules/server_backup.py
EMBED_QUEUE = ADMIN_DATA_DIR / "embed_queue"                  # cogs/embed_sender_cog.py

AUDIT_BEREICHE: tuple[tuple[str, str], ...] = (
    ("mod_log_channel", "Moderation"),
    ("join_log_channel", "Beitritte"),
    ("member_log_channel", "Mitglieder"),
    ("role_log_channel", "Rollen"),
    ("server_log_channel", "Server"),
    ("message_log_channel", "Nachrichten"),
    ("voice_log_channel", "Voice"),
)

STATUS_TEXT = {
    "aktiv": "Aktiv",
    "aus": "Aus",
    "offen": "Nicht eingerichtet",
    "pruefen": "Prüfen",
}

NUR_BEFEHLE = (
    "Profil (/profil)",
    "verknüpfte Konten (/link)",
    "Kanalthemen (/setup_topics)",
    "Benachrichtigungen",
    "Befehlsstatistik (läuft still mit)",
)


# --- Laden ------------------------------------------------------------------

def _json_datei(pfad: Path) -> Optional[dict]:
    """JSON-Datei lesen; None wenn sie fehlt oder kaputt ist."""
    try:
        with open(pfad, "r", encoding="utf-8") as f:
            daten = json.load(f)
        return daten if isinstance(daten, dict) else None
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"{pfad.name} nicht lesbar: {e}")
        return None


def _dateien_lesen() -> dict[str, Any]:
    """Alle Datei-Quellen in einem Thread-Aufruf."""
    letztes_backup = None
    try:
        zeiten = [p.stat().st_mtime for p in BACKUP_ORDNER.glob("*.json")]
        letztes_backup = max(zeiten) if zeiten else None
    except OSError as e:
        logger.warning(f"Backup-Ordner nicht lesbar: {e}")

    wartend = 0
    for p in EMBED_QUEUE.glob("embed_*.json"):
        job = _json_datei(p)
        if job and job.get("status") == "pending":
            wartend += 1

    try:
        hubs = read_temp_voice_hubs()
    except Exception as e:  # noqa: BLE001 — Übersicht zeigt dann 0 Hubs
        logger.warning(f"Temp-Voice-Hubs nicht lesbar: {e}")
        hubs = []

    snapshot = get_channels()
    return {
        "audit": _json_datei(AUDIT_DATEI),
        "tickets": _json_datei(TICKET_DATEI),
        "backup_auto": _json_datei(BACKUP_AUTO_DATEI),
        "letztes_backup": letztes_backup,
        "embeds_wartend": wartend,
        "hubs": hubs,
        "kanaele": {str(c.get("id")): c.get("name", "") for c in snapshot.get("channels", [])},
    }


async def _zahl(sql: str, params: tuple = ()) -> Optional[int]:
    """Eine Zählung aus der DB; None wenn die Tabelle fehlt."""
    try:
        conn = await get_read_db()
        cursor = await conn.execute(sql, params)
        zeile = await cursor.fetchone()
        return int(zeile[0]) if zeile else 0
    except Exception as e:  # noqa: BLE001 — fehlende Tabelle ist kein Fehler der Seite
        logger.debug(f"Zählung fehlgeschlagen ({sql[:40]}): {e}")
        return None


async def _willkommen(gid: str) -> Optional[dict]:
    """Zeile aus welcome_config; die Tabelle legt erst der Cog an."""
    try:
        conn = await get_read_db()
        cursor = await conn.execute(
            "SELECT channel_id, enabled FROM welcome_config WHERE guild_id = ?", (gid,)
        )
        zeile = await cursor.fetchone()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"welcome_config nicht lesbar: {e}")
        return None
    if zeile is None:
        return None
    return {"channel_id": zeile[0], "enabled": bool(zeile[1])}


async def lade_quellen() -> dict[str, Any]:
    """Sammelt alle Quellen der Übersicht für die Haupt-Guild."""
    gid = get_primary_guild_id()
    quellen: dict[str, Any] = {"guild_id": gid, "jetzt": time.time()}
    if gid is None:
        quellen.update({"cfg": {}, "module": {}})
    else:
        quellen["cfg"], quellen["module"] = await asyncio.gather(
            GuildConfig.get_all(gid), GuildConfig.get_modules(gid)
        )
    quellen.update(await asyncio.to_thread(_dateien_lesen))
    sgid = str(gid) if gid is not None else ""
    (quellen["rr_panels"], quellen["giveaways_laufend"], quellen["custom_commands"],
     quellen["willkommen"]) = await asyncio.gather(
        _zahl("SELECT COUNT(DISTINCT message_id) FROM reaction_roles WHERE guild_id = ?", (sgid,)),
        _zahl("SELECT COUNT(*) FROM giveaways WHERE ended = 0"),
        _zahl("SELECT COUNT(*) FROM custom_commands"),
        _willkommen(sgid),
    )
    # Automod-Regeln: der Bot nimmt den guild_config-Wert, sonst den Code-Standard
    # (Wortfilter/Anti-Spam zusätzlich mit dem features-Schalter aus config.json).
    quellen["features"] = {k: funktion_aktiv(k) for k in ("word_filter", "anti_spam")}
    return quellen


# --- Karten bauen (rein, testbar) ------------------------------------------

def kanal_info(kanaele: dict[str, str], label: str, cid: Any) -> Optional[dict]:
    """Kanal-ID -> Anzeige. `fehlt` nur, wenn der Snapshot da ist und die ID nicht kennt."""
    if not cid:
        return None
    cid = str(cid)
    name = kanaele.get(cid)
    return {"label": label, "id": cid, "name": name, "fehlt": bool(kanaele) and name is None}


def _karte(key: str, name: str, gruppe: str, text: str, status: str, info: str,
           kanaele: list = (), schalter: Optional[bool] = None, ohne_schalter: str = "",
           einstellen: str = "", link: str = "") -> dict:
    """Eine Karte; ein gelöschter Zielkanal macht aus „aktiv" ein „prüfen"."""
    kan = [k for k in kanaele if k]
    fehlend = [k for k in kan if k["fehlt"]]
    if status == "aktiv" and fehlend:
        status = "pruefen"
        info = f"Zielkanal gelöscht ({', '.join(k['label'] for k in fehlend)}). {info}".strip()
    return {
        "key": key, "name": name, "gruppe": gruppe, "text": text,
        "status": status, "status_text": STATUS_TEXT[status], "info": info,
        "kanaele": kan, "schalter": schalter, "ohne_schalter": ohne_schalter,
        "einstellen": einstellen, "link": link,
    }


def _an(q: dict, modul: str) -> bool:
    return bool(q.get("module", {}).get(modul, True))


def _uhrzeit(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def baue_karten(q: dict) -> list[dict]:
    """Alle Modulkarten aus den gesammelten Quellen."""
    cfg = q.get("cfg", {})
    kan = q.get("kanaele", {})
    karten: list[dict] = []

    # --- Sicherheit ---
    verify_an = bool(cfg.get("security.verification_enabled", False))
    verify_rolle = cfg.get("security.verification_role_id")
    sperre_bis = cfg.get("security.raid_lock_until")
    sperre_aktiv = bool(sperre_bis) and float(sperre_bis) > q.get("jetzt", time.time())
    if not verify_an:
        status, info = "aus", "Neue Mitglieder sehen den Server ohne Bestätigung."
    elif not verify_rolle:
        status, info = "pruefen", "Eingeschaltet, aber keine Rolle hinterlegt."
    else:
        status, info = "aktiv", "Neue Mitglieder bestätigen per Knopf und bekommen die Rolle."
    if sperre_aktiv:
        info += " Wegen der Raid-Sperre gerade pausiert."
    karten.append(_karte(
        "verifizierung", "Verifizierung", "Sicherheit",
        "Knopf zum Freischalten neuer Mitglieder.", status, info,
        ohne_schalter="Sicherheit wird bewusst in Discord geschaltet.",
        einstellen="/sicherheit verifizierung",
    ))

    schwelle = int(cfg.get("security.raid_threshold", 0) or 0)
    fenster = cfg.get("security.raid_window_seconds", 10)
    sperre_min = cfg.get("security.raid_lock_minutes", 10)
    alarm = kanal_info(kan, "Alarm", cfg.get("security.raid_alert_channel_id"))
    if schwelle <= 0:
        status, info = "aus", "Beitrittswellen werden nicht erkannt."
    else:
        status = "aktiv"
        info = f"Ab {schwelle} Beitritten in {fenster} s Alarm"
        info += f" und {sperre_min} min Sperre." if sperre_min else ", ohne Sperre."
        if alarm is None:
            info += " Alarm geht in den Systemkanal."
    if sperre_aktiv:
        status, info = "pruefen", f"Sperre läuft bis {_uhrzeit(float(sperre_bis))}. {info}"
    karten.append(_karte(
        "raidschutz", "Raid-Schutz", "Sicherheit",
        "Erkennt Beitrittswellen und sperrt Einladungen, DMs und Verifizieren.",
        status, info, kanaele=[alarm],
        ohne_schalter="Sicherheit wird bewusst in Discord geschaltet.",
        einstellen="/sicherheit raidschutz",
    ))

    regeln = []
    for key, label, standard in AUTOMOD_REGELN:
        wert = cfg.get(f"moderation.{key}")
        if wert is None:
            wert = standard and q.get("features", {}).get(key, True)
        regeln.append((label.split(" (")[0], bool(wert)))
    an = [label for label, wert in regeln if wert]
    karten.append(_karte(
        "automod", "Automod", "Sicherheit",
        "Wortfilter, Anti-Spam und weitere Filter für Nachrichten.",
        "aktiv" if an else "aus",
        (f"An: {', '.join(an)}. " if an else "") + f"{len(regeln) - len(an)} von {len(regeln)} Filtern aus.",
        ohne_schalter="Jeder Filter hat seinen Schalter auf der Seite Moderation.",
        link="/moderation",
    ))

    karten.append(_karte(
        "warn", "Verwarnungen", "Sicherheit",
        "/warn mit automatischer Strafe ab einer Anzahl Verwarnungen.",
        "aktiv", "Stufen: Mute ab 3, Kick ab 6, Bann ab 10. Verwarnungen verfallen nach 30 Tagen.",
        ohne_schalter="Nur Befehle, nichts läuft von selbst.", einstellen="/warn",
    ))

    audit = q.get("audit") or {}
    bereiche = [(label, audit.get(key)) for key, label in AUDIT_BEREICHE]
    gesetzt = [(label, cid) for label, cid in bereiche if cid]
    ziele: dict[str, list[str]] = {}
    for label, cid in gesetzt:
        ziele.setdefault(str(cid), []).append(label)
    audit_kanaele = [
        kanal_info(kan, "alle Bereiche" if len(labels) == len(gesetzt) and len(ziele) == 1
                   else ", ".join(labels), cid)
        for cid, labels in ziele.items()
    ]
    audit_an = _an(q, "audit")
    if not gesetzt:
        status, info = "offen", "Kein Bereich hat einen Log-Kanal."
    elif not audit_an:
        status, info = "aus", "Keine Meldungen in Discord. Der Eintrag im Protokoll entsteht weiter."
    else:
        status, info = "aktiv", f"{len(gesetzt)} von {len(bereiche)} Bereichen melden."
    karten.append(_karte(
        "audit", "Audit-Log", "Sicherheit",
        "Beitritte, Austritte, Rollen, Nachrichten, Voice und Moderation im Log-Kanal.",
        status, info, kanaele=audit_kanaele, schalter=audit_an, einstellen="/audit",
    ))

    auto = q.get("backup_auto") or {}
    letztes = q.get("letztes_backup")
    letztes_text = (f" Letzte Sicherung: {datetime.fromtimestamp(letztes).strftime('%d.%m. %H:%M')}."
                    if letztes else " Noch keine Sicherung vorhanden.")
    if auto.get("enabled"):
        tage = int(auto.get("interval_days", 7) or 7)
        takt = "jeden Tag" if tage == 1 else f"alle {tage} Tage"
        status, info = "aktiv", f"Automatisch {takt}." + letztes_text
    else:
        status, info = "aus", "Nur manuell per /backup." + letztes_text
    karten.append(_karte(
        "backups", "Server-Backups", "Sicherheit",
        "Sichert Rollen, Kanäle und Rechte des Servers als Datei.", status, info,
        ohne_schalter="Automatik wird per /backup geschaltet.", einstellen="/backup",
    ))

    # --- Gemeinschaft ---
    belohnungen = cfg.get("leveling.role_rewards") or {}
    lvl_kanal = kanal_info(kan, "Level-Ups", cfg.get("leveling.announce_channel"))
    lvl_an = _an(q, "leveling")
    info = f"{len(belohnungen)} Rangrollen"
    info += ", nur die höchste bleibt." if cfg.get("leveling.remove_lower_rewards") else ", alle bleiben."
    if lvl_kanal is None:
        info += " Level-Ups erscheinen im Kanal, in dem geschrieben wurde."
    karten.append(_karte(
        "leveling", "Leveling", "Gemeinschaft",
        "XP für Nachrichten und Voice, Rangrollen und Level-Up-Karten.",
        "aktiv" if lvl_an else "aus",
        info if lvl_an else "Es gibt keine XP. Rangrollen bleiben, wie sie sind.",
        kanaele=[lvl_kanal], schalter=lvl_an, link="/leveling",
    ))

    zaehl_kanal = kanal_info(kan, "Zählkanal", cfg.get("counting.channel_id"))
    zaehl_an = _an(q, "counting")
    if zaehl_kanal is None:
        status, info = "offen", "Kein Zählkanal gesetzt."
    elif not zaehl_an:
        status, info = "aus", "Der Kanal ist frei, der Stand bleibt gespeichert."
    else:
        status = "aktiv"
        info = f"Stand {cfg.get('counting.current', 0) or 0}, Rekord {cfg.get('counting.record', 0) or 0}."
    karten.append(_karte(
        "counting", "Counting", "Gemeinschaft", "Gemeinsam hochzählen, ein Fehler setzt auf 1 zurück.",
        status, info, kanaele=[zaehl_kanal], schalter=zaehl_an, einstellen="/counting",
    ))

    lfg_rolle = cfg.get("lfg.role_id")
    lfg_kanal = kanal_info(kan, "Kanal", cfg.get("lfg.channel_id"))
    karten.append(_karte(
        "lfg", "Spielersuche", "Gemeinschaft", "Ping-Rolle für Mitspielersuche mit Abklingzeit.",
        "aktiv" if lfg_rolle else "offen",
        ("Ping erlaubt " + ("nur im gesetzten Kanal." if lfg_kanal else "überall."))
        if lfg_rolle else "Keine Ping-Rolle gesetzt.",
        kanaele=[lfg_kanal], ohne_schalter="Nur Befehle und Knopf, nichts läuft von selbst.",
        link="/lfg",
    ))

    sup = cfg.get("booster.supporter_role_id")
    karten.append(_karte(
        "supporter", "HQ-Supporter", "Gemeinschaft", "Rolle automatisch für echte Server-Booster.",
        "aktiv" if sup and verify_rolle else "offen",
        "Booster bekommen die Rolle, wer nicht mehr boostet, verliert sie."
        if sup and verify_rolle else "Supporter- oder Verifiziert-Rolle fehlt.",
        ohne_schalter="Aus würde die Rollen einfrieren.", einstellen="/supporter",
    ))

    rr = q.get("rr_panels")
    karten.append(_karte(
        "reaction_roles", "Reaction Roles", "Gemeinschaft", "Rollen per Reaktion auf eine Nachricht.",
        "aktiv" if rr else "offen", f"{rr} Nachricht(en) mit Rollen." if rr else "Keine Nachricht eingerichtet.",
        ohne_schalter="Aus würde Reaktionen verlieren, die Rollen liefen auseinander.",
        einstellen="/reactionrole",
    ))

    gw = q.get("giveaways_laufend")
    karten.append(_karte(
        "giveaways", "Giveaways", "Gemeinschaft", "Verlosungen mit Teilnahme-Knopf und Auslosung.",
        "aktiv", f"{gw} läuft gerade." if gw == 1 else (f"{gw} laufen gerade." if gw else "Gerade keine Verlosung."),
        ohne_schalter="Aus würde laufende Verlosungen nie beenden.", einstellen="/giveaway",
    ))

    wk = q.get("willkommen")
    if wk is None:
        status, info, wk_kanal = "offen", "Nicht eingerichtet.", None
    else:
        wk_kanal = kanal_info(kan, "Kanal", wk.get("channel_id"))
        status = "aktiv" if wk.get("enabled") else "aus"
        info = "Begrüßt neue Mitglieder." if wk.get("enabled") else "Abgeschaltet per /welcome."
    karten.append(_karte(
        "willkommen", "Willkommen", "Gemeinschaft", "Begrüßung, Startrolle und DM für neue Mitglieder.",
        status, info, kanaele=[wk_kanal], ohne_schalter="Wird per /welcome geschaltet.",
        einstellen="/welcome",
    ))

    # --- Werkzeuge ---
    hubs = q.get("hubs") or []
    hub_kanaele = [kanal_info(kan, f"Hub {i + 1}", h.get("hub_id")) for i, h in enumerate(hubs)]
    karten.append(_karte(
        "tempvoice", "Temp-Voice", "Werkzeuge", "Eigene Talks per Beitritt, mit Steuer-Panel.",
        "aktiv" if hubs else "offen",
        f"{len(hubs)} Hub(s)." if hubs else "Kein Hub eingerichtet.",
        kanaele=hub_kanaele, ohne_schalter="Aus ließe offene Talks zurück.",
        link="#temp_voice",
    ))

    tk = q.get("tickets") or {}
    tk_kanaele = [kanal_info(kan, "Kategorie", tk.get("ticket_category_id")),
                  kanal_info(kan, "Transkripte", tk.get("log_channel_id"))]
    rollen = len(tk.get("support_roles") or [])
    karten.append(_karte(
        "tickets", "Tickets", "Werkzeuge", "Support-Tickets per Knopf, Transkript beim Schließen.",
        "aktiv" if tk else "offen",
        f"{rollen} Support-Rolle(n)." if tk else "Nicht eingerichtet.",
        kanaele=tk_kanaele, ohne_schalter="Die Ticket-Knöpfe bleiben dauerhaft im Kanal.",
        einstellen="/ticket setup",
    ))

    wartend = q.get("embeds_wartend", 0)
    karten.append(_karte(
        "embeds", "Embeds", "Werkzeuge", "Nachrichten aus dem Dashboard in einen Kanal senden.",
        "aktiv", f"{wartend} Nachricht(en) warten auf den Versand." if wartend else "Keine Nachricht in der Warteschlange.",
        ohne_schalter="Läuft nur, wenn du im Dashboard sendest.", link="#embeds",
    ))

    cc = q.get("custom_commands")
    karten.append(_karte(
        "custom_commands", "Custom Commands", "Werkzeuge", "Eigene Antwort-Befehle.",
        "aktiv" if cc else "offen", f"{cc} Befehl(e)." if cc else "Keine eigenen Befehle.",
        ohne_schalter="Antwortet nur, wenn jemand den Befehl nutzt.",
    ))
    return karten


def zusammenfassung(karten: list[dict]) -> dict:
    """Zähler für den Kopf der Übersicht."""
    zaehler = {s: 0 for s in STATUS_TEXT}
    for k in karten:
        zaehler[k["status"]] += 1
    return {"gesamt": len(karten), **zaehler,
            "pruefen_namen": [k["name"] for k in karten if k["status"] == "pruefen"]}


def gruppiert(karten: list[dict]) -> list[tuple[str, list[dict]]]:
    """Karten in Anzeige-Reihenfolge der Gruppen."""
    reihenfolge: list[str] = []
    for k in karten:
        if k["gruppe"] not in reihenfolge:
            reihenfolge.append(k["gruppe"])
    return [(g, [k for k in karten if k["gruppe"] == g]) for g in reihenfolge]

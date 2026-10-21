"""
Startseite (Bereich „Übersicht“) im Design C3.

Ersetzt die zwei überlappenden Startseiten (/home + /dashboard): oben steht,
was Aufmerksamkeit braucht, danach Kennzahlen, Bots, Game-Server,
Schnellzugriff und die letzten Ereignisse. /dashboard bleibt als
„Game-Server · Übersicht und Verlauf“ bestehen.
"""
from __future__ import annotations

from datetime import datetime, timezone

from utils.logger import get_logger

logger = get_logger(__name__)

ONLINE = {"online", "connected", "ready", "ok", "running"}

# Aufgabe je Bot in drei Wörtern (Anzeige auf der Startseite)
BOT_AUFGABE = {
    "monitor": "meldet Ausfälle und Status",
    "gameserver": "steuert die Game-Server",
    "admin": "moderiert den Discord",
    "pipeline": "verarbeitet TikTok-Videos",
}

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]

# Grenzwerte für „Braucht Aufmerksamkeit“ (wie bisher die Ampel auf /home)
CPU_GRENZE, RAM_GRENZE, DISK_GRENZE = 85, 90, 90

SCHNELLZUGRIFF = (
    {"titel": "Discord-Module", "text": "Alle Module von Marshal mit Schaltern und Status.",
     "url": "/marshal-bot", "icon": "i-chat", "bereich": "Discord-Server"},
    {"titel": "Game-Server", "text": "Status, Verlauf und Steuerung aller Server.",
     "url": "/dashboard", "icon": "i-pad", "bereich": "Game-Server"},
    {"titel": "Leveling", "text": "Rangliste, Rangrollen und XP.",
     "url": "/leveling", "icon": "i-award", "bereich": "Discord-Server"},
    {"titel": "Automod", "text": "Wortfilter, Anti-Spam und weitere Filter.",
     "url": "/moderation", "icon": "i-filter", "bereich": "Discord-Server"},
    {"titel": "Bots und Dienste", "text": "Dienste neu starten, System-Updates.",
     "url": "/system", "icon": "i-cpu", "bereich": "Bots & System"},
    {"titel": "Einstellungen", "text": "Schalter, Intervalle und Schwellwerte.",
     "url": "/config", "icon": "i-sliders", "bereich": "Bots & System"},
)


def datum_text(jetzt: datetime | None = None) -> str:
    """„Mittwoch, 21. Oktober“ — ohne Locale-Abhängigkeit."""
    d = jetzt or datetime.now()
    return f"{WOCHENTAGE[d.weekday()]}, {d.day}. {MONATE[d.month - 1]}"


def aufmerksamkeit(servers: list[dict], system: dict, bots: list[dict], karten: list[dict],
                   gestoppt: set[str] | frozenset = frozenset()) -> list[dict]:
    """
    Punkte, die jetzt eine Handlung brauchen — wichtigstes zuerst.

    Ein bewusst gestoppter Game-Server (manual_stop_state) ist kein Alarm.

    Returns:
        Liste mit ``titel``, ``text``, ``url``, ``knopf``, ``icon``.
    """
    punkte: list[dict] = []
    for b in bots:
        if b.get("status") not in ONLINE:
            zustand = "meldet sich nicht mehr" if b.get("status") == "veraltet" else "ist offline"
            punkte.append({"titel": f"{b.get('name', 'Ein Bot')} {zustand}.",
                           "text": "Status und Neustart findest du bei Bots und Dienste.",
                           "url": "/system", "knopf": "Ansehen", "icon": "i-bot"})
    for s in servers:
        if s.get("status") not in ONLINE and s.get("id") not in gestoppt:
            punkte.append({"titel": f"{s.get('name', 'Ein Game-Server')} ist offline.",
                           "text": "Starten und Logs findest du auf der Seite des Servers.",
                           "url": "/dashboard", "knopf": "Ansehen", "icon": "i-server"})
    for k in karten:
        if k.get("status") == "pruefen":
            punkte.append({"titel": f"{k['name']}: {k.get('info') or 'bitte prüfen.'}",
                           "text": "Das Modul läuft, aber ein Ziel fehlt.",
                           "url": f"/marshal-bot#mod-{k['key']}", "knopf": "Ansehen", "icon": "i-alert"})
    last = []
    if (system.get("cpu_percent") or 0) >= CPU_GRENZE:
        last.append(f"CPU {round(system['cpu_percent'])} %")
    if (system.get("ram_percent") or 0) >= RAM_GRENZE:
        last.append(f"RAM {round(system['ram_percent'])} %")
    if (system.get("disk_percent") or 0) >= DISK_GRENZE:
        last.append(f"Festplatte {round(system['disk_percent'])} %")
    if last:
        punkte.append({"titel": "Der Server ist stark ausgelastet: " + ", ".join(last) + ".",
                       "text": "Prüfe, welcher Dienst die Last erzeugt.",
                       "url": "/system", "knopf": "Ansehen", "icon": "i-gauge"})
    return punkte


def bots_kompakt(bots: list[dict]) -> list[dict]:
    return [{"name": b.get("name", b.get("id", "")).replace(" Bot", ""),
             "online": b.get("status") in ONLINE,
             "status_text": "Online" if b.get("status") in ONLINE else
                            ("Keine Meldung" if b.get("status") == "veraltet" else "Offline"),
             "aufgabe": BOT_AUFGABE.get(b.get("id", ""), "")} for b in bots]


def ereignisse_kompakt(events: list[dict], anzahl: int = 6, heute: datetime | None = None) -> list[dict]:
    """
    Letzte Ereignisse mit Uhrzeit (HH:MM), Tag (leer = heute, sonst „gestern“
    oder „TT.MM.“) und Absender. Von den Update-Prüfungen bleibt nur die neueste.
    """
    tag_heute = (heute or datetime.now()).date()
    aus, update_gesehen = [], False
    for e in events:
        if len(aus) >= anzahl:
            break
        if e.get("event_type") == "package_check":
            if update_gesehen:
                continue
            update_gesehen = True
        zeit, tag = str(e.get("timestamp", ""))[11:16], ""
        try:
            ts = datetime.fromisoformat(str(e.get("timestamp")))
            zeit = ts.strftime("%H:%M")
            diff = (tag_heute - ts.date()).days
            tag = "" if diff == 0 else ("gestern" if diff == 1 else ts.strftime("%d.%m."))
        except (TypeError, ValueError):
            pass
        quelle = e.get("server_id") or e.get("category") or ""
        aus.append({"zeit": zeit, "tag": tag, "text": e.get("message", ""), "quelle": quelle})
    return aus


# Bereich (dashboard_audit.resource) -> Anzeige
BEREICH_TEXT = {
    "system": "Bots & System", "rbac": "Zugang und Rechte", "leveling": "Leveling",
    "lfg": "Spielersuche", "moderation": "Automod", "marshal_bot": "Discord-Module",
    "minecraft": "Minecraft-Server", "satisfactory": "Satisfactory-Server",
    "temp_voice": "Temp-Voice", "audit": "Änderungsprotokoll",
}
AKTION_TEXT = {"edit": "geändert", "control": "gesteuert", "view": "angesehen"}


def aenderungen_kompakt(zeilen: list[dict], heute: datetime | None = None) -> list[dict]:
    """
    Letzte Dashboard-Änderungen (dashboard_audit, ts in UTC) für die Startseite:
    Uhrzeit, Tag, wer, was. Keine IDs, keine Details aus dem Formular.
    """
    tag_heute = (heute or datetime.now()).date()
    aus = []
    for z in zeilen:
        zeit, tag = "", ""
        try:
            ts = datetime.fromisoformat(str(z.get("ts"))).replace(tzinfo=timezone.utc).astimezone()
            zeit = ts.strftime("%H:%M")
            diff = (tag_heute - ts.date()).days
            tag = "" if diff == 0 else ("gestern" if diff == 1 else ts.strftime("%d.%m."))
        except (TypeError, ValueError):
            pass
        bereich = BEREICH_TEXT.get(str(z.get("resource")), str(z.get("resource") or ""))
        aktion = AKTION_TEXT.get(str(z.get("action")), str(z.get("action") or ""))
        aus.append({"zeit": zeit, "tag": tag, "text": f"{bereich} {aktion}",
                    "quelle": str(z.get("username") or "unbekannt")})
    return aus

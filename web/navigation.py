"""
Navigation des Dashboards (Design C3 „Lobby-HUD“, Marco-Wahl 21.10.2026).

Fünf Bereiche, sortiert nach dem, was ein Admin tun will — nicht nach Technik.
Jede Seite gehört genau einem Bereich. ``base_v5.html`` rendert daraus die
Bereichsleiste (links) und die Seitenspalte des aktiven Bereichs; die Suche in
der Kopfzeile nutzt dieselbe Liste.

Ein Eintrag zeigt auf eine bestehende Route. Tabs werden per ``?tab=<name>``
angesprungen (``static/c3/c3.js`` klickt den passenden ``[data-tab]``-Knopf),
Abschnitte per ``#anker``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True)
class Seite:
    """Ein Navigationseintrag."""
    key: str
    titel: str
    url: str
    icon: str
    suchwoerter: str = ""
    nur_owner: bool = False


@dataclass(frozen=True)
class Gruppe:
    titel: str
    seiten: tuple[Seite, ...]


@dataclass(frozen=True)
class Bereich:
    key: str
    titel: str
    icon: str
    untertitel: str
    gruppen: tuple[Gruppe, ...] = field(default_factory=tuple)

    @property
    def start_url(self) -> str:
        return self.gruppen[0].seiten[0].url


def _bereiche(spielserver: list) -> tuple[Bereich, ...]:
    """Baut die Bereiche. Die Game-Server kommen aus der Server-Registry."""
    server_seiten = tuple(
        Seite(f"server-{s.kennung}", s.label, f"/server/{s.kennung}", "i-server",
              f"{s.spiel} spieler konsole rcon backups updates mods")
        for s in spielserver
    )
    return (
        Bereich("overview", "Übersicht", "i-grid", "Ostfront HQ", (
            Gruppe("", (
                Seite("home", "Startseite", "/home", "i-grid", "start dashboard aufmerksamkeit"),
            )),
        )),
        Bereich("discord", "Discord-Server", "i-chat", "Module von Marshal", (
            Gruppe("", (
                Seite("marshal", "Modulübersicht", "/marshal-bot", "i-grid", "marshal module schalter"),
            )),
            # Module ohne eigene Seite springen zu ihrer Karte in der Übersicht (#mod-<key>).
            # Die alten Marshal-Tabs speichern bis auf Embeds nicht mehr (SAVE_LOCKS) und
            # werden deshalb nicht verlinkt; eingestellt wird per Befehl in Discord.
            Gruppe("Sicherheit", (
                Seite("k-verifizierung", "Verifizierung und Raid-Schutz", "/marshal-bot#mod-verifizierung", "i-shield-check", "verifizieren raid sperre beitritte sicherheit"),
                Seite("moderation", "Automod", "/moderation", "i-filter", "moderation wortfilter anti-spam caps links"),
                Seite("k-warn", "Verwarnungen", "/marshal-bot#mod-warn", "i-flag", "warn mute kick bann"),
                Seite("mod-akte", "Moderations-Akte", "/moderation/akte", "i-file", "akte notizen beweis verwarnung timeout bann kick"),
                Seite("k-audit", "Audit-Log", "/marshal-bot#mod-audit", "i-list", "log protokoll discord"),
                Seite("logs", "Log-Kanäle", "/logs", "i-hash", "log kanal audit protokoll webhook thread"),
                Seite("k-backups", "Server-Backups", "/marshal-bot#mod-backups", "i-archive", "backup sicherung rollen kanaele"),
            )),
            Gruppe("Gemeinschaft", (
                Seite("leveling", "Leveling", "/leveling", "i-award", "xp level rang rangrollen"),
                Seite("k-counting", "Counting", "/marshal-bot#mod-counting", "i-count", "zaehlen"),
                Seite("lfg", "Spielersuche", "/lfg", "i-users", "lfg mitspieler ping"),
                Seite("k-supporter", "HQ-Supporter", "/marshal-bot#mod-supporter", "i-gem", "booster"),
                Seite("k-reaction_roles", "Reaction Roles", "/marshal-bot#mod-reaction_roles", "i-smile", "reaktion rollen"),
                Seite("k-giveaways", "Giveaways", "/marshal-bot#mod-giveaways", "i-gift", "verlosung"),
                Seite("k-willkommen", "Willkommen", "/marshal-bot#mod-willkommen", "i-door", "welcome begruessung"),
                Seite("k-sticky_roles", "Sticky-Rollen", "/marshal-bot#mod-sticky_roles", "i-refresh", "rollen zurueck rueckkehr wiederkommen"),
            )),
            Gruppe("Werkzeuge", (
                Seite("m-temp_voice", "Temp-Voice", "/marshal-bot?tab=temp_voice", "i-headphones", "voice talk panel hubs"),
                Seite("k-tickets", "Tickets", "/marshal-bot#mod-tickets", "i-ticket", "support transkript"),
                Seite("m-embeds", "Embeds", "/marshal-bot?tab=embeds", "i-message", "nachricht senden embedinator"),
                Seite("k-custom_commands", "Custom Commands", "/marshal-bot#mod-custom_commands", "i-terminal", "befehle eigene"),
            )),
        )),
        Bereich("game", "Game-Server", "i-pad", "Status und Steuerung", (
            Gruppe("", (
                Seite("dashboard", "Übersicht und Verlauf", "/dashboard", "i-chart", "game server verlauf statistik"),
            )),
            Gruppe("Server", server_seiten),
        )),
        Bereich("system", "Bots & System", "i-cpu", "Bots, Dienste und Server", (
            Gruppe("Bots", (
                Seite("system", "Bots und Dienste", "/system", "i-activity", "bot status recon operator marshal pipeline echo services dienste"),
                Seite("errors", "Fehler", "/errors", "i-alert", "errors fehler log"),
            )),
            Gruppe("Server-Schutz", (
                Seite("security", "IP-Sperren und Fail2Ban", "/security", "i-shield", "sicherheit ip sperren fail2ban zertifikate ssl"),
            )),
            Gruppe("Einstellungen", (
                Seite("config", "Funktionen", "/config", "i-sliders", "konfiguration config feature toggles schalter"),
                Seite("config-intervals", "Zeitpläne", "/config?tab=intervals", "i-clock", "konfiguration intervall neustart backup"),
                Seite("config-thresholds", "Warnschwellen", "/config?tab=thresholds", "i-gauge", "konfiguration alarm schwelle schwellwert"),
                Seite("config-channels", "Kanäle", "/config?tab=channels", "i-hash", "konfiguration channels kanal"),
                Seite("config-notifications", "Benachrichtigungen", "/config?tab=notifications", "i-bell", "konfiguration benachrichtigung"),
                Seite("config-botprofiles", "Bot-Profile", "/config?tab=botprofiles", "i-bot", "avatar name profil"),
            )),
        )),
        Bereich("admin", "Verwaltung", "i-key", "Zugang und Protokolle", (
            Gruppe("Dashboard-Zugang", (
                Seite("config-login", "Wer sich anmelden darf", "/zugang", "i-users", "login zugang erlaubt", nur_owner=True),
                Seite("rbac", "Rechte je Rolle", "/rbac", "i-key", "rbac rollen rechte", nur_owner=True),
            )),
            Gruppe("Protokolle", (
                Seite("audit", "Änderungsprotokoll", "/audit", "i-history", "audit verlauf wer hat", nur_owner=True),
                Seite("changelog", "Changelog", "/changelog", "i-file", "version neu"),
            )),
        )),
    )


def _passt(seite: Seite, pfad: str, tab: str) -> int:
    """Wie gut passt die Seite zur aktuellen URL? 0 = gar nicht, höher = genauer."""
    teile = urlsplit(seite.url)
    if teile.path != pfad:
        return 0
    seiten_tab = parse_qs(teile.query).get("tab", [""])[0]
    if seiten_tab:
        return 2 if seiten_tab == tab else 0
    return 1


def navigation(pfad: str, tab: str = "", ist_owner: bool = False, spielserver: list | None = None) -> dict:
    """
    Navigation für eine Anfrage.

    Returns:
        ``bereiche`` (gefiltert nach Rechten), ``aktiv_bereich``, ``aktiv_seite``
        (Keys) und ``brotkrumen`` ("Bereich / Seite").
    """
    bereiche = []
    beste = (0, None, None)
    for b in _bereiche(spielserver or []):
        gruppen = []
        for g in b.gruppen:
            seiten = tuple(s for s in g.seiten if ist_owner or not s.nur_owner)
            if seiten:
                gruppen.append(Gruppe(g.titel, seiten))
            for s in seiten:
                wert = _passt(s, pfad, tab)
                if wert > beste[0]:
                    beste = (wert, b, s)
        if gruppen:
            bereiche.append(Bereich(b.key, b.titel, b.icon, b.untertitel, tuple(gruppen)))

    _, b_aktiv, s_aktiv = beste
    if b_aktiv is None:
        b_aktiv = bereiche[0]
        s_aktiv = None
    krumen = b_aktiv.titel + (f" / {s_aktiv.titel}" if s_aktiv else "")
    return {
        "bereiche": bereiche,
        "aktiv_bereich": b_aktiv.key,
        "aktiv_seite": s_aktiv.key if s_aktiv else "",
        "brotkrumen": krumen,
    }


def suchindex(bereiche: list[Bereich]) -> list[dict]:
    """Einträge für die Kopfzeilen-Suche (wird als JSON ins Template gerendert)."""
    eintraege = []
    for b in bereiche:
        for g in b.gruppen:
            for s in g.seiten:
                ort = b.titel + (f" · {g.titel}" if g.titel else "")
                eintraege.append({"t": s.titel, "a": ort, "href": s.url, "k": s.suchwoerter})
    return eintraege

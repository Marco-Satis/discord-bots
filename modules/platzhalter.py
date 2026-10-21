"""
Gemeinsames Platzhalter-System für alle Bot-Nachrichten.

Ein Text wie ``"Willkommen {user} auf {server}!"`` wird hier einheitlich
befüllt — Willkommensnachricht, Custom Commands und Embed-Sender nutzen
dieselbe Funktion, und das Dashboard zeigt dieselbe Liste neben jedem
Textfeld (``web/templates/partials/_platzhalter.html``).

Sicherheit:
  - Es wird NIE ``str.format`` benutzt. Ein Admin-Text wie
    ``"{0.__class__}"`` oder ``"{user.__init__}"`` bleibt damit unverändert
    stehen, statt Python-Attribute auszulesen.
  - Ersetzt wird in EINEM Regex-Durchlauf über eine feste Whitelist.
    Werte, die selbst wie Platzhalter aussehen (z. B. ein Anzeigename
    ``"{server}"``), werden nicht ein zweites Mal ersetzt.
  - Unbekannte Platzhalter bleiben wörtlich stehen (auch ``{embed}``).

Benutzung::

    from modules.platzhalter import render
    text = render(vorlage, member=member, guild=member.guild,
                  extra={"level": "5"})
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo("Europe/Berlin")
except Exception:  # pragma: no cover - tzdata fehlt nur auf exotischen Systemen
    _TZ = timezone.utc

# (Schlüssel, Beschreibung, Beispiel) — Reihenfolge = Anzeige-Reihenfolge im Dashboard
PLATZHALTER: list[tuple[str, str, str]] = [
    ("{user}", "Erwähnung des Mitglieds", "@Marco"),
    ("{user.name}", "Anzeigename des Mitglieds", "Marco"),
    ("{server}", "Name des Servers", "Ostfront HQ"),
    ("{membercount}", "Aktuelle Mitgliederzahl", "128"),
    ("{kontoalter}", "Alter des Discord-Kontos", "2 Jahre"),
    ("{beitritt}", "Datum des Server-Beitritts", "21.10.2026"),
    ("{datum}", "Heutiges Datum", "21.10.2026"),
    ("{zeit}", "Aktuelle Uhrzeit (Berlin)", "18:30"),
]

# Platzhalter, die ein Mitglied brauchen — ohne Mitglied (z. B. Embed-Sender)
# werden sie im Dashboard nicht angeboten und im Text nicht ersetzt.
MITGLIED_KEYS: frozenset[str] = frozenset(
    {"{user}", "{user.name}", "{kontoalter}", "{beitritt}"}
)

# Nur einfache Bezeichner mit optionalem Punkt-Teil; Leerzeichen, Klammern,
# Ziffern am Anfang usw. matchen gar nicht erst.
_MUSTER = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)?)\}")


def _jetzt() -> datetime:
    """Aktuelle Zeit in Europe/Berlin (eigene Funktion, damit Tests sie ersetzen können)."""
    return datetime.now(_TZ)


def jetzt_berlin() -> datetime:
    """Öffentlicher Zugang zur aktuellen Berliner Zeit (für Modul-Extras wie {date})."""
    return _jetzt()


def _als_berlin(dt: Optional[datetime]) -> Optional[datetime]:
    """Datetime nach Europe/Berlin umrechnen; naive Werte gelten als UTC (wie bei discord.py)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ)


def kontoalter_text(erstellt: Optional[datetime], jetzt: Optional[datetime] = None) -> str:
    """
    Alter als lesbarer Text: "weniger als 1 Tag", "1 Tag", "3 Tage",
    "1 Monat", "5 Monate", "1 Jahr", "2 Jahre".
    """
    if erstellt is None:
        return "unbekannt"
    jetzt = jetzt or _jetzt()
    if erstellt.tzinfo is None:
        erstellt = erstellt.replace(tzinfo=timezone.utc)
    tage = (jetzt - erstellt).days
    if tage < 1:
        return "weniger als 1 Tag"
    if tage < 31:
        return "1 Tag" if tage == 1 else f"{tage} Tage"
    if tage < 365:
        monate = tage // 30
        return "1 Monat" if monate == 1 else f"{monate} Monate"
    jahre = tage // 365
    return "1 Jahr" if jahre == 1 else f"{jahre} Jahre"


def _sicherer_name(name: str) -> str:
    """Anzeigename ohne Markdown-Wirkung und ohne @everyone/@here-Ping."""
    try:
        import discord.utils as du

        return du.escape_mentions(du.escape_markdown(name))
    except Exception:
        return name


def _werte(member: Any, guild: Any) -> dict[str, str]:
    """Alle verfügbaren Werte als {schluessel_ohne_klammern: text}."""
    jetzt = _jetzt()
    werte: dict[str, str] = {
        "datum": jetzt.strftime("%d.%m.%Y"),
        "zeit": jetzt.strftime("%H:%M"),
    }
    if guild is None and member is not None:
        guild = getattr(member, "guild", None)
    if guild is not None:
        werte["server"] = str(getattr(guild, "name", "") or "")
        anzahl = getattr(guild, "member_count", None)
        werte["membercount"] = str(anzahl if anzahl is not None else 0)
    if member is not None:
        mention = getattr(member, "mention", None)
        if mention:
            werte["user"] = str(mention)
        name = getattr(member, "display_name", None) or getattr(member, "name", None)
        if name:
            werte["user.name"] = _sicherer_name(str(name))
        werte["kontoalter"] = kontoalter_text(getattr(member, "created_at", None), jetzt)
        beitritt = _als_berlin(getattr(member, "joined_at", None))
        werte["beitritt"] = beitritt.strftime("%d.%m.%Y") if beitritt else "unbekannt"
    return werte


def _normalisiere(extra: Optional[dict]) -> dict[str, str]:
    """Extra-Schlüssel mit oder ohne Klammern akzeptieren: "level" == "{level}"."""
    if not extra:
        return {}
    aus: dict[str, str] = {}
    for k, v in extra.items():
        key = str(k).strip()
        if key.startswith("{") and key.endswith("}"):
            key = key[1:-1]
        aus[key] = "" if v is None else str(v)
    return aus


def render(
    text: str,
    *,
    member: Any = None,
    guild: Any = None,
    extra: Optional[dict] = None,
) -> str:
    """
    Platzhalter in ``text`` ersetzen.

    Args:
        text: Vorlage mit Platzhaltern wie ``{user}``.
        member: Discord-Mitglied (oder ein Objekt mit ``mention``,
            ``display_name``, ``created_at``, ``joined_at``, ``guild``).
        guild: Server; ohne Angabe ``member.guild``.
        extra: Modul-eigene Werte, z. B. ``{"level": 5}``. Sie überschreiben
            die Standardwerte gleichen Namens.

    Returns:
        Text mit ersetzten Platzhaltern. Unbekannte bleiben unverändert.
    """
    if not text or "{" not in text:
        return text or ""
    werte = _werte(member, guild)
    werte.update(_normalisiere(extra))

    def _ersetze(m: re.Match) -> str:
        wert = werte.get(m.group(1))
        return m.group(0) if wert is None else wert

    return _MUSTER.sub(_ersetze, text)


def platzhalter_daten(
    mitglied: bool = True, extra: Iterable[Iterable[str]] = ()
) -> list[dict[str, str]]:
    """
    Liste für das Dashboard: ``[{"key", "beschreibung", "beispiel"}, ...]``.

    Args:
        mitglied: False blendet die Mitglieds-Platzhalter aus (Embed-Sender).
        extra: Zusätzliche ``(key, beschreibung, beispiel)``-Tupel eines Moduls.
    """
    liste = [
        {"key": k, "beschreibung": b, "beispiel": e}
        for k, b, e in PLATZHALTER
        if mitglied or k not in MITGLIED_KEYS
    ]
    for eintrag in extra or ():
        teile = list(eintrag) + ["", "", ""]
        liste.append({"key": str(teile[0]), "beschreibung": str(teile[1]), "beispiel": str(teile[2])})
    return liste

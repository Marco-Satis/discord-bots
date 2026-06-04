"""
Discord Content-Filter (D4) — Mass-Caps + Zalgo-Erkennung (Admin Bot).

Zwei zusaetzliche Auto-Mod-Regeln, beide **standardmaessig AUS** (konservativ,
per Dashboard/Slash aktivierbar):

- Mass-Caps: Nachricht ist (fast) komplett GROSSGESCHRIEBEN ("Schreien").
- Zalgo: uebermaessige kombinierende Unicode-Zeichen (zerfranster Text), oft
  zum Stoeren / Umgehen von Filtern genutzt.

Die reinen Detektoren (`is_mass_caps` / `is_zalgo`) sind discord-frei +
unit-testbar. Die Aktivierung liegt per-Guild in `guild_config` (Cog-Ebene),
nicht in diesem Modul — dieses Modul liefert nur Schwellwerte + Logik.

NICHT fuer RCON/In-Game.
"""
from __future__ import annotations

import unicodedata


def is_mass_caps(content: str | None, min_len: int = 10, ratio: float = 0.7) -> bool:
    """
    True wenn der Buchstaben-Anteil der Nachricht ueberwiegend GROSS ist.

    Args:
        content: Nachrichtentext
        min_len: Mindestanzahl Buchstaben, sonst ignorieren (kurze Rufe ok)
        ratio: Grossbuchstaben-Anteil (0..1) ab dem als Mass-Caps gilt

    Nur Buchstaben zaehlen (Zahlen/Emojis/Satzzeichen ignoriert), damit
    "HALLO!!!" korrekt, aber "ABC123" je nach min_len nicht falsch triggert.
    """
    if not content:
        return False
    letters = [c for c in content if c.isalpha()]
    if len(letters) < min_len:
        return False
    uppers = sum(1 for c in letters if c.isupper())
    return uppers / len(letters) >= ratio


def is_zalgo(
    content: str | None, ratio: float = 0.3, min_combining: int = 4
) -> bool:
    """
    True wenn der Text uebermaessig kombinierende Unicode-Zeichen enthaelt (Zalgo).

    Args:
        content: Nachrichtentext
        ratio: Anteil kombinierender Zeichen (0..1) ab dem als Zalgo gilt
        min_combining: Mindestanzahl kombinierender Zeichen (absolut), sonst
                       ignorieren (vereinzelte Diakritika sind legitim)
    """
    if not content:
        return False
    combining = sum(1 for c in content if unicodedata.combining(c))
    if combining < min_combining:
        return False
    return combining / max(1, len(content)) >= ratio


class ContentFilter:
    """
    Mass-Caps + Zalgo-Detektoren mit konfigurierbaren Schwellwerten.

    Aktivierung erfolgt per-Guild ueber `guild_config` im Cog (nicht hier).
    Config-Key ``content_filter`` (optional, sonst Defaults)::

        {"content_filter": {"caps_min_len": 10, "caps_ratio": 0.7,
                             "zalgo_min_combining": 4, "zalgo_ratio": 0.3}}
    """

    def __init__(self, config: dict) -> None:
        cf = config.get("content_filter", {}) if config else {}
        self.caps_min_len: int = int(cf.get("caps_min_len", 10))
        self.caps_ratio: float = float(cf.get("caps_ratio", 0.7))
        self.zalgo_min_combining: int = int(cf.get("zalgo_min_combining", 4))
        self.zalgo_ratio: float = float(cf.get("zalgo_ratio", 0.3))

    def check_caps(self, content: str | None) -> bool:
        """True wenn `content` als Mass-Caps gilt (Schwellwerte aus Config)."""
        return is_mass_caps(content, self.caps_min_len, self.caps_ratio)

    def check_zalgo(self, content: str | None) -> bool:
        """True wenn `content` als Zalgo gilt (Schwellwerte aus Config)."""
        return is_zalgo(content, self.zalgo_ratio, self.zalgo_min_combining)

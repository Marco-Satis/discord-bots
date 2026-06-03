"""
Zentraler Embed-Helper — einheitlicher Stil für ALLE Bot-Nachrichten.

Konsolidiert die ~250 verstreuten ``discord.Embed(...)``-Aufrufe (36 Files).
Der Stil ist bewusst NEUTRAL (Phase 0 des Umbaus) — das finale Branding/Theme
kommt später aus Marcos Design-PDF und wird **hier zentral** gesetzt
(1 Datei restylen statt 36 Cogs anfassen).

Nutzung:
    from utils.embeds import success_embed, error_embed, base_embed

    embed = success_embed("Fertig", "Server neu gestartet.")
    await ctx.send(embed=embed)

Migration der bestehenden Embeds passiert inkrementell (nur bei Berührung) —
neuer Code nutzt diesen Helper, Alt-Code wird beim nächsten Edit umgestellt.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import discord

# --- Semantische Farben (zentral — Restyle = NUR hier ändern) --------------
COLOR_SUCCESS: int = 0x2ECC71   # grün
COLOR_ERROR: int = 0xE74C3C     # rot
COLOR_WARNING: int = 0xF39C12   # orange
COLOR_INFO: int = 0x5865F2      # Discord-Blurple
COLOR_NEUTRAL: int = 0x95A5A6   # grau
COLOR_BRAND: int = 0x5865F2     # Platzhalter bis Branding-PDF (Phase F)

# --- Branding (Platzhalter — später aus Dashboard/Config/PDF gesetzt) -------
_BRAND_FOOTER: Optional[str] = None
_BRAND_ICON: Optional[str] = None


def set_branding(footer: Optional[str] = None, icon_url: Optional[str] = None) -> None:
    """
    Globales Embed-Branding setzen (Footer-Text + Icon).

    Wird später beim Bot-Start aus Config/Dashboard/Branding-PDF aufgerufen.
    Default ist None -> kein Footer (neutral).
    """
    global _BRAND_FOOTER, _BRAND_ICON
    if footer is not None:
        _BRAND_FOOTER = footer
    if icon_url is not None:
        _BRAND_ICON = icon_url


def base_embed(
    title: Optional[str] = None,
    description: Optional[str] = None,
    *,
    color: int = COLOR_BRAND,
    timestamp: bool = True,
    footer: Optional[str] = None,
) -> discord.Embed:
    """
    Basis-Embed mit einheitlichem Stil (Farbe + optional Timestamp + Footer).

    Args:
        title: Embed-Titel.
        description: Embed-Beschreibung.
        color: Farbe (Default = Brand-Farbe; semantische Helfer setzen sie).
        timestamp: aktuellen Zeitstempel setzen (Default True).
        footer: Footer-Text; None -> globales Branding-Footer (falls gesetzt).

    Returns:
        Konfiguriertes discord.Embed.
    """
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now() if timestamp else None,
    )
    foot = footer if footer is not None else _BRAND_FOOTER
    if foot:
        embed.set_footer(text=foot, icon_url=_BRAND_ICON or discord.utils.MISSING)
    return embed


def success_embed(title: Optional[str] = None, description: Optional[str] = None,
                  **kwargs) -> discord.Embed:
    """Erfolgs-Embed (grün)."""
    kwargs.setdefault("color", COLOR_SUCCESS)
    return base_embed(title, description, **kwargs)


def error_embed(title: Optional[str] = None, description: Optional[str] = None,
                **kwargs) -> discord.Embed:
    """Fehler-Embed (rot)."""
    kwargs.setdefault("color", COLOR_ERROR)
    return base_embed(title, description, **kwargs)


def warning_embed(title: Optional[str] = None, description: Optional[str] = None,
                  **kwargs) -> discord.Embed:
    """Warn-Embed (orange)."""
    kwargs.setdefault("color", COLOR_WARNING)
    return base_embed(title, description, **kwargs)


def info_embed(title: Optional[str] = None, description: Optional[str] = None,
               **kwargs) -> discord.Embed:
    """Info-Embed (Blurple)."""
    kwargs.setdefault("color", COLOR_INFO)
    return base_embed(title, description, **kwargs)


def neutral_embed(title: Optional[str] = None, description: Optional[str] = None,
                  **kwargs) -> discord.Embed:
    """Neutrales Embed (grau)."""
    kwargs.setdefault("color", COLOR_NEUTRAL)
    return base_embed(title, description, **kwargs)

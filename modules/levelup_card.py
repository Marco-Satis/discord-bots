"""
Level-Up-Card-Renderer (Pillow)

Erzeugt ein Banner-PNG mit Wallpaper-Hintergrund, rundem Avatar, Username,
Level und XP-Fortschrittsbalken fuer Level-Up-Benachrichtigungen.

Reine synchrone Funktion — im Cog via ``asyncio.to_thread`` aufrufen,
damit der Event-Loop nicht blockiert (Pillow ist blocking).
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from utils.logger import get_logger

logger = get_logger("levelup_card")

# Karten-Dimensionen (Banner-Format)
CARD_W, CARD_H = 900, 280

# Font-Kandidaten — Linux-Server zuerst, dann Windows (lokale Vorschau),
# Fallback auf den Pillow-Bitmap-Default.
_FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
_FONT_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """TrueType-Font in gewuenschter Groesse laden, Fallback auf Default."""
    for p in (_FONT_BOLD if bold else _FONT_REG):
        try:
            if Path(p).exists():
                return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001 — Font-Probleme nie fatal
            continue
    return ImageFont.load_default()


def _hex_to_rgb(h: str, default: tuple[int, int, int] = (241, 196, 15)) -> tuple[int, int, int]:
    """Hex-Farbe (#RRGGBB) zu RGB-Tupel, bei Fehler Default."""
    try:
        h = h.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:  # noqa: BLE001
        return default


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Bild auf w x h zuschneiden (cover — fuellt komplett, Mitte sichtbar)."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = round(iw * scale), round(ih * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def render_levelup_card(
    bg_path: str | Path,
    avatar_bytes: bytes | None,
    username: str,
    level: int,
    xp_in_level: int = 0,
    xp_for_next: int = 100,
    accent: str = "#f1c40f",
    header: str = "LEVEL UP",
) -> bytes:
    """
    Karte rendern und als PNG-Bytes zurueckgeben.

    Ein gemeinsames Template fuer Level-Up- UND Rang-Karte (1-Admin-Template,
    per-Guild BG/Akzent). Nur die Kopfzeile (`header`) unterscheidet sich
    ("LEVEL UP" vs. z.B. "RANG #3").

    Args:
        bg_path: Pfad zum Hintergrund-Bild
        avatar_bytes: Avatar als Bytes (PNG/JPG) oder None
        username: Anzeigename
        level: Aktuelles Level
        xp_in_level: XP im aktuellen Level
        xp_for_next: XP fuer das naechste Level
        accent: Akzentfarbe als Hex (#RRGGBB)
        header: Kopfzeile (Default "LEVEL UP"; Rang-Karte z.B. "RANG #3")

    Returns:
        PNG-Bytes der fertigen Karte
    """
    acc = _hex_to_rgb(accent)

    # --- Hintergrund (cover) ---
    try:
        bg = Image.open(bg_path).convert("RGB")
        bg = _cover(bg, CARD_W, CARD_H)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"BG laden fehlgeschlagen ({bg_path}): {e}")
        bg = Image.new("RGB", (CARD_W, CARD_H), (15, 13, 20))
    card = bg.convert("RGBA")

    # --- Dunkles Overlay (links staerker) fuer Text-Kontrast ---
    ov = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for x in range(CARD_W):
        a = int(200 * (1 - x / CARD_W) + 70)
        a = max(60, min(225, a))
        od.line([(x, 0), (x, CARD_H)], fill=(8, 6, 14, a))
    card = Image.alpha_composite(card, ov)

    d = ImageDraw.Draw(card)

    # --- Akzent-Linie unten ---
    d.rectangle([0, CARD_H - 5, CARD_W, CARD_H], fill=acc + (255,))

    # --- Avatar (rund) links ---
    av_size = 180
    ax, ay = 50, (CARD_H - av_size) // 2
    if avatar_bytes:
        try:
            av = (
                Image.open(io.BytesIO(avatar_bytes))
                .convert("RGBA")
                .resize((av_size, av_size), Image.LANCZOS)
            )
            mask = Image.new("L", (av_size, av_size), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, av_size, av_size], fill=255)
            d.ellipse(
                [ax - 5, ay - 5, ax + av_size + 5, ay + av_size + 5],
                outline=acc + (255,),
                width=5,
            )
            card.paste(av, (ax, ay), mask)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Avatar laden fehlgeschlagen: {e}")

    d = ImageDraw.Draw(card)

    # --- Text-Block rechts vom Avatar ---
    tx = ax + av_size + 45

    head = header if len(header) <= 20 else header[:19] + "…"
    d.text((tx, 46), head.upper(), font=_font(26, True), fill=acc + (255,))

    name = username if len(username) <= 18 else username[:17] + "…"
    d.text((tx, 80), name, font=_font(46, True), fill=(255, 255, 255, 255))

    d.text((tx, 142), f"Level {level}", font=_font(30, True), fill=(235, 230, 245, 255))

    # --- Progress-Bar ---
    bar_x, bar_y = tx, 196
    bar_w, bar_h = CARD_W - tx - 50, 26
    radius = bar_h // 2
    d.rounded_rectangle(
        [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
        radius=radius,
        fill=(255, 255, 255, 45),
    )
    frac = 0.0
    if xp_for_next > 0:
        frac = max(0.0, min(1.0, xp_in_level / xp_for_next))
    fw = int(bar_w * frac)
    if fw >= bar_h:
        d.rounded_rectangle(
            [bar_x, bar_y, bar_x + fw, bar_y + bar_h], radius=radius, fill=acc + (255,)
        )
    elif fw > 0:
        d.rounded_rectangle(
            [bar_x, bar_y, bar_x + bar_h, bar_y + bar_h], radius=radius, fill=acc + (255,)
        )

    d.text(
        (bar_x, bar_y + bar_h + 8),
        f"{xp_in_level:,} / {xp_for_next:,} XP",
        font=_font(20, False),
        fill=(210, 205, 225, 255),
    )

    out = io.BytesIO()
    card.convert("RGB").save(out, "PNG")
    out.seek(0)
    return out.getvalue()


# --- Leaderboard-Karte (B3) ------------------------------------------------

# Leaderboard-Dimensionen
LB_W = 920
LB_HEADER_H = 130
LB_ROW_H = 82
LB_PAD = 40

# Medaillen-Farben fuer die Top-3-Raenge (Gold/Silber/Bronze)
_MEDAL_COLORS = {
    1: (255, 200, 40),
    2: (200, 200, 210),
    3: (205, 127, 50),
}


def render_leaderboard_card(
    rows: list[dict],
    bg_path: str | Path | None,
    accent: str = "#f1c40f",
    title: str = "LEADERBOARD",
    subtitle: str | None = None,
) -> bytes:
    """
    Leaderboard als PNG rendern (Wallpaper-BG + Rang-Liste, Arcane-Stil).

    Reine synchrone Funktion — im Cog via ``asyncio.to_thread`` aufrufen.

    Args:
        rows: Liste von Dicts je Spieler, je: rank (int), name (str),
              level (int), xp (int), optional avatar (bytes|None).
        bg_path: Hintergrund-Bild oder None (dann Solid-Fallback).
        accent: Akzentfarbe als Hex (#RRGGBB).
        title: Kopfzeile (Default "LEADERBOARD").
        subtitle: Optionale Unterzeile (z.B. Guild-Name + Spielerzahl).

    Returns:
        PNG-Bytes der fertigen Leaderboard-Karte.
    """
    acc = _hex_to_rgb(accent)
    n = max(1, len(rows))
    height = LB_HEADER_H + n * LB_ROW_H + 24

    # --- Hintergrund (cover) ---
    try:
        if bg_path is None:
            raise ValueError("kein BG")
        bg = Image.open(bg_path).convert("RGB")
        bg = _cover(bg, LB_W, height)
    except Exception as e:  # noqa: BLE001 — BG nie fatal, Solid-Fallback
        if bg_path is not None:
            logger.warning(f"Leaderboard-BG laden fehlgeschlagen ({bg_path}): {e}")
        bg = Image.new("RGB", (LB_W, height), (15, 13, 20))
    card = bg.convert("RGBA")

    # --- Dunkles Overlay (gleichmaessig) fuer Text-Kontrast ---
    ov = Image.new("RGBA", (LB_W, height), (8, 6, 14, 180))
    card = Image.alpha_composite(card, ov)
    d = ImageDraw.Draw(card)

    # --- Kopf ---
    head = title if len(title) <= 24 else title[:23] + "…"
    d.text((LB_PAD, 34), head.upper(), font=_font(42, True), fill=acc + (255,))
    if subtitle:
        sub = subtitle if len(subtitle) <= 48 else subtitle[:47] + "…"
        d.text((LB_PAD, 88), sub, font=_font(22, False), fill=(210, 205, 225, 255))
    d.line(
        [(LB_PAD, LB_HEADER_H - 6), (LB_W - LB_PAD, LB_HEADER_H - 6)],
        fill=acc + (160,), width=2,
    )

    av_size = 54
    name_x = LB_PAD + 70 + av_size + 18

    for i, row in enumerate(rows):
        ry = LB_HEADER_H + i * LB_ROW_H
        cy = ry + LB_ROW_H // 2
        rank = int(row.get("rank", i + 1))

        # Rang (Top-3 in Medaillen-Farbe, sonst hell)
        rank_color = _MEDAL_COLORS.get(rank, (235, 230, 245)) + (255,)
        rank_font = _font(32, True)
        rank_txt = f"#{rank}"
        rw = d.textlength(rank_txt, font=rank_font)
        d.text(
            (LB_PAD + 34 - rw / 2, cy - 18), rank_txt,
            font=rank_font, fill=rank_color,
        )

        # Avatar (rund) — optional
        avatar = row.get("avatar")
        ax = LB_PAD + 70
        ay = cy - av_size // 2
        if avatar:
            try:
                av = (
                    Image.open(io.BytesIO(avatar))
                    .convert("RGBA")
                    .resize((av_size, av_size), Image.LANCZOS)
                )
                mask = Image.new("L", (av_size, av_size), 0)
                ImageDraw.Draw(mask).ellipse([0, 0, av_size, av_size], fill=255)
                card.paste(av, (ax, ay), mask)
                d = ImageDraw.Draw(card)
            except Exception as e:  # noqa: BLE001 — Avatar nie fatal
                logger.debug(f"Leaderboard-Avatar fehlgeschlagen: {e}")

        # Name + Sub-Zeile (Level + XP)
        raw_name = str(row.get("name", "?"))
        name = raw_name if len(raw_name) <= 22 else raw_name[:21] + "…"
        d.text((name_x, ry + 16), name, font=_font(28, True), fill=(255, 255, 255, 255))
        level = int(row.get("level", 0))
        xp = int(row.get("xp", 0))
        d.text(
            (name_x, ry + 48),
            f"Level {level} · {xp:,} XP",
            font=_font(19, False), fill=(195, 190, 212, 255),
        )

        # Level rechtsbuendig gross
        lvl_txt = f"Lvl {level}"
        lvl_font = _font(30, True)
        lw = d.textlength(lvl_txt, font=lvl_font)
        d.text(
            (LB_W - LB_PAD - lw, cy - 18), lvl_txt,
            font=lvl_font, fill=acc + (255,),
        )

        # Zeilen-Trenner (dezent)
        if i < len(rows) - 1:
            sy = ry + LB_ROW_H
            d.line([(LB_PAD, sy), (LB_W - LB_PAD, sy)], fill=(255, 255, 255, 22), width=1)

    # --- Akzent-Linie unten ---
    d.rectangle([0, height - 5, LB_W, height], fill=acc + (255,))

    out = io.BytesIO()
    card.convert("RGB").save(out, "PNG")
    out.seek(0)
    return out.getvalue()

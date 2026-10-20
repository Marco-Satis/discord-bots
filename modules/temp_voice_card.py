"""
Temp-Voice-Kopfkarte — Pillow-Renderer (Prismatische Neon-Lobby, Final-Palette).

Erzeugt ein PNG-Banner fuer das Temp-Voice-Panel mit Kanalinfo, Status-Chips
und Squad-Reihe (bis zu 12 Mitglieder in zwei Reihen).

Reine synchrone Funktion — im Cog via ``asyncio.to_thread`` aufrufen.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from utils.logger import get_logger

logger = get_logger(__name__)

# ── Dateipfade ──────────────────────────────────────────────────────────────────
_REPO   = Path(__file__).resolve().parents[1]
_STATIC = _REPO / "web" / "static"
_BG1    = _STATIC / "temp_voice" / "card_bg_1.png"
_BG2    = _STATIC / "temp_voice" / "card_bg_2.png"
_SG     = _STATIC / "fonts" / "spacegrotesk" / "SpaceGrotesk-Variable.ttf"
_IBM_M  = _STATIC / "fonts" / "ibmplexmono" / "IBMPlexMono-Medium.ttf"
_IBM_S  = _STATIC / "fonts" / "ibmplexmono" / "IBMPlexMono-SemiBold.ttf"
_DV     = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
_DV_B   = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

# ── Karten-Abmessungen (2×-Aufloesung) ─────────────────────────────────────────
CARD_W  = 928
CARD_H1 = 500   # 1 Zeile (1–6 Slots)
CARD_H2 = 660   # 2 Zeilen (7–12 Slots)

# ── Farbpalette ─────────────────────────────────────────────────────────────────
C_TEXT      = (244, 241, 255)  # #f4f1ff
C_WHITE     = (255, 255, 255)
C_CYAN      = (83,  231, 242)  # #53e7f2
C_VIOLET    = (184, 77,  255)  # #b84dff
C_MAGENTA   = (255, 63,  164)  # #ff3fa4
C_LIVE      = (255, 101, 181)  # #ff65b5

C_HUB_LBL   = (222, 216, 243)  # #ded8f3
C_KONTEXT   = (187, 180, 208)  # #bbb4d0
C_CHIP_BG   = (23,  21,  38)   # #171526
C_CHIP_BDR  = (110, 102, 135)  # #6e6687
C_CHIP_BDR2 = (89,  108, 121)  # #596c79
C_LED_BDR   = (191, 255, 255)  # #bfffff
C_DIV_LINE  = (85,  78,  106)  # #554e6a

C_SQUAD_HDR = (216, 210, 232)  # #d8d2e8
C_SQUAD_ST  = (143, 239, 244)  # #8feff4
C_TREN      = (73,  67,  89)   # #494359

C_AV_RING   = (143, 136, 160)  # #8f88a0
C_AV_FREI_BG  = (23,  21,  34)   # #171522
C_AV_FREI_BDR = (119, 112, 135)  # #777087
C_AV_FREI_TXT = (170, 163, 185)  # #aaa3b9
C_SLOT_META = (189, 182, 205)  # #bdb6cd
C_OWN_BG    = (18,  48,  57)   # #123039
C_OWN_TXT   = (201, 255, 255)  # #c9ffff
C_FREI_NAME = (199, 193, 210)  # #c7c1d2
C_FREI_META = (143, 136, 157)  # #8f889d
C_SEG_INA   = (84,  79,  101)  # #544f65

# Avatar-Initialen-Farbpalette (deterministisch per Name-Hash)
_AV_PALETTE = [
    (143, 54,  200),  # Violett
    (49,  90,  150),  # Blau
    (155, 50,  106),  # Pink
    (60,  120, 80),   # Gruen
    (160, 80,  40),   # Orange
    (50,  110, 130),  # Teal
    (120, 60,  90),   # Dunkelpink
    (70,  80,  160),  # Indigo
]

# Layout-Konstanten (2×)
_PAD_L        = 44    # links
_PAD_R        = 36    # rechts (Abstand Kartenrand zu Chips)
_HUB_Y        = 38    # hub-label top
_KANAL_Y      = 84    # kanalname top
_KONTEXT_Y    = 156   # kanal-kontext top
_CHIP_TOP     = 40    # statusgruppe top
_CHIP_H       = 64    # chip hoehe
_CHIP_GAP     = 14    # abstand zwischen chips
_SQUAD_Y      = 210   # squad-kopf top
_TREN_Y       = 238   # trennlinie top
_GRID_TOP     = 264   # squad-grid top
_GRID_L       = 40    # grid links
_GRID_R       = 888   # grid rechts
_GRID_W       = _GRID_R - _GRID_L   # 848
_COL_GAP      = 14    # abstand zwischen spalten
_AV_D         = 76    # avatar-durchmesser
_AV_R         = 38    # avatar-radius
_ROW_H        = 196   # zeilenhoehe im squad-grid

# Laufweiten (letter-spacing aus konzept_1.html, dort 1×, hier 2×)
_TR_HUB       = 2.9
_TR_KANAL     = -2.5
_TR_KONTEXT   = 1.6
_TR_STATUS    = 2.2
_TR_ZEIT      = 1.2
_TR_BEL       = 1.0
_TR_SQUAD     = 2.5
_TR_SQUAD_ST  = 1.5
_TR_OWNER     = 1.1
_TR_BEREIT    = 0.9


# ── Datenmodell ─────────────────────────────────────────────────────────────────
@dataclass
class KartenSlot:
    """Datensatz fuer einen belegten Squad-Platz."""

    name: str               # Anzeigename (darf Emoji/Sonderzeichen enthalten)
    sekunden: int           # Verweildauer im Kanal
    owner: bool = False
    avatar: bytes | None = None   # PNG/JPG/WEBP-Bytes; None → Initialen-Kreis


# ── Font-Hilfsfunktionen ─────────────────────────────────────────────────────────
def _sg(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    """Space Grotesk Variable mit gesetztem Weight."""
    try:
        f = ImageFont.truetype(str(_SG), size)
        f.set_variation_by_axes([weight])
        return f
    except Exception:
        return _dv(size, bold=(weight >= 600))


def _ibm(size: int, semibold: bool = False) -> ImageFont.FreeTypeFont:
    """IBM Plex Mono Medium oder SemiBold."""
    path = _IBM_S if semibold else _IBM_M
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return _dv(size, bold=semibold)


def _dv(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """DejaVu Sans als Fallback-Font."""
    path = _DV_B if bold else _DV
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default()


# ── Text-Hilfsfunktionen ─────────────────────────────────────────────────────────
# Space Grotesk deckt Basic Latin + Latin-1 Supplement + Latin Extended-A
# zuverlässig ab; IBM Plex Mono ähnlich. Zeichen darüber → DejaVu-Fallback.
_SG_EXTRA = frozenset("·—–…×÷±€£¥¢°©®™§¶†‡•‰")


def _sg_has(ch: str) -> bool:
    """True wenn Space Grotesk / IBM Plex Mono das Zeichen enthalten."""
    cp = ord(ch)
    if cp <= 0x017F:
        return True
    return ch in _SG_EXTRA


def _draw_text_safe(
    d: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    main_font: ImageFont.FreeTypeFont,
    fallback_font: ImageFont.FreeTypeFont,
    fill: tuple,
    track: float = 0.0,
) -> int:
    """
    Text mit Font-Fallback zeichnen (Zeichen außerhalb des Haupt-Font-Zeichensatzes
    werden mit DejaVu gerendert). Gibt die gezeichnete Breite in Pixeln zurueck.

    track = Laufweite in px wie CSS letter-spacing (ohne Abstand nach dem letzten
    Zeichen). Positionen kommen aus Praefix-Laengen, damit Kerning erhalten bleibt.
    """
    i = 0
    cur_x = float(x)
    while i < len(text):
        use_main = _sg_has(text[i]) or text[i] == " "
        j = i + 1
        while j < len(text) and (_sg_has(text[j]) or text[j] == " ") == use_main:
            j += 1
        seg = text[i:j]
        font = main_font if use_main else fallback_font
        if track:
            for k, ch in enumerate(seg):
                d.text((round(cur_x + font.getlength(seg[:k]) + k * track), y),
                       ch, font=font, fill=fill)
            cur_x += font.getlength(seg) + len(seg) * track
        else:
            d.text((round(cur_x), y), seg, font=font, fill=fill)
            cur_x += font.getlength(seg)
        i = j
    if track and text:
        cur_x -= track
    return round(cur_x - x)


def _text_w(text: str, main_font: ImageFont.FreeTypeFont,
             fallback_font: ImageFont.FreeTypeFont | None = None,
             track: float = 0.0) -> int:
    """Textbreite mit optionalem Fallback-Font und Laufweite."""
    extra = max(0, len(text) - 1) * track
    if fallback_font is None:
        return round(main_font.getlength(text) + extra)
    total = 0.0
    for ch in text:
        if _sg_has(ch) or ch == " ":
            total += main_font.getlength(ch)
        else:
            total += fallback_font.getlength(ch)
    return round(total + extra)


def _truncate(text: str, max_w: int,
               main_font: ImageFont.FreeTypeFont,
               fallback_font: ImageFont.FreeTypeFont,
               track: float = 0.0) -> str:
    """Text auf max_w Pixel kuerzen (…-Ellipsis); niemals Exception."""
    if _text_w(text, main_font, fallback_font, track) <= max_w:
        return text
    ell = "…"
    ew = round(main_font.getlength(ell) + track)
    s = text
    while s:
        if _text_w(s, main_font, fallback_font, track) + ew <= max_w:
            return s + ell
        s = s[:-1]
    return ell


# ── Zeitformatierung ─────────────────────────────────────────────────────────────
def _fmt_dur(sek: int) -> str:
    """Verweildauer kurz formatieren: <1m | 56m | 1h 42m | 1d 3h."""
    if sek < 60:
        return "<1m"
    m = sek // 60
    h = m // 60
    d = h // 24
    if d >= 1:
        rh = h % 24
        return f"{d}d {rh}h" if rh else f"{d}d"
    if h >= 1:
        rm = m % 60
        return f"{h}h {rm}m" if rm else f"{h}h"
    return f"{m}m"


def _fmt_laufzeit(sek: int) -> str:
    """Laufzeit fuer LIVE-Chip formatieren."""
    return _fmt_dur(sek)


# ── Avatar-Hilfsfunktionen ───────────────────────────────────────────────────────
def _av_bg_color(name: str) -> tuple:
    """Deterministische Hintergrundfarbe per Name-Hash."""
    idx = int.from_bytes(name.encode()[:8], "big") % len(_AV_PALETTE)
    return _AV_PALETTE[idx]


def _make_av_circle(
    av_bytes: bytes | None,
    size: int,
    bg_col: tuple,
    init_char: str,
) -> Image.Image:
    """
    Runden Avatar-Kreis (RGBA, size×size) erstellen.

    Wird mit 4×-Supersampling gezeichnet und auf Zielgroesse skaliert,
    um sauberes Antialiasing zu erhalten.
    Ungueltige av_bytes → still auf Initialen-Kreis zurueckfallen.
    """
    SS = 4
    big = size * SS
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d_big = ImageDraw.Draw(canvas)

    # Hintergrundkreis
    d_big.ellipse([0, 0, big - 1, big - 1], fill=bg_col + (255,))

    drew_avatar = False
    if av_bytes:
        try:
            av = (
                Image.open(io.BytesIO(av_bytes))
                .convert("RGBA")
                .resize((big, big), Image.LANCZOS)
            )
            mask = Image.new("L", (big, big), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, big - 1, big - 1], fill=255)
            canvas.paste(av, (0, 0), mask)
            drew_avatar = True
        except Exception as e:
            logger.debug(f"Avatar-Bytes ungueltig, Fallback auf Initiale: {e}")

    if not drew_avatar:
        # Initiale mittig
        init_size = max(19, int(size * 0.44 * SS))
        try:
            f_init = ImageFont.truetype(str(_SG), init_size)
            f_init.set_variation_by_axes([700])
        except Exception:
            f_init = _dv(init_size, bold=True)
        ch = init_char[:1].upper() if init_char else "?"
        bbox = f_init.getbbox(ch)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (big - tw) // 2 - bbox[0]
        ty = (big - th) // 2 - bbox[1]
        d_big.text((tx, ty), ch, font=f_init, fill=(255, 255, 255, 255))

    return canvas.resize((size, size), Image.LANCZOS)


def _draw_dashed_circle(
    d: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    r: int,
    color: tuple,
    width: int = 2,
    dash_px: int = 12,
    gap_px: int = 8,
) -> None:
    """Gestrichelten Kreis auf dem Draw-Objekt zeichnen."""
    circ = 2 * math.pi * r
    if circ <= 0:
        return
    total = dash_px + gap_px
    n = max(1, int(circ / total))
    for i in range(n):
        t0 = (i * total) / circ
        t1 = min((i * total + dash_px) / circ, 1.0)
        a0 = t0 * 360 - 90
        a1 = t1 * 360 - 90
        if a1 > a0:
            d.arc(
                [cx - r, cy - r, cx + r, cy + r],
                start=a0,
                end=a1,
                fill=color,
                width=width,
            )


# ── Chip-Rendering ───────────────────────────────────────────────────────────────
def _chip_status_w(
    privat: bool,
    laufzeit_s: int,
    ibm_s20: ImageFont.FreeTypeFont,
) -> int:
    """Breite des Status-Chips berechnen."""
    pub_txt = "PRIVATE" if privat else "PUBLIC"
    time_txt = f"LIVE {_fmt_laufzeit(laufzeit_s)}"
    # Inhalt: 18 + LED14 + 14 + pub_w + 16 + div2 + 16 + time_w + 18
    w = (
        18
        + 14  # LED
        + 14  # LED margin
        + _text_w(pub_txt, ibm_s20, track=_TR_STATUS)
        + 16  # nach PUBLIC
        + 2   # Trennlinie
        + 16  # vor LIVE
        + _text_w(time_txt, ibm_s20, track=_TR_ZEIT)
        + 18  # rechtes Padding
    )
    return max(184, w)


def _chip_bel_w(
    n: int,
    limit: int,
    ibm_s20: ImageFont.FreeTypeFont,
) -> int:
    """Breite des Belegungschips berechnen."""
    bel_txt = f"{n} / ∞" if limit <= 0 else f"{n} / {limit}"
    w = 16 + 20 + 16 + _text_w(bel_txt, ibm_s20, _dv(20), _TR_BEL) + 16
    return max(134, w)


def _draw_status_chip(
    d: ImageDraw.ImageDraw,
    x: int,
    privat: bool,
    laufzeit_s: int,
    chip_w: int,
    ibm_s20: ImageFont.FreeTypeFont,
) -> None:
    """Status-Chip (PUBLIC/PRIVATE · LIVE …) zeichnen."""
    y0, y1 = _CHIP_TOP, _CHIP_TOP + _CHIP_H
    cy = (y0 + y1) // 2

    # Hintergrund
    d.rounded_rectangle([x, y0, x + chip_w, y1], radius=10, fill=C_CHIP_BG)
    d.rounded_rectangle([x, y0, x + chip_w, y1], radius=10,
                         outline=C_CHIP_BDR, width=2)

    # LED
    led_cx = x + 18 + 7
    led_cy = cy
    d.ellipse(
        [led_cx - 7, led_cy - 7, led_cx + 7, led_cy + 7],
        fill=(C_MAGENTA if privat else C_CYAN),
    )
    d.ellipse(
        [led_cx - 9, led_cy - 9, led_cx + 9, led_cy + 9],
        outline=C_LED_BDR,
        width=3,
    )

    # Statustext
    txt_x = x + 18 + 14 + 14
    pub_txt = "PRIVATE" if privat else "PUBLIC"
    pub_w = _draw_text_safe(d, txt_x, cy - 10, pub_txt, ibm_s20, ibm_s20, C_TEXT, _TR_STATUS)

    # Trennlinie
    div_x = txt_x + pub_w + 16
    d.rectangle([div_x, cy - 14, div_x + 2, cy + 14], fill=C_DIV_LINE)

    # LIVE-Zeit
    time_txt = f"LIVE {_fmt_laufzeit(laufzeit_s)}"
    _draw_text_safe(d, div_x + 2 + 16, cy - 10, time_txt, ibm_s20, ibm_s20, C_LIVE, _TR_ZEIT)


def _draw_bel_chip(
    d: ImageDraw.ImageDraw,
    x: int,
    n: int,
    limit: int,
    chip_w: int,
    ibm_s20: ImageFont.FreeTypeFont,
) -> None:
    """Belegungschip (Balken + n / limit) zeichnen."""
    y0, y1 = _CHIP_TOP, _CHIP_TOP + _CHIP_H
    cy = (y0 + y1) // 2

    d.rounded_rectangle([x, y0, x + chip_w, y1], radius=10, fill=C_CHIP_BG)
    d.rounded_rectangle([x, y0, x + chip_w, y1], radius=10,
                         outline=C_CHIP_BDR2, width=2)

    # Signalbalken (3 Stufen)
    if limit > 0:
        ratio = n / limit
    else:
        # Bei unbegrenzt: 1 Balken ab 1, 2 ab 4, 3 ab 8
        ratio = 0.0 if n == 0 else (0.34 if n < 4 else (0.67 if n < 8 else 1.0))
    filled = 1 if ratio > 0 else 0
    if ratio >= 0.34:
        filled = 1
    if ratio >= 0.67:
        filled = 2
    if ratio >= 1.0:
        filled = 3

    bar_bottom = cy + 13
    bar_heights = [10, 18, 26]
    bar_x = x + 16
    for idx, bh in enumerate(bar_heights):
        col = C_CYAN if idx < filled else C_SEG_INA
        by_top = bar_bottom - bh
        d.rectangle([bar_x, by_top, bar_x + 4, bar_bottom], fill=col)
        bar_x += 8  # 4px bar + 4px gap

    # Belegungstext rechtsbuendig
    bel_txt = f"{n} / ∞" if limit <= 0 else f"{n} / {limit}"
    dv20 = _dv(20)
    bel_w = _text_w(bel_txt, ibm_s20, dv20, _TR_BEL)
    _draw_text_safe(d, x + chip_w - 16 - bel_w, cy - 10, bel_txt, ibm_s20, dv20, C_WHITE, _TR_BEL)


# ── Squad-Slot ───────────────────────────────────────────────────────────────────
def _draw_slot(
    img: Image.Image,
    d: ImageDraw.ImageDraw,
    cx: int,
    row_top_y: int,
    slot: KartenSlot | None,
    col_w: int,
    sg20: ImageFont.FreeTypeFont,
    ibm19: ImageFont.FreeTypeFont,
    ibm17: ImageFont.FreeTypeFont,
    dv20: ImageFont.FreeTypeFont,
    dv19: ImageFont.FreeTypeFont,
    overflow_n: int = 0,
) -> None:
    """
    Einzelnen Squad-Slot zeichnen.

    slot=None → freier Platz.
    overflow_n > 0 → Overflow-Kachel "+N weitere" (slot muss None sein).
    """
    av_top = row_top_y
    av_cy = av_top + _AV_R

    if slot is None and overflow_n <= 0:
        # Freier Platz: gestrichelter Kreis, "+", "frei"/"BEREIT"
        _draw_dashed_circle(d, cx, av_cy, _AV_R, C_AV_FREI_BDR, width=2)
        d.ellipse(
            [cx - _AV_R + 2, av_cy - _AV_R + 2, cx + _AV_R - 2, av_cy + _AV_R - 2],
            fill=C_AV_FREI_BG,
        )
        # "+" mittig, 30 px wie .avatar-frei im Konzept
        d.text((cx, av_cy), "+", font=_sg(30, 500), fill=C_AV_FREI_TXT, anchor="mm")
        # "frei"
        name_y = av_top + _AV_D + 14
        fn_w = _text_w("frei", sg20, dv20)
        _draw_text_safe(d, cx - fn_w // 2, name_y, "frei", sg20, dv20, C_FREI_NAME)
        meta_y = name_y + 22 + 6
        bw = _text_w("BEREIT", ibm19, dv19, _TR_BEREIT)
        _draw_text_safe(d, cx - bw // 2, meta_y, "BEREIT", ibm19, dv19, C_FREI_META, _TR_BEREIT)
        return

    if slot is None and overflow_n > 0:
        # Overflow-Kachel: dunkler Kreis + "+N weitere"
        d.ellipse(
            [cx - _AV_R, av_cy - _AV_R, cx + _AV_R, av_cy + _AV_R],
            fill=C_AV_FREI_BG,
        )
        d.ellipse(
            [cx - _AV_R, av_cy - _AV_R, cx + _AV_R, av_cy + _AV_R],
            outline=C_AV_FREI_BDR,
            width=2,
        )
        ov_txt = f"+{overflow_n}"
        ov_w = round(ibm17.getlength(ov_txt))
        d.text((cx - ov_w // 2, av_cy - 10), ov_txt, font=ibm17, fill=C_AV_FREI_TXT)
        name_y = av_top + _AV_D + 14
        wt = _text_w("weitere", sg20, dv20)
        _draw_text_safe(d, cx - wt // 2, name_y, "weitere", sg20, dv20, C_FREI_NAME)
        return

    # Belegter Platz
    bg_col = _av_bg_color(slot.name)
    init_ch = slot.name[:1] if slot.name else "?"
    av_img = _make_av_circle(slot.avatar, _AV_D, bg_col, init_ch)
    av_x = cx - _AV_R
    av_y = av_cy - _AV_R
    img.paste(av_img, (av_x, av_y), av_img)

    # Ring(e) auf dem Karten-Image zeichnen
    if slot.owner:
        # Innen: Cyan, Außen: Violett
        d.ellipse(
            [av_x - 3, av_y - 3, av_x + _AV_D + 3, av_y + _AV_D + 3],
            outline=C_CYAN,
            width=4,
        )
        d.ellipse(
            [av_x - 10, av_y - 10, av_x + _AV_D + 10, av_y + _AV_D + 10],
            outline=C_VIOLET,
            width=4,
        )
    else:
        d.ellipse(
            [av_x - 2, av_y - 2, av_x + _AV_D + 2, av_y + _AV_D + 2],
            outline=C_AV_RING,
            width=2,
        )

    # Name (gekuerzt auf Spaltenbreite)
    name_y = av_top + _AV_D + 14
    raw_name = _truncate(slot.name, col_w - 4, sg20, dv20)
    name_w = _text_w(raw_name, sg20, dv20)
    _draw_text_safe(d, cx - name_w // 2, name_y, raw_name, sg20, dv20, C_TEXT)

    # Verweildauer
    meta_y = name_y + 22 + 6
    meta_txt = _fmt_dur(slot.sekunden)
    meta_w = _text_w(meta_txt, ibm19, dv19)
    _draw_text_safe(d, cx - meta_w // 2, meta_y, meta_txt, ibm19, dv19, C_SLOT_META)

    # Owner-Badge
    if slot.owner:
        badge_y = meta_y + 21 + 10
        badge_w = 100
        badge_h = 30
        badge_x = cx - badge_w // 2
        d.rounded_rectangle(
            [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
            radius=6,
            fill=C_OWN_BG,
        )
        d.rounded_rectangle(
            [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
            radius=6,
            outline=C_OWN_TXT,
            width=2,
        )
        badge_txt = "OWNER"
        ibm_badge = _ibm(19, semibold=True)
        btw = _text_w(badge_txt, ibm_badge, track=_TR_OWNER)
        _draw_text_safe(d, cx - btw // 2, badge_y + (badge_h - 19) // 2, badge_txt,
                        ibm_badge, ibm_badge, C_OWN_TXT, _TR_OWNER)


# ── Hauptfunktion ────────────────────────────────────────────────────────────────
def render_temp_voice_card(
    *,
    hub: str,
    kanalname: str,
    privat: bool,
    laufzeit_s: int,
    mitglieder: list[KartenSlot],
    limit: int,
) -> bytes:
    """
    Temp-Voice-Kopfkarte rendern und als PNG-Bytes zurueckgeben.

    Args:
        hub:         Hub-Bezeichnung (z.B. "GAMING"), wird uppercase verwendet.
        kanalname:   Name des temporaeren Kanals.
        privat:      True fuer PRIVATE-Kanal.
        laufzeit_s:  Kanalaltein Sekunden (fuer LIVE-Chip).
        mitglieder:  Liste der aktuellen Kanalnutzer.
        limit:       Maximale Mitgliederzahl (0 = unbegrenzt).

    Returns:
        PNG-Bytes, 928 px breit, unter 1,5 MB.
    """
    n = len(mitglieder)

    # Hintergrund-Bild und Karten-Hoehe bestimmen
    use_two_rows = (limit > 6) or (limit == 0 and n > 6)
    if limit > 0:
        use_two_rows = limit > 6
    card_h = CARD_H2 if use_two_rows else CARD_H1
    bg_path = _BG2 if use_two_rows else _BG1

    try:
        card = Image.open(bg_path).convert("RGB")
        if card.size != (CARD_W, card_h):
            card = card.resize((CARD_W, card_h), Image.LANCZOS)
        card = card.convert("RGBA")
    except Exception as e:
        logger.warning(f"Hintergrund laden fehlgeschlagen: {e}")
        card = Image.new("RGBA", (CARD_W, card_h), (9, 10, 20, 255))

    d = ImageDraw.Draw(card)

    # Fonts laden (Mindest-Schriftgroesse 19 px)
    ibm_s20 = _ibm(20, semibold=True)
    ibm_m19 = _ibm(19, semibold=False)
    ibm_s19 = _ibm(19, semibold=True)
    sg_700_56 = _sg(56, 700)
    sg_600_20 = _sg(20, 600)
    dv_20 = _dv(20)
    dv_19 = _dv(19)

    # ── Chip-Breiten berechnen (vor Hub-Label, um Kollision zu pruefen) ──────────
    w_status = _chip_status_w(privat, laufzeit_s, ibm_s20)
    w_bel    = _chip_bel_w(n, limit, ibm_s20)
    chips_right = CARD_W - _PAD_R          # 892
    bel_x    = chips_right - w_bel
    stat_x   = bel_x - _CHIP_GAP - w_status
    chips_left = stat_x                    # Linksbegrenzung fuer Kanalname

    # ── Hub-Label (Marker + Text) ─────────────────────────────────────────────────
    marker_x1 = _PAD_L
    marker_x2 = marker_x1 + 34
    marker_y  = _HUB_Y + 8
    d.rectangle([marker_x1, marker_y, marker_x2, marker_y + 4], fill=C_CYAN)

    hub_text  = hub.upper()
    hub_max_w = chips_left - marker_x2 - 16 - 20
    hub_text  = _truncate(hub_text, hub_max_w, ibm_s20, dv_20, _TR_HUB)
    _draw_text_safe(d, marker_x2 + 16, _HUB_Y, hub_text, ibm_s20, dv_20, C_HUB_LBL, _TR_HUB)

    # ── Kanalname ────────────────────────────────────────────────────────────────
    kanal_max_w = chips_left - _PAD_L - 20
    kanal_trunc = _truncate(kanalname, kanal_max_w, sg_700_56, dv_20, _TR_KANAL)
    _draw_text_safe(d, _PAD_L - 2, _KANAL_Y, kanal_trunc, sg_700_56, dv_20, C_WHITE, _TR_KANAL)

    # ── Kontext-Zeile ────────────────────────────────────────────────────────────
    voll = limit > 0 and n >= limit
    kontext_txt = "TEMP-VOICE · SQUAD VOLL" if voll else "TEMP-VOICE · SQUAD-BEREIT"
    _draw_text_safe(d, _PAD_L + 2, _KONTEXT_Y, kontext_txt, ibm_m19, dv_19, C_KONTEXT, _TR_KONTEXT)

    # ── Status-Chips ─────────────────────────────────────────────────────────────
    _draw_status_chip(d, stat_x, privat, laufzeit_s, w_status, ibm_s20)
    _draw_bel_chip(d, bel_x, n, limit, w_bel, ibm_s20)

    # ── SQUAD-Kopf und Status ─────────────────────────────────────────────────────
    _draw_text_safe(d, _PAD_L, _SQUAD_Y, "SQUAD", ibm_s19, dv_19, C_SQUAD_HDR, _TR_SQUAD)

    if limit > 0:
        frei = limit - n
        if frei <= 0:
            squad_st = "VOLL"
        elif frei == 1:
            squad_st = "1 PLATZ FREI"
        else:
            squad_st = f"{frei} PLÄTZE FREI"
    else:
        squad_st = "OFFEN"

    st_w = _text_w(squad_st, ibm_m19, dv_19, _TR_SQUAD_ST)
    _draw_text_safe(d, CARD_W - _PAD_R - 6 - st_w, _SQUAD_Y, squad_st,
                    ibm_m19, dv_19, C_SQUAD_ST, _TR_SQUAD_ST)

    # ── Trennlinie ───────────────────────────────────────────────────────────────
    d.rectangle([_PAD_L, _TREN_Y, CARD_W - _PAD_R + 6, _TREN_Y + 2], fill=C_TREN)

    # ── Slot-Liste aufbauen ──────────────────────────────────────────────────────
    # Slot-Objekte (None = freier Platz, overflow_n > 0 = "+N weitere"-Kachel)
    overflow_n = 0
    if limit <= 0 or limit > 12:
        if n > 12:
            slots: list[KartenSlot | None] = list(mitglieder[:11]) + [None]
            overflow_n = n - 11
        else:
            slots = list(mitglieder)
    else:
        clamp = max(1, min(12, limit))
        slots = list(mitglieder[:clamp])
        while len(slots) < clamp:
            slots.append(None)

    # ── Spaltenbreite ─────────────────────────────────────────────────────────────
    col_w = (_GRID_W - 5 * _COL_GAP) / 6   # ≈ 129.67

    def _col_cx(col: int) -> int:
        return round(_GRID_L + col * (col_w + _COL_GAP) + col_w / 2)

    # ── Slots zeichnen ────────────────────────────────────────────────────────────
    for idx, slot in enumerate(slots):
        col  = idx % 6
        row  = idx // 6
        cx   = _col_cx(col)
        row_top = _GRID_TOP + row * _ROW_H
        ov   = overflow_n if (slot is None and idx == 11 and overflow_n > 0) else 0
        _draw_slot(
            card, d, cx, row_top, slot,
            round(col_w),
            sg_600_20, ibm_m19, ibm_s19, dv_20, dv_19,
            overflow_n=ov,
        )

    # ── PNG ausgeben ─────────────────────────────────────────────────────────────
    out = io.BytesIO()
    card.convert("RGB").save(out, "PNG", optimize=True)
    out.seek(0)
    data = out.getvalue()
    if len(data) > 1_500_000:
        logger.warning(f"Karte zu gross: {len(data)} Bytes — comprime erneut")
        out2 = io.BytesIO()
        card.convert("RGB").save(out2, "PNG", optimize=True, compress_level=9)
        out2.seek(0)
        data = out2.getvalue()
    return data

"""
Temp-Voice-Kopfkarte — Pillow-Renderer (Prismatische Neon-Lobby, Final-Palette).

Erzeugt ein PNG-Banner fuer das Temp-Voice-Panel mit Kanalinfo, Status-Chips
und Squad-Reihe (bis zu 12 Mitglieder in zwei Reihen). v2 (2026-10-20): auf
Discords Anzeigegroesse 600×350 ausgelegt, Schrift fuer halbe Groesse bemessen.

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

# ── Karten-Abmessungen ──────────────────────────────────────────────────────────
# Discord zeigt ein Media-Gallery-Bild hoechstens 600×350 CSS-px gross und
# verkleinert nur (Client-Konstanten, Recherche 2026-10-20). Die Karte ist genau
# doppelt so gross: auf Bildschirmen mit Skalierung 2 pixelgenau, sonst glatt
# halbiert statt um einen krummen Faktor verkleinert (das machte v1 unscharf).
CARD_W  = 1200
CARD_H1 = 560   # 1 Reihe (1–6 Plaetze)  -> Anzeige 600×280
CARD_H2 = 700   # 2 Reihen (7–12 Plaetze) -> Anzeige 600×350
SS      = 3     # Supersampling fuer Formen: Pillow glaettet Kreise und Konturen nicht

# ── Farbpalette ─────────────────────────────────────────────────────────────────
C_TEXT      = (244, 241, 255)  # #f4f1ff
C_WHITE     = (255, 255, 255)
C_CYAN      = (83,  231, 242)  # #53e7f2
C_VIOLET    = (184, 77,  255)  # #b84dff
C_MAGENTA   = (255, 63,  164)  # #ff3fa4
C_LIVE      = (255, 101, 181)  # #ff65b5

C_HUB_LBL   = (222, 216, 243)  # #ded8f3
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
C_AV_FREI_BDR = (130, 123, 148)  # heller als im Konzept, sonst bei halber Groesse weg
C_AV_FREI_TXT = (178, 171, 194)
C_SLOT_META = (208, 202, 224)  # Verweildauer: heller als im Konzept (Lesbarkeit)
C_OWN_BG    = (18,  48,  57)   # #123039
C_OWN_TXT   = (201, 255, 255)  # #c9ffff
C_FREI_NAME = (199, 193, 210)  # #c7c1d2
C_FREI_META = (163, 156, 180)
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

# ── Layout (Kartenpixel; Anzeige = halbe Werte) ─────────────────────────────────
# Marco 20.10. nach dem ersten Live-Blick: Titel kleiner, Kleinschrift groesser.
# Anzeige bei 600 px: Etiketten 15–16 px, Namen 20 px, Zeiten 17 px, Kanal 35 px.
_PAD_L      = 56
_PAD_R      = 48
_HUB_Y      = 52    # Hub-Label oben (Text-Oberkante)
_MARKER_W   = 44
_CHIP_TOP   = 32
_CHIP_H     = 78
_CHIP_GAP   = 16
_KANAL_Y    = 122   # unter den Chips: der Kanalname bekommt die volle Breite
_SQUAD_Y    = 222
_TREN_Y     = 270   # liegt auf den Seitenstrichen des Hintergrunds
_GRID_L     = 48
_GRID_R     = CARD_W - 48
_COL_GAP    = 16
_COLS       = 6

# Reihen: Oberkante Raster, Avatar-Durchmesser, Abstand zweier Reihen
_REIHE_1 = {"top": 298, "av": 104, "row_h": 0}
_REIHE_2 = {"top": 288, "av": 76,  "row_h": 204}

# Schriftgroessen
_FS_HUB     = 32
_FS_CHIP    = 30
_FS_KANAL   = 70
_FS_SQUAD   = 26   # Marco 20.10.: bei 32 zu wuchtig
_FS_NAME    = 40
_FS_ZEIT    = 34
_FS_BADGE   = 24
_FS_PLUS    = 50
_FS_BEREIT  = 28
_FS_OV      = 40

# Laufweiten (letter-spacing wie im Konzept, auf diese Groesse umgerechnet)
_TR_HUB       = 2.8
_TR_KANAL     = -2.2
_TR_STATUS    = 2.8
_TR_ZEIT      = 1.6
_TR_BEL       = 1.4
_TR_SQUAD     = 3.2
_TR_SQUAD_ST  = 2.0
_TR_OWNER     = 1.8
_TR_BEREIT    = 1.6


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


def _ohne_emoji(text: str) -> str:
    """Bunte Emoji (ab U+1F000) samt Joiner/Variantenwahl entfernen: keine Schrift der
    Karte kann sie zeichnen, sie kaemen als leere Kaestchen. Discord zeigt sie ja im Namen."""
    rein = "".join(ch for ch in text
                   if ord(ch) < 0x1F000 and ch not in "\u200d\ufe0f\u20e3")
    return " ".join(rein.split())


def _mitte_y(font: ImageFont.FreeTypeFont, cy: float, probe: str = "LIVE0") -> int:
    """Text-Oberkante, damit Grossbuchstaben/Ziffern vertikal mittig auf cy sitzen."""
    top, bottom = font.getbbox(probe)[1], font.getbbox(probe)[3]
    return round(cy - (top + bottom) / 2)


# ── Formen-Ebene (Supersampling) ─────────────────────────────────────────────────
class _Formen:
    """
    Formen auf einer SS-fach groesseren, transparenten Ebene zeichnen und geglaettet
    auf die Karte legen. Koordinaten in Kartenpixeln.
    """

    def __init__(self, w: int, h: int) -> None:
        self.img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    @staticmethod
    def _b(box) -> list[int]:
        return [round(v * SS) for v in box]

    def rect(self, box, fill) -> None:
        self.d.rectangle(self._b(box), fill=fill)

    def rrect(self, box, r: float, fill=None, outline=None, width: float = 0) -> None:
        self.d.rounded_rectangle(self._b(box), radius=round(r * SS), fill=fill,
                                 outline=outline, width=round(width * SS))

    def kreis(self, cx: float, cy: float, r: float, fill=None, outline=None, width: float = 0) -> None:
        self.d.ellipse(self._b([cx - r, cy - r, cx + r, cy + r]), fill=fill,
                       outline=outline, width=round(width * SS))

    def strichkreis(self, cx: float, cy: float, r: float, color, width: float = 3,
                    dash: float = 14, gap: float = 9) -> None:
        """Gestrichelter Kreis (freier Platz)."""
        umfang = 2 * math.pi * r
        n = max(1, int(umfang / (dash + gap)))
        schritt = 360 / n
        anteil = dash / (dash + gap)
        for i in range(n):
            a0 = i * schritt - 90
            self.d.arc(self._b([cx - r, cy - r, cx + r, cy + r]), start=a0,
                       end=a0 + schritt * anteil, fill=color, width=round(width * SS))

    def auf(self, card: Image.Image) -> None:
        """Verkleinert (mit vormultipliziertem Alpha, sonst dunkle Saeume) auflegen."""
        klein = self.img.convert("RGBa").resize(card.size, Image.LANCZOS).convert("RGBA")
        card.alpha_composite(klein)


# ── Chips ───────────────────────────────────────────────────────────────────────
def _status_teile(privat: bool, laufzeit_s: int) -> tuple[str, str]:
    return ("PRIVATE" if privat else "PUBLIC"), f"LIVE {_fmt_laufzeit(laufzeit_s)}"


def _bel_text(n: int, limit: int) -> str:
    return f"{n} / ∞" if limit <= 0 else f"{n} / {limit}"


def _chip_status_w(privat: bool, laufzeit_s: int, f: ImageFont.FreeTypeFont) -> int:
    """Breite des Status-Chips: Rand 22 · LED 18 · 14 · Status · 18 | 18 · LIVE · Rand 22."""
    pub, zeit = _status_teile(privat, laufzeit_s)
    return (22 + 18 + 14 + _text_w(pub, f, track=_TR_STATUS) + 18 + 2 + 18
            + _text_w(zeit, f, track=_TR_ZEIT) + 22)


def _chip_bel_w(n: int, limit: int, f: ImageFont.FreeTypeFont, fb: ImageFont.FreeTypeFont) -> int:
    """Breite des Belegungs-Chips: Rand 20 · Balken 28 · 16 · Text · Rand 20."""
    return 20 + 28 + 16 + _text_w(_bel_text(n, limit), f, fb, _TR_BEL) + 20


def _balken(n: int, limit: int) -> int:
    """Gefuellte Signalbalken (0–3)."""
    if limit > 0:
        anteil = n / limit
    else:
        # unbegrenzt: 1 Balken ab 1, 2 ab 4, 3 ab 8
        anteil = 0.0 if n == 0 else (0.34 if n < 4 else (0.67 if n < 8 else 1.0))
    if anteil >= 1.0:
        return 3
    if anteil >= 0.67:
        return 2
    return 1 if anteil > 0 else 0


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
        hub:         Hub-Bezeichnung (z.B. "GAMING · OSTFRONT HQ"), wird uppercase verwendet.
        kanalname:   Name des temporaeren Kanals.
        privat:      True fuer PRIVATE-Kanal.
        laufzeit_s:  Kanalalter in Sekunden (fuer den LIVE-Chip).
        mitglieder:  Liste der aktuellen Kanalnutzer.
        limit:       Maximale Mitgliederzahl (0 = unbegrenzt).

    Returns:
        PNG-Bytes, 1200 px breit (560 bzw. 700 hoch), unter 1,5 MB.
    """
    hub = _ohne_emoji(hub)
    kanalname = _ohne_emoji(kanalname) or "Temp-Voice"
    mitglieder = [KartenSlot(_ohne_emoji(m.name) or "?", m.sekunden, m.owner, m.avatar)
                  for m in mitglieder]
    n = len(mitglieder)
    zwei_reihen = limit > 6 if limit > 0 else n > 6
    card_h = CARD_H2 if zwei_reihen else CARD_H1
    reihe = _REIHE_2 if zwei_reihen else _REIHE_1

    try:
        card = Image.open(_BG2 if zwei_reihen else _BG1).convert("RGBA")
        if card.size != (CARD_W, card_h):
            card = card.resize((CARD_W, card_h), Image.LANCZOS)
    except Exception as e:
        logger.warning(f"Hintergrund laden fehlgeschlagen: {e}")
        card = Image.new("RGBA", (CARD_W, card_h), (9, 10, 20, 255))

    f_hub    = _ibm(_FS_HUB, semibold=True)
    f_chip   = _ibm(_FS_CHIP, semibold=True)
    f_kanal  = _sg(_FS_KANAL, 700)
    f_squad  = _ibm(_FS_SQUAD, semibold=True)
    f_squad2 = _ibm(_FS_SQUAD)
    f_name   = _sg(_FS_NAME, 600)
    f_zeit   = _ibm(_FS_ZEIT)
    f_badge  = _ibm(_FS_BADGE, semibold=True)
    f_plus   = _sg(_FS_PLUS, 500)
    f_bereit = _ibm(_FS_BEREIT)
    f_ov     = _sg(_FS_OV, 700)
    dv_gross = _dv(_FS_KANAL)
    dv_name  = _dv(_FS_NAME)
    dv_klein = _dv(_FS_CHIP)

    # ── Slots bestimmen (None = freier Platz) ───────────────────────────────────
    overflow_n = 0
    if limit <= 0 or limit > 12:
        if n > 12:
            slots: list[KartenSlot | None] = list(mitglieder[:11]) + [None]
            overflow_n = n - 11
        else:
            slots = list(mitglieder)
    else:
        slots = list(mitglieder[:limit])
        slots += [None] * (limit - len(slots))

    col_w = (_GRID_R - _GRID_L - (_COLS - 1) * _COL_GAP) / _COLS
    av_d = reihe["av"]
    av_r = av_d / 2

    def _pos(idx: int) -> tuple[float, float]:
        """Mittelpunkt des Avatars von Slot idx."""
        col, row = idx % _COLS, idx // _COLS
        cx = _GRID_L + col * (col_w + _COL_GAP) + col_w / 2
        return cx, reihe["top"] + row * reihe["row_h"] + av_r

    # ── Avatare (bereits geglaettet) ─────────────────────────────────────────────
    for idx, slot in enumerate(slots):
        if slot is None:
            continue
        cx, cy = _pos(idx)
        bild = _make_av_circle(slot.avatar, av_d, _av_bg_color(slot.name), slot.name[:1] or "?")
        card.alpha_composite(bild, (round(cx - av_r), round(cy - av_r)))

    # ── Chips: Breiten rechtsbuendig ─────────────────────────────────────────────
    w_status = _chip_status_w(privat, laufzeit_s, f_chip)
    w_bel = _chip_bel_w(n, limit, f_chip, dv_klein)
    bel_x = CARD_W - _PAD_R - w_bel
    stat_x = bel_x - _CHIP_GAP - w_status
    chip_cy = _CHIP_TOP + _CHIP_H / 2

    # ── Formen ───────────────────────────────────────────────────────────────────
    fo = _Formen(CARD_W, card_h)
    hub_cy = _HUB_Y + _FS_HUB * 0.42
    fo.rrect([_PAD_L, hub_cy - 3, _PAD_L + _MARKER_W, hub_cy + 3], 3, fill=C_CYAN)

    for x, w, rand in ((stat_x, w_status, C_CHIP_BDR), (bel_x, w_bel, C_CHIP_BDR2)):
        fo.rrect([x, _CHIP_TOP, x + w, _CHIP_TOP + _CHIP_H], 14, fill=C_CHIP_BG,
                 outline=rand, width=2)
    led_cx = stat_x + 22 + 9
    fo.kreis(led_cx, chip_cy, 8, fill=C_MAGENTA if privat else C_CYAN)
    fo.kreis(led_cx, chip_cy, 11, outline=C_LED_BDR, width=3)
    pub, zeit = _status_teile(privat, laufzeit_s)
    div_x = stat_x + 22 + 18 + 14 + _text_w(pub, f_chip, track=_TR_STATUS) + 18
    fo.rect([div_x, chip_cy - 18, div_x + 2, chip_cy + 18], fill=C_DIV_LINE)
    gefuellt = _balken(n, limit)
    bx = bel_x + 20
    for i, hoehe in enumerate((12, 22, 32)):
        fo.rrect([bx, chip_cy + 16 - hoehe, bx + 6, chip_cy + 16], 1.5,
                 fill=C_CYAN if i < gefuellt else C_SEG_INA)
        bx += 11

    fo.rect([_PAD_L, _TREN_Y, CARD_W - _PAD_R, _TREN_Y + 2], fill=C_TREN)

    for idx, slot in enumerate(slots):
        cx, cy = _pos(idx)
        if slot is None:
            if idx == 11 and overflow_n > 0:
                fo.kreis(cx, cy, av_r, fill=C_AV_FREI_BG, outline=C_AV_FREI_BDR, width=3)
            else:
                fo.kreis(cx, cy, av_r - 2, fill=C_AV_FREI_BG)
                fo.strichkreis(cx, cy, av_r, C_AV_FREI_BDR, width=3)
        elif slot.owner:
            fo.kreis(cx, cy, av_r + 5, outline=C_CYAN, width=5)
            fo.kreis(cx, cy, av_r + 13, outline=C_VIOLET, width=5)
            bw = _text_w("OWNER", f_badge, track=_TR_OWNER) + 28
            fo.rrect([cx - bw / 2, cy + av_r - 18, cx + bw / 2, cy + av_r + 18], 8,
                     fill=C_OWN_BG, outline=C_OWN_TXT, width=2)
        else:
            fo.kreis(cx, cy, av_r + 3, outline=C_AV_RING, width=3)
    fo.auf(card)

    # ── Text ─────────────────────────────────────────────────────────────────────
    d = ImageDraw.Draw(card)

    def _mittig(text: str, cx: float, y: float, font, fb, fill, track: float = 0.0) -> None:
        w = _text_w(text, font, fb, track)
        _draw_text_safe(d, round(cx - w / 2), round(y), text, font, fb, fill, track)

    hub_x = _PAD_L + _MARKER_W + 18
    hub_text = _truncate(hub.upper(), stat_x - hub_x - 24, f_hub, dv_klein, _TR_HUB)
    _draw_text_safe(d, hub_x, _mitte_y(f_hub, hub_cy), hub_text, f_hub, dv_klein,
                    C_HUB_LBL, _TR_HUB)

    chip_ty = _mitte_y(f_chip, chip_cy)
    _draw_text_safe(d, stat_x + 22 + 18 + 14, chip_ty, pub, f_chip, f_chip, C_TEXT, _TR_STATUS)
    _draw_text_safe(d, div_x + 2 + 18, chip_ty, zeit, f_chip, f_chip, C_LIVE, _TR_ZEIT)
    bel = _bel_text(n, limit)
    _draw_text_safe(d, bel_x + w_bel - 20 - _text_w(bel, f_chip, dv_klein, _TR_BEL), chip_ty,
                    bel, f_chip, dv_klein, C_WHITE, _TR_BEL)

    kanal = _truncate(kanalname, CARD_W - _PAD_L - _PAD_R, f_kanal, dv_gross, _TR_KANAL)
    _draw_text_safe(d, _PAD_L - 3, _KANAL_Y, kanal, f_kanal, dv_gross, C_WHITE, _TR_KANAL)

    _draw_text_safe(d, _PAD_L, _SQUAD_Y, "SQUAD", f_squad, dv_klein, C_SQUAD_HDR, _TR_SQUAD)
    if limit > 0:
        frei = limit - n
        squad_st = "VOLL" if frei <= 0 else ("1 PLATZ FREI" if frei == 1 else f"{frei} PLÄTZE FREI")
    else:
        squad_st = "OFFEN"
    st_w = _text_w(squad_st, f_squad2, dv_klein, _TR_SQUAD_ST)
    _draw_text_safe(d, CARD_W - _PAD_R - st_w, _SQUAD_Y, squad_st, f_squad2, dv_klein,
                    C_SQUAD_ST, _TR_SQUAD_ST)

    name_abstand = 20 if zwei_reihen else 26
    for idx, slot in enumerate(slots):
        cx, cy = _pos(idx)
        name_y = cy + av_r + name_abstand
        zeit_y = name_y + _FS_NAME + (4 if zwei_reihen else 8)
        if slot is None:
            if idx == 11 and overflow_n > 0:
                d.text((cx, cy), f"+{overflow_n}", font=f_ov, fill=C_FREI_NAME, anchor="mm")
                _mittig("weitere", cx, name_y, f_name, dv_name, C_FREI_NAME)
            else:
                d.text((cx, cy), "+", font=f_plus, fill=C_AV_FREI_TXT, anchor="mm")
                _mittig("frei", cx, name_y, f_name, dv_name, C_FREI_NAME)
                _mittig("BEREIT", cx, zeit_y, f_bereit, f_bereit, C_FREI_META, _TR_BEREIT)
            continue
        if slot.owner:
            _mittig("OWNER", cx, _mitte_y(f_badge, cy + av_r), f_badge, f_badge,
                    C_OWN_TXT, _TR_OWNER)
        name = _truncate(slot.name, col_w - 6, f_name, dv_name)
        _mittig(name, cx, name_y, f_name, dv_name, C_TEXT)
        _mittig(_fmt_dur(slot.sekunden), cx, zeit_y, f_zeit, f_zeit, C_SLOT_META)

    out = io.BytesIO()
    # optimize=True spart nur 4 %, kostet aber 1,2 s je Karte (gemessen 2026-10-20)
    card.convert("RGB").save(out, "PNG", compress_level=6)
    return out.getvalue()

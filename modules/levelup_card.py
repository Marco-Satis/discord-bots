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


# --- Level-Up-/Rang-Karte (C3 Neon-Lobby, 21.10.2026) ------------------------

# Ausgabegroesse: Discord zeigt Bilder im Embed hoechstens ~600 px breit an.
# Gerendert wird doppelt so gross (2400x800) und auf 1200x400 verkleinert,
# damit Kreise und Verlaeufe glatt bleiben. Schriftgroessen gelten fuer die
# halbe Anzeige, also eher gross waehlen.
CARD_W, CARD_H = 1200, 400
_SS = 2  # Supersampling-Faktor

# Palette Design C3 (web/static/c3.css)
C3_BG = (9, 10, 20)
C3_VIOLET = (184, 77, 255)
C3_MAGENTA = (255, 63, 164)
C3_CYAN = (83, 231, 242)
C3_TEXT = (244, 241, 255)
C3_TEXT2 = (195, 191, 211)

STILE = ("neon", "akzent")

_WEB_FONTS = Path(__file__).resolve().parent.parent / "web" / "static" / "fonts"
_GROTESK = _WEB_FONTS / "spacegrotesk" / "SpaceGrotesk-Variable.ttf"
_MONO = _WEB_FONTS / "ibmplexmono" / "IBMPlexMono-SemiBold.ttf"


def _grotesk(size: int, weight: int = 700) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Space Grotesk (Dashboard-Schrift) in Gewicht `weight`, sonst DejaVu."""
    try:
        f = ImageFont.truetype(str(_GROTESK), size)
        try:
            f.set_variation_by_axes([max(300, min(700, weight))])
        except Exception:  # noqa: BLE001 — Variable-Achse optional
            pass
        return f
    except Exception:  # noqa: BLE001
        return _font(size, bold=weight >= 600)


def _mono(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(_MONO), size)
    except Exception:  # noqa: BLE001
        return _font(size, bold=False)


def _sauber(text: str) -> str:
    """Emoji und Symbole entfernen, die Space Grotesk nicht kennt (sonst Kaestchen).

    Rollennamen wie "〣│🎮・Mitspieler | Level 10" werden so zu "Mitspieler".
    """
    import unicodedata

    raus = {"\u200d", "\ufe0f", "\ufe0e"}
    zeichen = []
    for ch in text:
        if ch in raus or ord(ch) > 0xFFFF:
            continue
        kat = unicodedata.category(ch)
        if kat in ("So", "Sk", "Cs", "Co", "Cf"):
            continue
        # CJK-Zeichen und Box-Striche aus Kanal-/Rollen-Deko
        if 0x2500 <= ord(ch) <= 0x27BF or 0x3000 <= ord(ch) <= 0x303F or 0x30FB == ord(ch):
            continue
        zeichen.append(ch)
    return " ".join("".join(zeichen).split())


def rollen_anzeige(rollenname: str | None) -> str | None:
    """Rangrollen-Namen fuer die Karte kuerzen: Deko und "| Level N" weg."""
    if not rollenname:
        return None
    name = _sauber(rollenname).split("|")[0].strip(" -·")
    return name or None


def _passt(d: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    """Text auf max_w Pixel kuerzen (mit Auslassungszeichen)."""
    if d.textlength(text, font=font) <= max_w:
        return text
    while text and d.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _verlauf(w: int, h: int, farben: list[tuple[int, int, int]]) -> Image.Image:
    """Horizontaler Mehrfarb-Verlauf als RGB-Bild."""
    basis = Image.new("RGB", (len(farben), 1))
    basis.putdata(farben)
    return basis.resize((w, h), Image.BILINEAR)


def _glow(size: tuple[int, int], mitte: tuple[int, int], radius: int,
          farbe: tuple[int, int, int], staerke: int) -> Image.Image:
    """Weicher Lichtfleck (Radial-Glow) als RGBA-Ebene."""
    from PIL import ImageFilter

    ebene = Image.new("RGBA", size, farbe + (0,))
    maske = Image.new("L", size, 0)
    ImageDraw.Draw(maske).ellipse(
        [mitte[0] - radius, mitte[1] - radius, mitte[0] + radius, mitte[1] + radius],
        fill=staerke,
    )
    maske = maske.filter(ImageFilter.GaussianBlur(radius * 0.55))
    ebene.putalpha(maske)
    return ebene


def _halbtransparent(card: Image.Image, zeichnen) -> Image.Image:
    """Halbtransparente Form auf eigener Ebene zeichnen und darueberlegen.

    ImageDraw auf einem RGBA-Bild ersetzt den Alphawert statt zu mischen.
    """
    ebene = Image.new("RGBA", card.size, (0, 0, 0, 0))
    zeichnen(ImageDraw.Draw(ebene))
    return Image.alpha_composite(card, ebene)


def render_levelup_card(
    bg_path: str | Path | None,
    avatar_bytes: bytes | None,
    username: str,
    level: int,
    xp_in_level: int = 0,
    xp_for_next: int = 100,
    accent: str = "#B84DFF",
    header: str = "LEVEL UP",
    *,
    stil: str = "neon",
    unterzeile: str | None = None,
    rolle: str | None = None,
) -> bytes:
    """
    Level-Up- bzw. Rang-Karte rendern und als PNG-Bytes zurueckgeben.

    Ein Template fuer Level-Up UND Rang-Karte; nur die Kopfzeile unterscheidet
    sich ("LEVEL UP" vs. "RANG #3").

    Args:
        bg_path: eigenes Hintergrundbild oder None (dann C3-Neon-Hintergrund)
        avatar_bytes: Avatar als Bytes oder None (dann Initiale)
        username: Anzeigename
        level: erreichtes Level
        xp_in_level / xp_for_next: Fortschritt im Level
        accent: Akzentfarbe (#RRGGBB), wirkt im Stil "akzent"
        header: Kopfzeile, z.B. "LEVEL UP"
        stil: "neon" (Violett-Magenta-Cyan) oder "akzent" (eine Farbe)
        unterzeile: frei waehlbarer Text unter dem Namen (schon mit Platzhaltern ersetzt)
        rolle: Name einer neu erreichten Rangrolle (wird als Abzeichen gezeigt)
    """
    W, H = CARD_W * _SS, CARD_H * _SS
    s = _SS
    acc = _hex_to_rgb(accent, C3_VIOLET)
    if stil == "akzent":
        farben = [acc, tuple(min(255, int(c * 0.6 + 255 * 0.4)) for c in acc)]
        glow_a, glow_b = acc, acc
    else:
        farben = [C3_VIOLET, C3_MAGENTA, C3_CYAN]
        glow_a, glow_b = C3_VIOLET, C3_CYAN
    hl = farben[0]

    # --- Hintergrund ---
    card = Image.new("RGBA", (W, H), C3_BG + (255,))
    if bg_path:
        try:
            bg = _cover(Image.open(bg_path).convert("RGB"), W, H).convert("RGBA")
            card = Image.alpha_composite(card, bg)
            # Abdunkeln, links staerker: Text bleibt lesbar
            ov = _verlauf(W, H, [(0, 0, 0), (0, 0, 0)]).convert("RGBA")
            maske = _verlauf(W, H, [(215,) * 3, (150,) * 3, (110,) * 3]).convert("L")
            ov.putalpha(maske)
            card = Image.alpha_composite(card, ov)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"BG laden fehlgeschlagen ({bg_path}): {e}")
    card = Image.alpha_composite(card, _glow((W, H), (int(W * 0.12), int(H * 0.5)), 330 * s, glow_a, 85))
    card = Image.alpha_composite(card, _glow((W, H), (int(W * 0.95), int(H * 0.1)), 300 * s, glow_b, 70))
    if stil != "akzent":
        card = Image.alpha_composite(card, _glow((W, H), (int(W * 0.78), int(H * 1.05)), 260 * s, C3_MAGENTA, 60))

    # Feines Lobby-Raster
    raster = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(raster)
    for x in range(0, W, 40 * s):
        rd.line([(x, 0), (x, H)], fill=(244, 241, 255, 9), width=s)
    for y in range(0, H, 40 * s):
        rd.line([(0, y), (W, y)], fill=(244, 241, 255, 9), width=s)
    card = Image.alpha_composite(card, raster)

    d = ImageDraw.Draw(card, "RGBA")  # mischt Halbtransparenz statt sie zu ersetzen

    # --- Avatar mit Verlaufsring ---
    av = 236 * s
    ax, ay = 64 * s, (H - av) // 2
    ring = 10 * s
    ring_img = _verlauf(av + 2 * ring, av + 2 * ring, farben).convert("RGBA")
    rm = Image.new("L", ring_img.size, 0)
    ImageDraw.Draw(rm).ellipse([0, 0, ring_img.size[0] - 1, ring_img.size[1] - 1], fill=255)
    ring_img.putalpha(rm)
    card = Image.alpha_composite(card, _glow((W, H), (ax + av // 2, ay + av // 2), av // 2 + 40 * s, hl, 110))
    card.paste(ring_img, (ax - ring, ay - ring), ring_img)
    innen = Image.new("RGBA", (av + 2 * s, av + 2 * s), C3_BG + (255,))
    im = Image.new("L", innen.size, 0)
    ImageDraw.Draw(im).ellipse([0, 0, innen.size[0] - 1, innen.size[1] - 1], fill=255)
    card.paste(innen, (ax - s, ay - s), im)
    av_img = None
    if avatar_bytes:
        try:
            av_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((av, av), Image.LANCZOS)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Avatar laden fehlgeschlagen: {e}")
    if av_img is None:
        av_img = _verlauf(av, av, farben[:2]).convert("RGBA")
        ad = ImageDraw.Draw(av_img)
        initiale = (_sauber(username)[:1] or "?").upper()
        f_ini = _grotesk(120 * s)
        iw = ad.textlength(initiale, font=f_ini)
        ad.text(((av - iw) / 2, av * 0.18), initiale, font=f_ini, fill=C3_TEXT)
    am = Image.new("L", (av, av), 0)
    ImageDraw.Draw(am).ellipse([0, 0, av - 1, av - 1], fill=255)
    card.paste(av_img, (ax, ay), am)

    d = ImageDraw.Draw(card, "RGBA")  # mischt Halbtransparenz statt sie zu ersetzen

    # --- Grosse Levelzahl rechts (Verlaufsfuellung) ---
    rechts = W - 64 * s
    f_lvl = _grotesk(176 * s, 700)
    lvl_txt = str(level)
    lw = int(d.textlength(lvl_txt, font=f_lvl))
    bbox = d.textbbox((0, 0), lvl_txt, font=f_lvl)
    lh = bbox[3]
    lx, ly = rechts - lw, 34 * s
    maske = Image.new("L", (W, H), 0)
    ImageDraw.Draw(maske).text((lx, ly), lvl_txt, font=f_lvl, fill=255)
    fuell = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fuell.paste(_verlauf(lw, lh, farben).convert("RGBA"), (lx, ly))
    fuell.putalpha(maske)
    card = Image.alpha_composite(card, _glow((W, H), (lx + lw // 2, ly + lh // 2), lw // 2 + 60 * s, hl, 70))
    card = Image.alpha_composite(card, fuell)
    d = ImageDraw.Draw(card, "RGBA")  # mischt Halbtransparenz statt sie zu ersetzen
    f_lbl = _mono(26 * s)
    lbl = "LEVEL"
    d.text((rechts - d.textlength(lbl, font=f_lbl), ly + lh + 6 * s), lbl, font=f_lbl, fill=C3_TEXT2 + (255,))

    # --- Textblock ---
    tx = ax + av + 56 * s
    text_max = lx - tx - 36 * s

    # Kopf-Chip
    f_head = _mono(26 * s)
    head = _sauber(header).upper()[:24] or "LEVEL UP"
    hw = int(d.textlength(head, font=f_head))
    chip_h = 46 * s
    cy = 66 * s
    card = _halbtransparent(card, lambda od: od.rounded_rectangle(
        [tx, cy, tx + hw + 36 * s, cy + chip_h], radius=chip_h // 2,
        fill=hl + (56,), outline=hl + (210,), width=2 * s))
    d = ImageDraw.Draw(card, "RGBA")
    d.text((tx + 18 * s, cy + 7 * s), head, font=f_head, fill=C3_TEXT + (255,))

    # Name
    f_name = _grotesk(64 * s, 700)
    name = _passt(d, _sauber(username) or "Mitglied", f_name, text_max)
    d.text((tx, cy + chip_h + 14 * s), name, font=f_name, fill=C3_TEXT + (255,))

    # Unterzeile bzw. Rangrolle
    zeile_y = cy + chip_h + 100 * s
    rolle_txt = rollen_anzeige(rolle)
    if rolle_txt:
        f_r = _grotesk(30 * s, 600)
        r_label = "Neue Rangrolle: "
        d.text((tx, zeile_y), r_label, font=f_r, fill=C3_TEXT2 + (255,))
        rx = tx + int(d.textlength(r_label, font=f_r))
        d.text((rx, zeile_y), _passt(d, rolle_txt, f_r, text_max - (rx - tx)), font=f_r, fill=hl + (255,))
    elif unterzeile:
        f_u = _grotesk(30 * s, 500)
        d.text((tx, zeile_y), _passt(d, _sauber(unterzeile), f_u, text_max), font=f_u,
               fill=C3_TEXT2 + (255,))

    # Fortschrittsbalken
    bar_x, bar_y = tx, H - 104 * s
    bar_w, bar_h = rechts - tx, 26 * s
    card = _halbtransparent(card, lambda od: od.rounded_rectangle(
        [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=bar_h // 2,
        fill=(244, 241, 255, 26), outline=(244, 241, 255, 46), width=s))
    d = ImageDraw.Draw(card, "RGBA")
    frac = max(0.0, min(1.0, xp_in_level / xp_for_next)) if xp_for_next > 0 else 0.0
    fw = max(bar_h, int(bar_w * frac)) if frac > 0 else 0
    if fw:
        balken = _verlauf(fw, bar_h, farben).convert("RGBA")
        bm = Image.new("L", (fw, bar_h), 0)
        ImageDraw.Draw(bm).rounded_rectangle([0, 0, fw - 1, bar_h - 1], radius=bar_h // 2, fill=255)
        balken.putalpha(bm)
        card = Image.alpha_composite(card, _glow((W, H), (bar_x + fw, bar_y + bar_h // 2), 30 * s, farben[-1], 80))
        card.paste(balken, (bar_x, bar_y), balken)
        d = ImageDraw.Draw(card, "RGBA")  # mischt Halbtransparenz statt sie zu ersetzen

    f_xp = _mono(24 * s)
    xp_txt = f"{xp_in_level:,} / {xp_for_next:,} XP".replace(",", ".")
    d.text((bar_x, bar_y + bar_h + 12 * s), xp_txt, font=f_xp, fill=C3_TEXT2 + (255,))
    naechst = f"bis Level {level + 1}"
    d.text((rechts - d.textlength(naechst, font=f_xp), bar_y + bar_h + 12 * s), naechst,
           font=f_xp, fill=C3_TEXT2 + (200,))

    # Rahmen
    card = _halbtransparent(card, lambda od: od.rounded_rectangle(
        [s, s, W - s, H - s], radius=36 * s, outline=(244, 241, 255, 34), width=2 * s))
    oben = _verlauf(W - 72 * s, 3 * s, farben).convert("RGBA")
    card.paste(oben, (36 * s, 0), oben)

    # Runde Ecken + verkleinern
    ecken = Image.new("L", (W, H), 0)
    ImageDraw.Draw(ecken).rounded_rectangle([0, 0, W - 1, H - 1], radius=36 * s, fill=255)
    card.putalpha(ecken)
    card = card.resize((CARD_W, CARD_H), Image.LANCZOS)

    out = io.BytesIO()
    card.save(out, "PNG")
    return out.getvalue()


# --- Leaderboard-Karte (C3 Neon-Lobby, 21.10.2026) --------------------------

# Schmal und hoch statt breit (Marco 21.10.): 10 Zeilen je Seite, Rest blaettern
LB_W = 800
LB_HEAD = 160
LB_ROW = 84
LB_GAP = 8
LB_PAD = 36

# Platz 1-3: Gold, Silber, Bronze (Verlauf hell -> satt)
_MEDAILLE = {
    1: [(255, 224, 120), (255, 170, 40)],
    2: [(236, 238, 248), (170, 176, 196)],
    3: [(240, 176, 120), (196, 112, 52)],
}


def render_leaderboard_card(
    rows: list[dict],
    bg_path: str | Path | None,
    accent: str = "#B84DFF",
    title: str = "LEADERBOARD",
    subtitle: str | None = None,
    *,
    stil: str = "neon",
) -> bytes:
    """
    Leaderboard als PNG im Stil der Level-Up-Karte rendern.

    Args:
        rows: je Spieler rank, name, level, xp, optional avatar (bytes|None)
        bg_path: eigenes Hintergrundbild oder None (C3-Neon-Hintergrund)
        accent / stil: wie bei der Level-Up-Karte
        title / subtitle: Kopfzeile und Unterzeile (z. B. Servername)
    """
    s = _SS
    n = max(1, len(rows))
    W = LB_W * s
    H = (LB_HEAD + n * (LB_ROW + LB_GAP) + LB_PAD - LB_GAP) * s
    acc = _hex_to_rgb(accent, C3_VIOLET)
    if stil == "akzent":
        farben = [acc, tuple(min(255, int(c * 0.6 + 255 * 0.4)) for c in acc)]
        glow_a, glow_b = acc, acc
    else:
        farben = [C3_VIOLET, C3_MAGENTA, C3_CYAN]
        glow_a, glow_b = C3_VIOLET, C3_CYAN
    hl = farben[0]

    card = Image.new("RGBA", (W, H), C3_BG + (255,))
    if bg_path:
        try:
            bg = _cover(Image.open(bg_path).convert("RGB"), W, H).convert("RGBA")
            card = Image.alpha_composite(card, bg)
            card = Image.alpha_composite(card, Image.new("RGBA", (W, H), (0, 0, 0, 175)))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Leaderboard-BG laden fehlgeschlagen ({bg_path}): {e}")
    card = Image.alpha_composite(card, _glow((W, H), (int(W * 0.1), 100 * s), 280 * s, glow_a, 90))
    card = Image.alpha_composite(card, _glow((W, H), (int(W * 0.95), int(H * 0.55)), 320 * s, glow_b, 50))
    raster = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(raster)
    for x in range(0, W, 40 * s):
        rd.line([(x, 0), (x, H)], fill=(244, 241, 255, 8), width=s)
    for y in range(0, H, 40 * s):
        rd.line([(0, y), (W, y)], fill=(244, 241, 255, 8), width=s)
    card = Image.alpha_composite(card, raster)

    # --- Kopf ---
    d = ImageDraw.Draw(card, "RGBA")
    f_title = _grotesk(52 * s, 700)
    titel = _sauber(title).upper() or "LEADERBOARD"
    tw = int(d.textlength(titel, font=f_title))
    maske = Image.new("L", (W, H), 0)
    ImageDraw.Draw(maske).text((LB_PAD * s, 38 * s), titel, font=f_title, fill=255)
    fuell = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fuell.paste(_verlauf(tw, 66 * s, farben).convert("RGBA"), (LB_PAD * s, 38 * s))
    fuell.putalpha(maske)
    card = Image.alpha_composite(card, fuell)
    d = ImageDraw.Draw(card, "RGBA")
    if subtitle:
        f_sub = _mono(20 * s)
        d.text((LB_PAD * s, 104 * s), _passt(d, _sauber(subtitle).upper(), f_sub, W - 2 * LB_PAD * s),
               font=f_sub, fill=C3_TEXT2 + (255,))
    linie = _verlauf(W - 2 * LB_PAD * s, 2 * s, farben).convert("RGBA")
    card.paste(linie, (LB_PAD * s, (LB_HEAD - 18) * s), linie)

    # --- Zeilen ---
    av = 52 * s
    for i, row in enumerate(rows):
        y0 = (LB_HEAD + i * (LB_ROW + LB_GAP)) * s
        y1 = y0 + LB_ROW * s
        cy = (y0 + y1) // 2
        rank = int(row.get("rank", i + 1))
        medaille = _MEDAILLE.get(rank)
        x0, x1 = LB_PAD * s, W - LB_PAD * s

        # Eigene Zeile (row["du"]) bekommt einen Cyan-Rahmen
        du = bool(row.get("du"))

        def _zeile(od, _y0=y0, _y1=y1, _m=medaille, _du=du):
            if _du:
                rahmen = C3_CYAN + (230,)
            elif _m:
                rahmen = _m[1] + (150,)
            else:
                rahmen = (244, 241, 255, 26)
            od.rounded_rectangle([x0, _y0, x1, _y1], radius=18 * s,
                                 fill=(244, 241, 255, 16 if (_m or _du) else 10),
                                 outline=rahmen, width=(3 if _du else 2) * s)
        card = _halbtransparent(card, _zeile)
        if rank == 1:
            card = Image.alpha_composite(card, _glow((W, H), (x0 + 50 * s, cy), 70 * s, medaille[1], 70))
        d = ImageDraw.Draw(card, "RGBA")

        # Platz
        f_rank = _grotesk(34 * s, 700)
        r_txt = f"#{rank}"
        rx = x0 + 22 * s
        r_w = int(d.textlength(r_txt, font=f_rank))
        if medaille:
            m = Image.new("L", (W, H), 0)
            ImageDraw.Draw(m).text((rx, cy - 23 * s), r_txt, font=f_rank, fill=255)
            f = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            f.paste(_verlauf(r_w, 48 * s, medaille).convert("RGBA"), (rx, cy - 23 * s))
            f.putalpha(m)
            card = Image.alpha_composite(card, f)
            d = ImageDraw.Draw(card, "RGBA")
        else:
            d.text((rx, cy - 23 * s), r_txt, font=f_rank, fill=C3_TEXT2 + (255,))

        # Avatar mit Ring
        ax = x0 + 108 * s
        ay = cy - av // 2
        ring_farben = medaille or farben
        ring = _verlauf(av + 8 * s, av + 8 * s, ring_farben).convert("RGBA")
        rm = Image.new("L", ring.size, 0)
        ImageDraw.Draw(rm).ellipse([0, 0, ring.size[0] - 1, ring.size[1] - 1], fill=255)
        ring.putalpha(rm)
        card.paste(ring, (ax - 4 * s, ay - 4 * s), ring)
        av_img = None
        if row.get("avatar"):
            try:
                av_img = Image.open(io.BytesIO(row["avatar"])).convert("RGBA").resize((av, av), Image.LANCZOS)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Leaderboard-Avatar fehlgeschlagen: {e}")
        if av_img is None:
            av_img = _verlauf(av, av, farben[:2]).convert("RGBA")
            ad = ImageDraw.Draw(av_img)
            ini = (_sauber(str(row.get("name", "?")))[:1] or "?").upper()
            f_ini = _grotesk(28 * s)
            ad.text(((av - ad.textlength(ini, font=f_ini)) / 2, av * 0.16), ini, font=f_ini, fill=C3_TEXT)
        am = Image.new("L", (av, av), 0)
        ImageDraw.Draw(am).ellipse([0, 0, av - 1, av - 1], fill=255)
        card.paste(av_img, (ax, ay), am)
        d = ImageDraw.Draw(card, "RGBA")

        # Name + XP
        level = int(row.get("level", 0))
        xp = int(row.get("xp", 0))
        nx = ax + av + 22 * s
        f_lvl = _grotesk(34 * s, 700)
        lvl_txt = f"{level}"
        lw = int(d.textlength(lvl_txt, font=f_lvl))
        f_lbl = _mono(15 * s)
        lbl_w = int(d.textlength("LVL", font=f_lbl))
        rechts = x1 - 24 * s
        f_name = _grotesk(28 * s, 700)
        name = _passt(d, _sauber(str(row.get("name", "?"))) or "Mitglied", f_name,
                      rechts - lw - lbl_w - 32 * s - nx)
        d.text((nx, cy - 29 * s), name, font=f_name, fill=C3_TEXT + (255,))
        d.text((nx, cy + 7 * s), f"{xp:,} XP".replace(",", "."), font=_mono(18 * s),
               fill=C3_TEXT2 + (255,))

        # Level rechts
        d.text((rechts - lw, cy - 23 * s), lvl_txt, font=f_lvl, fill=(medaille[0] if medaille else hl) + (255,))
        d.text((rechts - lw - lbl_w - 10 * s, cy - 4 * s), "LVL", font=f_lbl, fill=C3_TEXT2 + (220,))

    # Rahmen, runde Ecken, verkleinern
    card = _halbtransparent(card, lambda od: od.rounded_rectangle(
        [s, s, W - s, H - s], radius=36 * s, outline=(244, 241, 255, 34), width=2 * s))
    oben = _verlauf(W - 72 * s, 3 * s, farben).convert("RGBA")
    card.paste(oben, (36 * s, 0), oben)
    ecken = Image.new("L", (W, H), 0)
    ImageDraw.Draw(ecken).rounded_rectangle([0, 0, W - 1, H - 1], radius=36 * s, fill=255)
    card.putalpha(ecken)
    card = card.resize((W // s, H // s), Image.LANCZOS)
    out = io.BytesIO()
    card.save(out, "PNG")
    return out.getvalue()

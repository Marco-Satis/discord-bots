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

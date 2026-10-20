"""
Temp Voice Views — Phase 12a (F17), Panel-Umbau 2026-10-20 (Design C3)
Persistente Views und Modals für die Steuerung temporärer Voice-Channels.

Das Kanal-Panel steht im Text-Chat des Temp-Channels: oben die Kopfkarte
(Bild aus ``modules.temp_voice_card``: Kanalname, Status, LIVE-Laufzeit, Squad),
darunter je Aktion eine Zeile mit Titel, Erklärung und Knopf.
  - KANAL: RENAME, LIMIT, PRIVATE/PUBLIC
  - CREW:  TRANSFER, KICK, BAN/UNBAN
  - unten: Logs, Activision-IDs

Alle Knöpfe verwenden feste custom_ids und sind persistent —
sie funktionieren auch nach einem Bot-Neustart.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

from modules.temp_voice import owner_overwrite
from utils.config import ADMIN_DATA_DIR
from utils.logger import get_logger
from utils.embeds import info_embed
from utils.panels import action_row, hud_container, panel_row
from utils.ui_kit import subtext

try:
    from modules.temp_voice_card import KartenSlot, render_temp_voice_card
    KARTE_VERFUEGBAR = True
except ImportError:  # Pillow fehlt -> Panel ohne Kopfkarte, nur Text
    KARTE_VERFUEGBAR = False

if TYPE_CHECKING:
    from modules.temp_voice import TempVoiceManager

logger = get_logger("modules.temp_voice_views")

# Eine Components-V2-Nachricht fasst 4000 Zeichen (discord.ui.View.content_length).
# 25 Namen bleiben mit Abstand darunter, auch bei langen Nicknames.
MAX_MITGLIEDER_IM_PANEL = 25

# Panel-Edits je Kanal serialisieren: Join und Leave kommen als getrennte Tasks
# an: ohne Sperre koennen sich zwei Edits ueberholen und die aeltere Momentaufnahme
# gewinnt.
_panel_locks: "dict[int, asyncio.Lock]" = {}

# Kanaele mit eingeplantem Refresh: Treten fuenf Leute fast gleichzeitig bei,
# entsteht ein Bild statt fuenf.
_refresh_geplant: set[int] = set()
REFRESH_SAMMELZEIT = 1.5
# Juengere Temp-Channels bekommen ihr Panel noch vom Erstell-Ablauf
SELBSTHEILUNG_AB_S = 60

# Akzentfarbe des Panels: Neon-Violett aus Design C3 (Server-Vorgabe 2026-10-20)
PANEL_AKZENT = 0xB84DFF
KARTEN_DATEI = "panel.png"

# Discord erlaubt nur wenige Namenswechsel je Kanal (2 in 10 min); danach haengt
# channel.edit bis zum Ablauf. Eigene Sperre davor, damit der Klick sofort
# eine Antwort bekommt.
RENAME_COOLDOWN_S = 300
_letzter_rename: dict[int, float] = {}

# Avatare fuer die Kopfkarte: je URL (enthaelt Hash + Groesse) einmal laden.
_avatar_cache: dict[str, bytes] = {}
_AVATAR_CACHE_MAX = 300

# Icons der Knoepfe: eigene App-Emojis aus der Datei (z. B. "<:tv_rename:123>"),
# sonst Unicode als Rueckfall.
_EMOJI_DATEI = ADMIN_DATA_DIR / "temp_voice_emojis.json"
_EMOJI_RUECKFALL = {
    "rename": "✏️", "limit": "\U0001f465", "private": "\U0001f512",
    "public": "\U0001f513", "transfer": "\U0001f91d", "kick": "\U0001f462",
    "ban": "\U0001f6ab", "unban": "\U0001f464", "claim": "\U0001f451",
    "logs": "\U0001f4ca", "accounts": "\U0001f3ae",
}
_emoji_cache: dict[str, str] | None = None


def _emoji(name: str) -> discord.PartialEmoji | str:
    """Icon eines Knopfs: eigenes App-Emoji, wenn eingetragen, sonst Unicode."""
    global _emoji_cache
    if _emoji_cache is None:
        try:
            _emoji_cache = json.loads(_EMOJI_DATEI.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _emoji_cache = {}
    eigen = _emoji_cache.get(name)
    if isinstance(eigen, str) and eigen.startswith("<"):
        return discord.PartialEmoji.from_str(eigen)
    return _EMOJI_RUECKFALL.get(name, "•")


# ======================================================================
# Panel-Embed — Slot-Liste + Status (VOICEPANEL-Style)
# ======================================================================

def _format_duration(iso_ts: str | None) -> str:
    """ISO-Zeitstempel -> kompakte Dauer seit dann (z.B. '5m', '1h 3m')."""
    if not iso_ts:
        return "?"
    try:
        start = datetime.fromisoformat(iso_ts)
    except ValueError:
        return "?"
    secs = int((datetime.now(timezone.utc) - start).total_seconds())
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h {mins % 60}m"


def _darf_steuern(manager, channel, member) -> bool:
    """Owner oder jemand mit "Kanaele verwalten" — die eine Regel fuer alle Wege."""
    return (manager.get_owner(channel.id) == member.id
            or member.guild_permissions.manage_channels)


def _sekunden_seit(iso_ts: str | None) -> int:
    """Sekunden seit einem ISO-Zeitstempel (0 bei fehlendem/kaputtem Wert)."""
    if not iso_ts:
        return 0
    try:
        start = datetime.fromisoformat(iso_ts)
    except ValueError:
        return 0
    return max(0, int((datetime.now(timezone.utc) - start).total_seconds()))


def _kurzname(name: str) -> str:
    """Kanalname ohne Server-Praefix: "〣│🎮・Kim" -> "Kim"."""
    for trenner in ("・", "│"):
        if trenner in name:
            name = name.rsplit(trenner, 1)[1]
    return name.strip() or name


def _hub_label(channel: discord.VoiceChannel, manager: "TempVoiceManager") -> str:
    """Label fuer die Karte: Hub "〣│➕・Gaming" auf "Ostfront HQ" -> "GAMING · OSTFRONT HQ"."""
    hub_id = manager.get_hub_id(channel.id)
    hub = channel.guild.get_channel(hub_id) if hub_id else None
    label = _kurzname(hub.name).upper()[:20] if hub else ""
    label = label or "TEMP-VOICE"
    server = (channel.guild.name or "").upper()
    # Kuerzen bei Platzmangel uebernimmt der Renderer
    return f"{label} · {server}" if server else label


async def _avatar_bytes(member: discord.Member) -> bytes | None:
    """Avatar als PNG (128 px), gecacht je URL. None, wenn Discord ihn nicht liefert."""
    asset = member.display_avatar.replace(size=128, format="png")
    if asset.url in _avatar_cache:
        return _avatar_cache[asset.url]
    try:
        daten = await asset.read()
    except (discord.HTTPException, discord.NotFound) as e:
        logger.debug(f"Avatar laden fehlgeschlagen ({member.id}): {e}")
        return None
    if len(_avatar_cache) >= _AVATAR_CACHE_MAX:
        _avatar_cache.pop(next(iter(_avatar_cache)))
    _avatar_cache[asset.url] = daten
    return daten


async def panel_datei(
    channel: discord.VoiceChannel, manager: "TempVoiceManager"
) -> discord.File | None:
    """
    Kopfkarte des Panels als PNG rendern (Pillow, im Thread).

    None, wenn das nicht geht — das Panel kommt dann mit Text-Kopf statt Bild.
    """
    if not KARTE_VERFUEGBAR:
        return None
    owner_id = manager.get_owner(channel.id)
    joined = manager.get_joined_at(channel.id)
    mitglieder = [m for m in channel.members if not m.bot]
    # Owner vorne, danach in Beitrittsreihenfolge
    mitglieder.sort(key=lambda m: (m.id != owner_id, joined.get(str(m.id), "")))
    sichtbar = mitglieder[:12]  # mehr zeigt die Karte nicht (11 + "+N")
    avatare = await asyncio.gather(*(_avatar_bytes(m) for m in sichtbar))
    slots = [
        KartenSlot(name=m.display_name, sekunden=_sekunden_seit(joined.get(str(m.id))),
                   owner=m.id == owner_id, avatar=a)
        for m, a in zip(sichtbar, avatare)
    ]
    # Der Rest zaehlt nur fuer "+N weitere" — ohne Avatar-Download
    slots += [KartenSlot(name=m.display_name, sekunden=0) for m in mitglieder[12:]]
    try:
        png = await asyncio.to_thread(
            render_temp_voice_card,
            hub=_hub_label(channel, manager),
            kanalname=_kurzname(channel.name),
            privat=manager.is_private(channel.id),
            laufzeit_s=_sekunden_seit(manager.get_created_at(channel.id)),
            mitglieder=slots,
            limit=channel.user_limit or 0,
        )
    except Exception as e:  # noqa: BLE001 — die Karte ist Zierde, das Panel muss kommen
        logger.warning(f"Panel-Karte rendern fehlgeschlagen ({channel.id}): {e}")
        return None
    return discord.File(io.BytesIO(png), filename=KARTEN_DATEI)


def _textkopf(channel: discord.VoiceChannel, manager: "TempVoiceManager") -> str:
    """Kopf ohne Bild (Rueckfall): Kanalname, Status und Mitgliederliste."""
    owner_id = manager.get_owner(channel.id)
    joined = manager.get_joined_at(channel.id)
    mitglieder = [m for m in channel.members if not m.bot]
    limit_text = str(channel.user_limit) if channel.user_limit else "∞"
    status = "PRIVATE" if manager.is_private(channel.id) else "PUBLIC"
    # escape_markdown: Anzeige- und Kanalnamen sind frei waehlbar, im TextDisplay
    # wird Markdown gerendert. Deckel gegen das 4000-Zeichen-Limit.
    sichtbar = mitglieder[:MAX_MITGLIEDER_IM_PANEL]
    zeilen = [
        f"## {discord.utils.escape_markdown(_kurzname(channel.name))}",
        subtext(f"{status} · LIVE {_format_duration(manager.get_created_at(channel.id))} "
                f"· {len(mitglieder)}/{limit_text}"),
    ]
    zeilen += [
        f"{'👑' if m.id == owner_id else '›'} "
        f"**{discord.utils.escape_markdown(m.display_name)}** "
        f"{subtext(_format_duration(joined.get(str(m.id))))}"
        for m in sichtbar
    ]
    if len(mitglieder) > len(sichtbar):
        zeilen.append(subtext(f"… und {len(mitglieder) - len(sichtbar)} weitere"))
    return "\n".join(zeilen)


def build_channel_container(
    channel: discord.VoiceChannel,
    manager: "TempVoiceManager",
    knoepfe: "dict[str, discord.ui.Button]",
    *,
    mit_karte: bool = False,
) -> discord.ui.Container:
    """
    Kanal-Panel (Design C3): Kopfkarte, dann je Aktion eine Zeile mit Knopf rechts.

    Args:
        channel: der temporäre Voice-Channel.
        manager: Zustandsquelle (Owner, privat, Beitrittszeiten).
        knoepfe: Knöpfe nach Aktion (rename, limit, private, public, transfer,
            kick, ban, logs, accounts).
        mit_karte: True, wenn die Nachricht ``panel.png`` als Anhang trägt.
    """
    owner_id = manager.get_owner(channel.id)
    owner = channel.guild.get_member(owner_id) if owner_id else None
    if mit_karte:
        kopf: discord.ui.Item = discord.ui.MediaGallery(
            discord.MediaGalleryItem(f"attachment://{KARTEN_DATEI}")
        )
    else:
        kopf = discord.ui.TextDisplay(_textkopf(channel, manager))
    sichtbarkeit = (
        panel_row("**PUBLIC**\n-# Wieder für alle Mitglieder sichtbar machen",
                  knoepfe["public"])
        if manager.is_private(channel.id) else
        panel_row("**PRIVATE**\n-# Nur noch wer drin ist, sieht den Kanal",
                  knoepfe["private"])
    )
    owner_name = discord.utils.escape_markdown(owner.display_name) if owner else "?"
    return discord.ui.Container(
        kopf,
        discord.ui.TextDisplay("-# **KANAL**"),
        panel_row("**RENAME**\n-# Gib deinem Kanal einen eigenen Namen", knoepfe["rename"]),
        panel_row("**LIMIT**\n-# Wie viele Leute maximal rein dürfen", knoepfe["limit"]),
        sichtbarkeit,
        discord.ui.Separator(),
        discord.ui.TextDisplay("-# **CREW**"),
        panel_row("**TRANSFER**\n-# Die Owner-Rolle an jemanden im Kanal geben",
                  knoepfe["transfer"]),
        panel_row("**KICK**\n-# User rauswerfen, er kann wieder joinen", knoepfe["kick"]),
        panel_row("**BAN / UNBAN**\n-# User für diesen Kanal bannen oder entbannen",
                  knoepfe["ban"]),
        discord.ui.Separator(),
        action_row(knoepfe["logs"], knoepfe["accounts"]),
        discord.ui.TextDisplay(
            f"-# Owner: **{owner_name}** · Geht der Owner, übernimmt automatisch der Nächste."
        ),
        accent_colour=discord.Colour(PANEL_AKZENT),
    )


def forget_channel(channel_id: int) -> None:
    """Panel-Sperre und Rename-Sperre eines geloeschten Kanals freigeben."""
    _panel_locks.pop(channel_id, None)
    _letzter_rename.pop(channel_id, None)


async def send_panel(
    channel: discord.VoiceChannel, manager: "TempVoiceManager"
) -> discord.Message | None:
    """
    Panel in den Text-Chat des Temp-Channels senden und die Message merken.

    Scheitert der Versand mit Karte (etwa ohne „Dateien anhaengen“), geht das
    Panel ohne Karte raus: Ein Panel ohne Bild ist besser als gar keins.
    """
    datei = await panel_datei(channel, manager)
    for karte in ([datei, None] if datei is not None else [None]):
        anhang = {"file": karte} if karte is not None else {}
        try:
            msg = await channel.send(
                view=TempVoiceControlView(channel, manager, mit_karte=karte is not None),
                **anhang,
            )
        except discord.HTTPException as e:
            logger.warning(
                f"Panel senden {'mit' if karte else 'ohne'} Karte fehlgeschlagen ({channel.id}): {e}"
            )
            continue
        manager.set_panel_message(channel.id, msg.id)
        return msg
    return None


async def _panel_nachsenden(channel: discord.VoiceChannel, manager: "TempVoiceManager") -> None:
    """Fehlendes Panel ersetzen; die Sperre verhindert zwei Panels nebeneinander."""
    sperre = _panel_locks.setdefault(channel.id, asyncio.Lock())
    async with sperre:
        logger.info(f"Panel fehlt in Temp-Channel {channel.id}, sende neu")
        await send_panel(channel, manager)


async def refresh_panel(
    channel: discord.VoiceChannel, manager: "TempVoiceManager", *, heilen: bool = False
) -> None:
    """
    Das gespeicherte Kanal-Panel mit aktuellem Stand neu rendern (Edit samt Bild).

    Mehrere Anlaesse kurz hintereinander ergeben einen Edit: Der erste plant ihn
    nach REFRESH_SAMMELZEIT ein, alle weiteren bis dahin steigen aus.

    Wurde das Panel geloescht, kommt ein neues. heilen=True (5-Minuten-Takt)
    sendet auch dann eins, wenn nie eins gemerkt wurde — aber erst nach
    SELBSTHEILUNG_AB_S, vorher sendet es noch der Erstell-Ablauf.
    """
    pmid = manager.get_panel_message(channel.id)
    if not pmid:
        erstellt = manager.get_created_at(channel.id)
        if heilen and (erstellt is None or _sekunden_seit(erstellt) >= SELBSTHEILUNG_AB_S):
            await _panel_nachsenden(channel, manager)
        return
    if channel.id in _refresh_geplant:
        return
    _refresh_geplant.add(channel.id)
    try:
        await asyncio.sleep(REFRESH_SAMMELZEIT)
    finally:
        # Vor dem Rendern freigeben: Was ab jetzt passiert, plant einen neuen Edit.
        _refresh_geplant.discard(channel.id)
    sperre = _panel_locks.setdefault(channel.id, asyncio.Lock())
    try:
        async with sperre:
            # Innerhalb der Sperre bauen, nicht davor: sonst schickte der
            # zweite Aufrufer eine aeltere Momentaufnahme hinterher.
            datei = await panel_datei(channel, manager)
            msg = channel.get_partial_message(pmid)
            try:
                await msg.edit(
                    view=TempVoiceControlView(channel, manager, mit_karte=datei is not None),
                    attachments=[datei] if datei is not None else [],
                )
            except discord.NotFound:
                # Panel wurde geloescht: neues senden (wir halten die Sperre schon)
                logger.info(f"Panel in Temp-Channel {channel.id} geloescht, sende neu")
                await send_panel(channel, manager)
    except discord.HTTPException as e:
        # Deckt auch den Fall „altes Embed-Panel, laesst sich nicht auf V2
        # umstellen" ab. Kein Drama: der naechste Temp-Channel bekommt das neue
        # Panel, und die Buttons des alten funktionieren weiter (gleiche IDs).
        logger.debug(f"Panel-Refresh fehlgeschlagen ({channel.id}): {e}")


def build_interface_container(
    aktionen: "list[discord.ui.ActionRow] | None" = None,
) -> discord.ui.Container:
    """
    Statisches Panel fuer den Interface-Kanal.

    Channel-unabhaengig: die Buttons steuern den Temp-Channel, in dem der
    Klickende gerade ist (`member.voice.channel`), nicht einen festen Kanal.
    """
    return hud_container(
        "TEMP-VOICE",
        state="info",
        subtitle="Steuerung fuer deinen eigenen Kanal",
        rows=[discord.ui.TextDisplay(
            "Erstelle deinen Voice-Channel über den **Join-to-Create**-Kanal "
            "und steuere ihn hier.\n"
            "-# Voraussetzung: du bist gerade in deinem Temp-Channel.\n"
            "-# Nur der Owner kann steuern — ist er weg, geht Übernehmen."
        )],
        actions=aktionen,
        footer_note="Die Buttons wirken auf den Kanal, in dem du gerade bist",
    )


# ======================================================================
# Modals — Eingabedialoge
# ======================================================================

class RenameModal(discord.ui.Modal, title="Channel umbenennen"):
    """
    Modal-Dialog zum Umbenennen eines temporären Voice-Channels.

    Fragt den neuen Kanalnamen ab und wendet ihn an.
    """

    new_name = discord.ui.TextInput(
        label="Neuer Kanalname",
        placeholder="z.B. Gaming mit Freunden",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=100,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Modal abgeschickt — Kanal umbenennen."""
        # Cog-Referenz über den Bot holen
        cog = interaction.client.get_cog("TempVoiceCog")
        if not cog:
            await interaction.response.send_message(
                "Temp-Voice-System ist nicht verfügbar.", ephemeral=True
            )
            return

        # Pruefen ob der User in einem Temp-Voice-Channel ist
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "Du bist in keinem Voice-Channel.", ephemeral=True
            )
            return

        channel = member.voice.channel
        manager: TempVoiceManager = cog.manager

        # Prüfen ob der User Owner ist
        if not manager.is_temp_channel(channel.id):
            await interaction.response.send_message(
                "Du bist nicht in einem temporären Channel.", ephemeral=True
            )
            return

        # Gleiche Regel wie _check_owner: Owner ODER jemand mit
        # "Kanaele verwalten". Vorher war die Pruefung hier strenger als beim
        # Button, der das Modal oeffnet — ein Admin bekam den Dialog und beim
        # Absenden eine Absage.
        if not _darf_steuern(manager, channel, member):
            await interaction.response.send_message(
                "Nur der Channel-Owner kann den Namen ändern.", ephemeral=True
            )
            return

        # Erst bestaetigen, dann umbenennen: channel.edit kann am Discord-Limit
        # warten, und die Interaktion verfaellt nach 3 s ohne Antwort.
        await interaction.response.defer(ephemeral=True)
        new_name = self.new_name.value.strip()
        try:
            await channel.edit(name=new_name)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Channel umbenennen fehlgeschlagen: {e}")
            await interaction.followup.send(
                "Channel konnte nicht umbenannt werden.", ephemeral=True
            )
            return
        _letzter_rename[channel.id] = time.monotonic()
        manager.update_channel_name(channel.id, new_name)
        await interaction.followup.send(
            f"Channel umbenannt zu **{discord.utils.escape_markdown(new_name)}**.",
            ephemeral=True,
        )
        logger.info(
            f"Temp-Voice umbenannt: {channel.id} -> '{new_name}' "
            f"von {member.display_name}"
        )
        # Der Kartentitel IST der Kanalname — ohne diesen Aufruf stuende
        # dort bis zum naechsten Join/Leave der alte.
        await refresh_panel(channel, manager)


class LimitModal(discord.ui.Modal, title="Userlimit setzen"):
    """
    Modal-Dialog zum Setzen des Userlimits eines temporären Voice-Channels.

    Akzeptiert Werte von 0 (unbegrenzt) bis 99.
    """

    limit_input = discord.ui.TextInput(
        label="Userlimit (0 = unbegrenzt, 1-99)",
        placeholder="0",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=2,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Modal abgeschickt — Userlimit setzen."""
        cog = interaction.client.get_cog("TempVoiceCog")
        if not cog:
            await interaction.response.send_message(
                "Temp-Voice-System ist nicht verfügbar.", ephemeral=True
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "Du bist in keinem Voice-Channel.", ephemeral=True
            )
            return

        channel = member.voice.channel
        manager: TempVoiceManager = cog.manager

        if not manager.is_temp_channel(channel.id):
            await interaction.response.send_message(
                "Du bist nicht in einem temporären Channel.", ephemeral=True
            )
            return

        if not _darf_steuern(manager, channel, member):
            await interaction.response.send_message(
                "Nur der Channel-Owner kann das Limit ändern.", ephemeral=True
            )
            return

        # Wert validieren
        try:
            limit = int(self.limit_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "Bitte gib eine gültige Zahl ein (0-99).", ephemeral=True
            )
            return

        if limit < 0 or limit > 99:
            await interaction.response.send_message(
                "Das Limit muss zwischen 0 und 99 liegen.", ephemeral=True
            )
            return

        # Limit anwenden
        try:
            await channel.edit(user_limit=limit)
            manager.update_user_limit(channel.id, limit)
            limit_text = "unbegrenzt" if limit == 0 else str(limit)
            await interaction.response.send_message(
                f"Userlimit auf **{limit_text}** gesetzt.", ephemeral=True
            )
            logger.info(
                f"Temp-Voice Limit geändert: {channel.id} -> {limit} "
                f"von {member.display_name}"
            )
            # Belegungsbalken und Kennzahl haengen am Limit — Panel nachziehen.
            await refresh_panel(channel, manager)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Userlimit setzen fehlgeschlagen: {e}")
            await interaction.response.send_message(
                "Userlimit konnte nicht gesetzt werden.", ephemeral=True
            )


# ======================================================================
# Owner-Transfer Select — User-Auswahl
# ======================================================================

class OwnerTransferSelect(discord.ui.UserSelect):
    """
    User-Select-Menu zum Übertragen der Channel-Ownership.

    Zeigt eine Benutzerauswahl an. Der ausgewählte User muss
    sich im selben Voice-Channel befinden.
    """

    def __init__(self) -> None:
        # Kein fester custom_id — jede Instanz bekommt eine einzigartige ID
        # damit mehrere gleichzeitige Transfer-Dialoge sich nicht gegenseitig stören
        super().__init__(
            placeholder="Neuen Owner auswählen...",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """User ausgewählt — Ownership übertragen."""
        cog = interaction.client.get_cog("TempVoiceCog")
        if not cog:
            await interaction.response.send_message(
                "Temp-Voice-System ist nicht verfügbar.", ephemeral=True
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "Du bist in keinem Voice-Channel.", ephemeral=True
            )
            return

        channel = member.voice.channel
        manager: TempVoiceManager = cog.manager

        if not manager.is_temp_channel(channel.id):
            await interaction.response.send_message(
                "Du bist nicht in einem temporären Channel.", ephemeral=True
            )
            return

        owner_id = manager.get_owner(channel.id)
        if owner_id != member.id:
            await interaction.response.send_message(
                "Nur der Channel-Owner kann die Ownership übertragen.",
                ephemeral=True,
            )
            return

        # Ausgewählten User prüfen
        selected_user = self.values[0]
        if selected_user.id == member.id:
            await interaction.response.send_message(
                "Du bist bereits der Owner.", ephemeral=True
            )
            return

        # Prüfen ob der ausgewählte User im Channel ist
        target_member = channel.guild.get_member(selected_user.id)
        if not target_member or not target_member.voice or target_member.voice.channel != channel:
            await interaction.response.send_message(
                "Der ausgewählte User muss sich im selben Voice-Channel befinden.",
                ephemeral=True,
            )
            return

        # Berechtigungen: beide behalten Sehen/Verbinden/Sprechen (auch wenn der
        # Kanal privat ist) — die Steuerung haengt an der Ownership im Manager,
        # nicht an Kanalrechten. BEVOR Ownership übertragen wird (Rollback-Sicherheit).
        try:
            await channel.set_permissions(member, overwrite=owner_overwrite())
            await channel.set_permissions(target_member, overwrite=owner_overwrite())
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Berechtigungen beim Transfer aktualisieren fehlgeschlagen: {e}")
            await interaction.response.send_message(
                "Ownership-Transfer fehlgeschlagen (Berechtigungsfehler).",
                ephemeral=True,
            )
            return

        # Ownership übertragen (erst nach erfolgreichen Permissions)
        manager.transfer_ownership(channel.id, selected_user.id)

        await interaction.response.send_message(
            f"Ownership an **{discord.utils.escape_markdown(target_member.display_name)}** "
            f"übertragen.",
            ephemeral=True,
        )
        logger.info(
            f"Temp-Voice Ownership transferiert: {channel.id} "
            f"von {member.display_name} an {target_member.display_name}"
        )
        # Krone auf der Karte und Owner-Zeile im Panel wandern mit
        await refresh_panel(channel, manager)


class OwnerTransferView(discord.ui.View):
    """
    Temporäre View für die Owner-Transfer-Auswahl.

    Wird als Antwort auf den Transfer-Button gesendet und
    enthält ein User-Select-Menu.
    """

    def __init__(self) -> None:
        super().__init__(timeout=60)  # 60 Sekunden Timeout
        self.add_item(OwnerTransferSelect())


# ======================================================================
# Persistente Control View — Hauptsteuerung
# ======================================================================

class AktionsButton(discord.ui.Button):
    """
    Panel-Button, der eine gebundene Methode der View aufruft.

    Der Umweg über die gebundene Methode statt über ``self.view`` ist Absicht:
    in einem Components-V2-Layout hängen die Buttons in Container und Zeilen,
    die Auflösung nach oben wäre eine Annahme über interne Struktur.
    """

    def __init__(self, handler, *, custom_id: str, emoji: "discord.PartialEmoji | str",
                 style: discord.ButtonStyle = discord.ButtonStyle.secondary,
                 label: str | None = None) -> None:
        super().__init__(label=label, custom_id=custom_id, emoji=emoji, style=style)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction)


class TempVoiceControlView(discord.ui.LayoutView):
    """
    Persistente View für die Channel-Steuerung (Components V2).

    Umstieg 2026-08-13: vorher ein Embed plus separate Button-Leiste. Jetzt ein
    Panel — Kopfzeile, Mitgliederliste und Buttons gehören sichtbar zusammen.
    Die ``custom_id``s sind unverändert, Klicks auf noch stehende alte Panels
    landen also weiterhin hier.

    Args:
        channel: Temp-Channel für das Kanal-Panel; ``None`` baut das statische
            Interface-Panel, dessen Buttons auf den Kanal des Klickenden wirken.
        manager: Zustandsquelle für Owner, Bans und Beitrittszeiten.
    """

    def __init__(
        self,
        channel: discord.VoiceChannel | None = None,
        manager: "TempVoiceManager | None" = None,
        *,
        mit_karte: bool = False,
    ) -> None:
        # timeout=None fuer persistente Views (kein Ablauf)
        super().__init__(timeout=None)
        if channel is not None and manager is not None:
            self.add_item(
                build_channel_container(channel, manager, self._knoepfe(), mit_karte=mit_karte)
            )
        else:
            # Registrierung beim Start + statisches Interface-Panel: alle
            # custom_ids, auch die, die nur noch in alten Panels stehen
            # (unban, claim) — sonst sterben deren Knoepfe nach dem Deploy.
            self.add_item(build_interface_container(self._aktionszeilen()))

    def _knoepfe(self) -> "dict[str, discord.ui.Button]":
        """Knöpfe des Kanal-Panels: Zeilen nur mit Icon, unten mit Beschriftung."""
        def knopf(name: str, handler, label: str | None = None) -> AktionsButton:
            return AktionsButton(handler, custom_id=f"temp_voice:{name}",
                                 emoji=_emoji(name), label=label)

        return {
            "rename": knopf("rename", self.rename_button),
            "limit": knopf("limit", self.limit_button),
            "private": knopf("private", self.private_button),
            "public": knopf("public", self.public_button),
            "transfer": knopf("transfer", self.transfer_button),
            "kick": knopf("kick", self.kick_button),
            "ban": knopf("ban", self.ban_button),
            "logs": knopf("logs", self.logs_button, "Logs"),
            "accounts": knopf("accounts", self.accounts_button, "Activision-IDs"),
        }

    def _aktionszeilen(self) -> list[discord.ui.ActionRow]:
        """Alle Knöpfe in Zeilen zu höchstens fünf (Registrierung/Interface-Panel)."""
        aufteilung = (
            (("Rename", "rename", self.rename_button),
             ("Limit", "limit", self.limit_button),
             ("Private", "private", self.private_button),
             ("Public", "public", self.public_button),
             ("Transfer", "transfer", self.transfer_button)),
            (("Kick", "kick", self.kick_button),
             ("Ban", "ban", self.ban_button),
             ("Unban", "unban", self.unban_button),
             ("Claim", "claim", self.claim_button),
             ("Logs", "logs", self.logs_button)),
            (("Activision-IDs", "accounts", self.accounts_button),),
        )
        return [
            action_row(*(
                AktionsButton(handler, label=label, custom_id=f"temp_voice:{name}",
                              emoji=_emoji(name))
                for label, name, handler in zeile
            ))
            for zeile in aufteilung
        ]

    def _get_manager(self, interaction: discord.Interaction) -> TempVoiceManager | None:
        """Hilfsmethode: TempVoiceManager aus dem Cog holen."""
        cog = interaction.client.get_cog("TempVoiceCog")
        if cog:
            return cog.manager
        return None

    async def _check_in_temp(
        self,
        interaction: discord.Interaction,
    ) -> tuple[discord.Member, discord.VoiceChannel, "TempVoiceManager"] | None:
        """
        Hilfsmethode: Prüfen ob der User in einem Temp-Channel ist (OHNE
        Owner-Pflicht). Für Aktionen wie Claim die auch Nicht-Owner dürfen.

        Returns:
            Tuple (member, channel, manager) oder None (Fehlermeldung gesendet).
        """
        manager = self._get_manager(interaction)
        if not manager:
            await interaction.response.send_message(
                "Temp-Voice-System ist nicht verfügbar.", ephemeral=True
            )
            return None

        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "Du bist in keinem Voice-Channel.", ephemeral=True
            )
            return None

        channel = member.voice.channel
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "Du bist nicht in einem Voice-Channel.", ephemeral=True
            )
            return None

        if not manager.is_temp_channel(channel.id):
            await interaction.response.send_message(
                "Du bist nicht in einem temporären Channel.", ephemeral=True
            )
            return None

        return member, channel, manager

    async def _check_owner(
        self,
        interaction: discord.Interaction,
    ) -> tuple[discord.Member, discord.VoiceChannel, "TempVoiceManager"] | None:
        """
        Hilfsmethode: Prüfen ob der User Owner eines Temp-Channels ist.
        Admin-Override: Mitglieder mit `Manage Channels` dürfen ebenfalls.

        Returns:
            Tuple (member, channel, manager) wenn OK, sonst None (Fehler gesendet).
        """
        result = await self._check_in_temp(interaction)
        if result is None:
            return None
        member, channel, manager = result

        owner_id = manager.get_owner(channel.id)
        is_admin = member.guild_permissions.manage_channels
        if owner_id != member.id and not is_admin:
            await interaction.response.send_message(
                "Nur der Channel-Owner kann diese Aktion ausführen.",
                ephemeral=True,
            )
            return None

        return member, channel, manager

    # ------------------------------------------------------------------
    # Button: Umbenennen
    # ------------------------------------------------------------------

    async def rename_button(self, interaction: discord.Interaction) -> None:
        """Button-Handler: Modal zum Umbenennen öffnen."""
        # Vorprüfung: Ist der User Owner?
        result = await self._check_owner(interaction)
        if result is None:
            return
        _member, channel, _manager = result

        rest = RENAME_COOLDOWN_S - (time.monotonic() - _letzter_rename.get(channel.id, -1e9))
        if rest > 0:
            await interaction.response.send_message(
                f"RENAME geht wieder in {math.ceil(rest / 60)} min — "
                "Discord erlaubt nur wenige Namenswechsel pro Kanal.",
                ephemeral=True,
            )
            return

        modal = RenameModal()
        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------
    # Button: Limit setzen
    # ------------------------------------------------------------------

    async def limit_button(self, interaction: discord.Interaction) -> None:
        """Button-Handler: Modal zum Setzen des Userlimits öffnen."""
        result = await self._check_owner(interaction)
        if result is None:
            return

        modal = LimitModal()
        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------
    # Button: Privat (zu + unsichtbar)
    # ------------------------------------------------------------------

    async def private_button(self, interaction: discord.Interaction) -> None:
        """Button-Handler: Channel privat schalten (zu + unsichtbar)."""
        result = await self._check_owner(interaction)
        if result is None:
            return
        member, channel, manager = result

        await manager.set_private(channel)
        await interaction.response.send_message(
            "Channel ist jetzt **privat** — geschlossen und unsichtbar für alle anderen.",
            ephemeral=True,
        )
        logger.info(f"Temp-Voice privat: {channel.id} von {member.display_name}")
        await refresh_panel(channel, manager)

    # ------------------------------------------------------------------
    # Button: Öffentlich (offen + sichtbar)
    # ------------------------------------------------------------------

    async def public_button(self, interaction: discord.Interaction) -> None:
        """Button-Handler: Channel öffentlich schalten (offen + sichtbar)."""
        result = await self._check_owner(interaction)
        if result is None:
            return
        member, channel, manager = result

        await manager.set_public(channel)
        await interaction.response.send_message(
            "Channel ist jetzt **öffentlich** — offen und sichtbar für alle Mitglieder.",
            ephemeral=True,
        )
        logger.info(f"Temp-Voice öffentlich: {channel.id} von {member.display_name}")
        await refresh_panel(channel, manager)

    # ------------------------------------------------------------------
    # Button: Ownership übertragen
    # ------------------------------------------------------------------

    async def transfer_button(self, interaction: discord.Interaction) -> None:
        """Button-Handler: User-Select zum Übertragen der Ownership anzeigen."""
        result = await self._check_owner(interaction)
        if result is None:
            return

        member, channel, manager = result

        # Prüfen ob andere User im Channel sind
        other_members = [m for m in channel.members if not m.bot and m.id != member.id]
        if not other_members:
            await interaction.response.send_message(
                "Kein anderer User im Channel, an den die Ownership "
                "übertragen werden könnte.",
                ephemeral=True,
            )
            return

        # User-Select-View senden
        view = OwnerTransferView()
        await interaction.response.send_message(
            "Wähle den neuen Owner aus:",
            view=view,
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Button: Bannen (User trennen + connect-deny)
    # ------------------------------------------------------------------

    async def kick_button(self, interaction: discord.Interaction) -> None:
        """Button-Handler: Auswahl der Leute im Kanal zum Kicken anzeigen."""
        result = await self._check_owner(interaction)
        if result is None:
            return
        member, channel, manager = result

        kandidaten = _crew_ziele(channel, manager, member)
        if not kandidaten:
            await interaction.response.send_message(
                "Hier ist niemand, den du kicken kannst.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Wen willst du kicken? Er kann danach wieder joinen.",
            view=KickView(kandidaten), ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Button: Ban / Unban (ein Dialog fuer beides)
    # ------------------------------------------------------------------

    async def ban_button(self, interaction: discord.Interaction) -> None:
        """Button-Handler: Ban-Auswahl, darunter die Unban-Liste, falls jemand gebannt ist."""
        result = await self._check_owner(interaction)
        if result is None:
            return
        _member, channel, manager = result
        banned = manager.get_banned(channel.id)
        text = "**Ban:** User auswählen."
        if banned:
            text += "\n**Unban:** unten aus der Liste wählen."
        await interaction.response.send_message(
            text, view=BanUserView(channel, banned), ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Button: Entbannen (aus Ban-Liste)
    # ------------------------------------------------------------------

    async def unban_button(self, interaction: discord.Interaction) -> None:
        """Button-Handler: Select aus der Ban-Liste zum Entbannen anzeigen."""
        result = await self._check_owner(interaction)
        if result is None:
            return
        member, channel, manager = result

        banned = manager.get_banned(channel.id)
        if not banned:
            await interaction.response.send_message(
                "Niemand ist in diesem Channel gebannt.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Wähle den zu entbannenden User:",
            view=UnbanView(channel, banned),
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Button: Claim (Owner weg -> übernehmen)
    # ------------------------------------------------------------------

    async def claim_button(self, interaction: discord.Interaction) -> None:
        """Button-Handler: Channel übernehmen wenn der Owner nicht mehr da ist."""
        result = await self._check_in_temp(interaction)
        if result is None:
            return
        member, channel, manager = result

        owner_id = manager.get_owner(channel.id)
        if owner_id == member.id:
            await interaction.response.send_message(
                "Du bist bereits der Owner.", ephemeral=True
            )
            return
        owner_present = any(
            m.id == owner_id for m in channel.members if not m.bot
        )
        if owner_present:
            await interaction.response.send_message(
                "Der Owner ist noch im Channel — Übernahme nur möglich wenn er weg ist.",
                ephemeral=True,
            )
            return

        await manager.claim(channel, member)
        await interaction.response.send_message(
            "Du bist jetzt der **Owner** dieses Channels.", ephemeral=True
        )
        logger.info(
            f"Temp-Voice Claim: {channel.id} neuer Owner {member.display_name}"
        )
        await refresh_panel(channel, manager)

    # ------------------------------------------------------------------
    # Button: Logs (letzte Events)
    # ------------------------------------------------------------------

    async def logs_button(self, interaction: discord.Interaction) -> None:
        """Button-Handler: Letzte Join/Leave/Mod-Events anzeigen (ephemeral)."""
        result = await self._check_owner(interaction)
        if result is None:
            return
        member, channel, manager = result

        events = manager.get_events(channel.id, limit=8)
        if not events:
            await interaction.response.send_message(
                "Noch keine Events in diesem Channel.", ephemeral=True
            )
            return

        emoji_map = {
            "join": "➡️", "leave": "⬅️", "ban": "\U0001f6ab",
            "unban": "\U0001f464", "claim": "\U0001f451", "kick": "\U0001f462",
            "private": "\U0001f512", "public": "\U0001f513",
        }
        lines = []
        for ev in events:
            em = emoji_map.get(ev.get("type", ""), "•")
            uid = ev.get("uid")
            target = channel.guild.get_member(uid) if uid else None
            name = target.display_name if target else f"User {uid}"
            lines.append(f"{em} **{name}** · vor {_format_duration(ev.get('ts'))}")

        embed = info_embed(
            title="\U0001f4ca Letzte Events",
            description="\n".join(lines),
        )
        await interaction.response.send_message(
            embed=embed, ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def accounts_button(self, interaction: discord.Interaction) -> None:
        """
        Activision-IDs aller Leute im Kanal (Vorbild voicepanel) — nur für den
        Klickenden sichtbar und nur, wenn er selbst in diesem Temp-Channel ist.
        """
        from modules.linked_accounts import LinkedAccountsManager

        result = await self._check_in_temp(interaction)
        if result is None:
            return
        _member, channel, _manager = result
        await interaction.response.defer(ephemeral=True, thinking=True)

        leute = [m for m in channel.members if not m.bot][:MAX_MITGLIEDER_IM_PANEL]
        links = await asyncio.gather(*(
            LinkedAccountsManager.get_link(channel.guild.id, m.id, "activision")
            for m in leute
        ))
        zeilen = [
            f"**{discord.utils.escape_markdown(m.display_name)}** · "
            + (f"`{discord.utils.escape_markdown(str(link))}`" if link else "-# nicht verlinkt")
            for m, link in zip(leute, links)
        ]
        embed = info_embed(
            title="\U0001f3ae Activision-IDs im Kanal",
            description="\n".join(zeilen) or "Niemand im Kanal.",
        )
        embed.set_footer(text="Eigene ID verlinken: /link activision <ID>")
        await interaction.followup.send(
            embed=embed, ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


# ======================================================================
# Ban / Unban — temporäre Select-Views (pro Interaktion, kein Persist)
# ======================================================================

def _resolve_owner_action(
    interaction: discord.Interaction,
) -> tuple[discord.Member, discord.VoiceChannel, "TempVoiceManager"] | None:
    """
    Gemeinsame Vorprüfung für Ban/Unban-Selects: User ist Owner (oder Admin)
    eines Temp-Channels. Gibt None zurück ohne zu antworten — Caller antwortet.
    """
    cog = interaction.client.get_cog("TempVoiceCog")
    if not cog:
        return None
    member = interaction.user
    if (not isinstance(member, discord.Member) or not member.voice
            or not isinstance(member.voice.channel, discord.VoiceChannel)):
        return None
    channel = member.voice.channel
    manager = cog.manager
    if not manager.is_temp_channel(channel.id):
        return None
    owner_id = manager.get_owner(channel.id)
    if owner_id != member.id and not member.guild_permissions.manage_channels:
        return None
    return member, channel, manager


def _geschuetzt(member: discord.Member) -> bool:
    """Bots und Team (darf Kanäle verwalten) lassen sich nicht kicken oder bannen."""
    return member.bot or member.guild_permissions.manage_channels


def _crew_ziele(
    channel: discord.VoiceChannel, manager: "TempVoiceManager", wer: discord.Member
) -> list[discord.Member]:
    """Leute im Kanal, die der Owner kicken darf (nicht er selbst, kein Team, keine Bots)."""
    owner_id = manager.get_owner(channel.id)
    return [
        m for m in channel.members
        if m.id not in (wer.id, owner_id) and not _geschuetzt(m)
    ][:25]  # Discord-Limit 25 Optionen


class KickSelect(discord.ui.Select):
    """Auswahl der Leute im Kanal zum Kicken (nur trennen, kein Ban)."""

    def __init__(self, kandidaten: list[discord.Member]) -> None:
        super().__init__(
            placeholder="User auswählen...",
            min_values=1, max_values=1,
            options=[discord.SelectOption(label=m.display_name[:100], value=str(m.id))
                     for m in kandidaten],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = _resolve_owner_action(interaction)
        if ctx is None:
            await interaction.response.send_message(
                "Nur der Owner kann kicken (oder du bist in keinem Temp-Channel).",
                ephemeral=True,
            )
            return
        _member, channel, manager = ctx

        target = channel.guild.get_member(int(self.values[0]))
        if target is None or _geschuetzt(target):
            await interaction.response.send_message(
                "Diesen User kannst du nicht kicken.", ephemeral=True
            )
            return
        if not await manager.kick_user(channel, target):
            await interaction.response.send_message(
                "Der User ist nicht mehr im Kanal.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"**{discord.utils.escape_markdown(target.display_name)}** wurde gekickt.",
            ephemeral=True,
        )
        logger.info(f"Temp-Voice Kick: {channel.id} -> {target.id}")
        # Das Leave-Event zieht das Panel nach.


class KickView(discord.ui.View):
    """Temporäre View mit der Kick-Auswahl (60s Timeout)."""

    def __init__(self, kandidaten: list[discord.Member]) -> None:
        super().__init__(timeout=60)
        self.add_item(KickSelect(kandidaten))


class BanUserSelect(discord.ui.UserSelect):
    """User-Select zum Bannen eines Users aus dem Temp-Channel."""

    def __init__(self) -> None:
        super().__init__(
            placeholder="Ban: User auswählen...",
            min_values=1, max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = _resolve_owner_action(interaction)
        if ctx is None:
            await interaction.response.send_message(
                "Nur der Owner kann bannen (oder du bist in keinem Temp-Channel).",
                ephemeral=True,
            )
            return
        member, channel, manager = ctx

        target = self.values[0]
        if target.id == member.id:
            await interaction.response.send_message(
                "Du kannst dich nicht selbst bannen.", ephemeral=True
            )
            return
        if target.id == manager.get_owner(channel.id):
            await interaction.response.send_message(
                "Der Owner kann nicht gebannt werden.", ephemeral=True
            )
            return
        target_member = channel.guild.get_member(target.id)
        if target_member is None:
            await interaction.response.send_message(
                "User nicht auf dem Server gefunden.", ephemeral=True
            )
            return
        if _geschuetzt(target_member):
            await interaction.response.send_message(
                "Bots und Team-Mitglieder lassen sich nicht bannen.", ephemeral=True
            )
            return

        await manager.ban_user(channel, target_member)
        await interaction.response.send_message(
            f"**{discord.utils.escape_markdown(target_member.display_name)}** "
            f"wurde gebannt und getrennt.",
            ephemeral=True,
        )
        logger.info(f"Temp-Voice Ban: {channel.id} -> {target_member.id}")
        await refresh_panel(channel, manager)


class BanUserView(discord.ui.View):
    """Temporäre View: Ban-Auswahl, darunter die Unban-Liste, wenn jemand gebannt ist (60s)."""

    def __init__(
        self,
        channel: discord.VoiceChannel | None = None,
        banned_ids: list[int] | None = None,
    ) -> None:
        super().__init__(timeout=60)
        self.add_item(BanUserSelect())
        if channel is not None and banned_ids:
            self.add_item(UnbanSelect(channel, banned_ids))


class UnbanSelect(discord.ui.Select):
    """String-Select aus der Ban-Liste zum Entbannen."""

    def __init__(
        self, channel: discord.VoiceChannel, banned_ids: list[int]
    ) -> None:
        options = []
        for uid in banned_ids[:25]:  # Discord-Limit 25 Optionen
            m = channel.guild.get_member(uid)
            label = (m.display_name if m else f"User {uid}")[:100]
            options.append(discord.SelectOption(label=label, value=str(uid)))
        super().__init__(
            placeholder="Unban: User auswählen...",
            min_values=1, max_values=1, options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = _resolve_owner_action(interaction)
        if ctx is None:
            await interaction.response.send_message(
                "Nur der Owner kann entbannen (oder du bist in keinem Temp-Channel).",
                ephemeral=True,
            )
            return
        member, channel, manager = ctx

        uid = int(self.values[0])
        target_member = channel.guild.get_member(uid)
        if target_member is None:
            # User nicht mehr auf dem Server — nur aus der Liste entfernen
            manager.remove_ban(channel.id, uid)
            await interaction.response.send_message(
                "Aus der Ban-Liste entfernt (User nicht mehr auf dem Server).",
                ephemeral=True,
            )
            await refresh_panel(channel, manager)
            return

        ok = await manager.unban_user(channel, target_member)
        msg = (f"**{target_member.display_name}** wurde entbannt."
               if ok else "Dieser User war nicht (mehr) gebannt.")
        await interaction.response.send_message(msg, ephemeral=True)
        logger.info(f"Temp-Voice Unban: {channel.id} -> {uid} (ok={ok})")
        await refresh_panel(channel, manager)


class UnbanView(discord.ui.View):
    """Temporäre View mit dem Unban-Select (60s Timeout)."""

    def __init__(
        self, channel: discord.VoiceChannel, banned_ids: list[int]
    ) -> None:
        super().__init__(timeout=60)
        self.add_item(UnbanSelect(channel, banned_ids))

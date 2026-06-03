"""
Temp Voice Views — Phase 12a (F17)
Persistente Views und Modals für die Steuerung temporärer Voice-Channels.

Enthält die interaktiven UI-Elemente, die dem Channel-Owner
zur Verfügung stehen:
  - Umbenennen (Modal mit Texteingabe)
  - Limit setzen (Modal mit Zahleneingabe)
  - Sperren/Entsperren (Toggle fuer @everyone connect)
  - Ownership übertragen (User-Select-Menu)

Alle Views verwenden feste custom_ids und sind persistent —
sie funktionieren auch nach einem Bot-Neustart.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

from utils.logger import get_logger

if TYPE_CHECKING:
    from modules.temp_voice import TempVoiceManager

logger = get_logger("modules.temp_voice_views")


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


def build_panel_embed(
    channel: discord.VoiceChannel, manager: "TempVoiceManager"
) -> discord.Embed:
    """
    Kontrollpanel-Embed bauen: Header + Slot-Liste (Mitglieder mit 👑 Owner +
    Zeit-im-Channel) + Status-Zeile (privat/offen · Limit · Ban-Anzahl).
    """
    owner_id = manager.get_owner(channel.id)
    private = manager.is_private(channel.id)
    banned = manager.get_banned(channel.id)
    joined = manager.get_joined_at(channel.id)

    embed = discord.Embed(
        title=f"🎮 {channel.name}",
        color=0xE74C3C if private else 0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )

    # Slot-Liste
    members = [m for m in channel.members if not m.bot]
    if members:
        lines = []
        for m in members:
            crown = " 👑" if m.id == owner_id else ""
            dur = _format_duration(joined.get(str(m.id)))
            lines.append(f"• **{m.display_name}**{crown} · {dur}")
        slots = "\n".join(lines)
    else:
        slots = "_Noch niemand hier._"
    limit_txt = str(channel.user_limit) if channel.user_limit else "∞"
    embed.add_field(
        name=f"Mitglieder ({len(members)}/{limit_txt})", value=slots, inline=False
    )

    # Status-Zeile
    status = "🔒 Privat" if private else "🔓 Öffentlich"
    embed.add_field(
        name="Status",
        value=f"{status} · 👤 Limit {limit_txt} · 🚫 {len(banned)} gebannt",
        inline=False,
    )
    embed.set_footer(text="Steuerung über die Buttons (nur Owner)")
    return embed


async def refresh_panel(
    channel: discord.VoiceChannel, manager: "TempVoiceManager"
) -> None:
    """Das gespeicherte Panel-Embed mit aktuellem State neu rendern (Edit)."""
    pmid = manager.get_panel_message(channel.id)
    if not pmid:
        return
    try:
        msg = channel.get_partial_message(pmid)
        await msg.edit(embed=build_panel_embed(channel, manager))
    except discord.HTTPException as e:
        logger.debug(f"Panel-Refresh fehlgeschlagen ({channel.id}): {e}")


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

        owner_id = manager.get_owner(channel.id)
        if owner_id != member.id:
            await interaction.response.send_message(
                "Nur der Channel-Owner kann den Namen ändern.", ephemeral=True
            )
            return

        # Channel umbenennen
        new_name = self.new_name.value.strip()
        try:
            await channel.edit(name=new_name)
            manager.update_channel_name(channel.id, new_name)
            await interaction.response.send_message(
                f"Channel umbenannt zu **{new_name}**.", ephemeral=True
            )
            logger.info(
                f"Temp-Voice umbenannt: {channel.id} -> '{new_name}' "
                f"von {member.display_name}"
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Channel umbenennen fehlgeschlagen: {e}")
            await interaction.response.send_message(
                "Channel konnte nicht umbenannt werden.", ephemeral=True
            )


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

        owner_id = manager.get_owner(channel.id)
        if owner_id != member.id:
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

        # Berechtigungen aktualisieren: alter Owner verliert Manage-Rechte,
        # neuer Owner bekommt sie — BEVOR Ownership übertragen wird (Rollback-Sicherheit)
        try:
            await channel.set_permissions(
                member,
                overwrite=discord.PermissionOverwrite(
                    connect=True,
                    speak=True,
                ),
            )
            await channel.set_permissions(
                target_member,
                overwrite=discord.PermissionOverwrite(
                    connect=True,
                    speak=True,
                    manage_channels=True,
                    mute_members=True,
                    deafen_members=True,
                    move_members=True,
                    manage_permissions=True,
                ),
            )
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
            f"Ownership an **{target_member.display_name}** übertragen.",
            ephemeral=True,
        )
        logger.info(
            f"Temp-Voice Ownership transferiert: {channel.id} "
            f"von {member.display_name} an {target_member.display_name}"
        )


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

class TempVoiceControlView(discord.ui.View):
    """
    Persistente View für die Channel-Steuerung.

    Wird als Kontrollpanel im Channel oder als Nachricht gesendet.
    Enthält Buttons für alle Channel-Aktionen. Verwendet feste
    custom_ids damit die Buttons nach einem Bot-Neustart funktionieren.
    """

    def __init__(self) -> None:
        # timeout=None fuer persistente Views (kein Ablauf)
        super().__init__(timeout=None)

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

    @discord.ui.button(
        label="Umbenennen",
        style=discord.ButtonStyle.primary,
        custom_id="temp_voice:rename",
        emoji="\u270f\ufe0f",
        row=0,
    )
    async def rename_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Button-Handler: Modal zum Umbenennen öffnen."""
        # Vorprüfung: Ist der User Owner?
        result = await self._check_owner(interaction)
        if result is None:
            return

        modal = RenameModal()
        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------
    # Button: Limit setzen
    # ------------------------------------------------------------------

    @discord.ui.button(
        label="Limit setzen",
        style=discord.ButtonStyle.primary,
        custom_id="temp_voice:limit",
        emoji="\U0001f465",
        row=0,
    )
    async def limit_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Button-Handler: Modal zum Setzen des Userlimits öffnen."""
        result = await self._check_owner(interaction)
        if result is None:
            return

        modal = LimitModal()
        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------
    # Button: Privat (zu + unsichtbar)
    # ------------------------------------------------------------------

    @discord.ui.button(
        label="Privat",
        style=discord.ButtonStyle.secondary,
        custom_id="temp_voice:private",
        emoji="\U0001f512",
        row=0,
    )
    async def private_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Button-Handler: Channel privat schalten (zu + unsichtbar)."""
        result = await self._check_owner(interaction)
        if result is None:
            return
        member, channel, manager = result

        await manager.set_private(channel)
        await interaction.response.send_message(
            "Channel ist jetzt **privat** — geschlossen und unsichtbar für @everyone.",
            ephemeral=True,
        )
        logger.info(f"Temp-Voice privat: {channel.id} von {member.display_name}")
        await refresh_panel(channel, manager)

    # ------------------------------------------------------------------
    # Button: Öffentlich (offen + sichtbar)
    # ------------------------------------------------------------------

    @discord.ui.button(
        label="Öffentlich",
        style=discord.ButtonStyle.secondary,
        custom_id="temp_voice:public",
        emoji="\U0001f513",
        row=0,
    )
    async def public_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Button-Handler: Channel öffentlich schalten (offen + sichtbar)."""
        result = await self._check_owner(interaction)
        if result is None:
            return
        member, channel, manager = result

        await manager.set_public(channel)
        await interaction.response.send_message(
            "Channel ist jetzt **öffentlich** — offen und sichtbar für alle.",
            ephemeral=True,
        )
        logger.info(f"Temp-Voice öffentlich: {channel.id} von {member.display_name}")
        await refresh_panel(channel, manager)

    # ------------------------------------------------------------------
    # Button: Ownership übertragen
    # ------------------------------------------------------------------

    @discord.ui.button(
        label="Owner übertragen",
        style=discord.ButtonStyle.danger,
        custom_id="temp_voice:transfer",
        emoji="\U0001f91d",
        row=1,
    )
    async def transfer_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
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

    @discord.ui.button(
        label="Bannen",
        style=discord.ButtonStyle.danger,
        custom_id="temp_voice:ban",
        emoji="\U0001f6ab",
        row=1,
    )
    async def ban_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Button-Handler: User-Select zum Bannen anzeigen."""
        result = await self._check_owner(interaction)
        if result is None:
            return
        await interaction.response.send_message(
            "Wähle den zu bannenden User:", view=BanUserView(), ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Button: Entbannen (aus Ban-Liste)
    # ------------------------------------------------------------------

    @discord.ui.button(
        label="Entbannen",
        style=discord.ButtonStyle.secondary,
        custom_id="temp_voice:unban",
        emoji="\U0001f464",
        row=1,
    )
    async def unban_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
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

    @discord.ui.button(
        label="Übernehmen",
        style=discord.ButtonStyle.success,
        custom_id="temp_voice:claim",
        emoji="\U0001f451",
        row=1,
    )
    async def claim_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
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

    @discord.ui.button(
        label="Logs",
        style=discord.ButtonStyle.secondary,
        custom_id="temp_voice:logs",
        emoji="\U0001f4ca",
        row=1,
    )
    async def logs_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
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
            "unban": "\U0001f464", "claim": "\U0001f451",
            "private": "\U0001f512", "public": "\U0001f513",
        }
        lines = []
        for ev in events:
            em = emoji_map.get(ev.get("type", ""), "•")
            uid = ev.get("uid")
            target = channel.guild.get_member(uid) if uid else None
            name = target.display_name if target else f"User {uid}"
            lines.append(f"{em} **{name}** · vor {_format_duration(ev.get('ts'))}")

        embed = discord.Embed(
            title="\U0001f4ca Letzte Events",
            description="\n".join(lines),
            color=0x5865F2,
        )
        await interaction.response.send_message(
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


class BanUserSelect(discord.ui.UserSelect):
    """User-Select zum Bannen eines Users aus dem Temp-Channel."""

    def __init__(self) -> None:
        super().__init__(
            placeholder="Zu bannenden User auswählen...",
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

        await manager.ban_user(channel, target_member)
        await interaction.response.send_message(
            f"**{target_member.display_name}** wurde gebannt und getrennt.",
            ephemeral=True,
        )
        logger.info(f"Temp-Voice Ban: {channel.id} -> {target_member.id}")
        await refresh_panel(channel, manager)


class BanUserView(discord.ui.View):
    """Temporäre View mit dem Ban-User-Select (60s Timeout)."""

    def __init__(self) -> None:
        super().__init__(timeout=60)
        self.add_item(BanUserSelect())


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
            placeholder="Zu entbannenden User auswählen...",
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

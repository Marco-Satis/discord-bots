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

from typing import TYPE_CHECKING

import discord

from utils.logger import get_logger

if TYPE_CHECKING:
    from modules.temp_voice import TempVoiceManager

logger = get_logger("modules.temp_voice_views")


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

    async def _check_owner(
        self,
        interaction: discord.Interaction,
    ) -> tuple[discord.Member, discord.VoiceChannel, "TempVoiceManager"] | None:
        """
        Hilfsmethode: Prüfen ob der User Owner eines Temp-Channels ist.

        Returns:
            Tuple (member, channel, manager) wenn alles OK, sonst None
            (Fehlermeldung wird automatisch gesendet)
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

        owner_id = manager.get_owner(channel.id)
        if owner_id != member.id:
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
    # Button: Sperren / Entsperren
    # ------------------------------------------------------------------

    @discord.ui.button(
        label="Sperren/Entsperren",
        style=discord.ButtonStyle.secondary,
        custom_id="temp_voice:lock_toggle",
        emoji="\U0001f512",
        row=0,
    )
    async def lock_toggle_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Button-Handler: @everyone connect-Berechtigung umschalten."""
        result = await self._check_owner(interaction)
        if result is None:
            return

        member, channel, manager = result

        # Aktuellen Lock-Status prüfen
        # Standard-Berechtigung für @everyone im Channel ermitteln
        everyone_role = channel.guild.default_role
        overwrites = channel.overwrites_for(everyone_role)

        # Toggle: Wenn connect erlaubt oder nicht gesetzt -> sperren
        # Wenn connect gesperrt -> entsperren
        # Nur connect-Berechtigung ändern, alle anderen beibehalten
        if overwrites.connect is False:
            # Entsperren: connect wieder erlauben
            try:
                overwrites.connect = True
                await channel.set_permissions(everyone_role, overwrite=overwrites)
                await interaction.response.send_message(
                    "Channel **entsperrt** — Alle können beitreten.",
                    ephemeral=True,
                )
                logger.info(
                    f"Temp-Voice entsperrt: {channel.id} "
                    f"von {member.display_name}"
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.error(f"Channel entsperren fehlgeschlagen: {e}")
                await interaction.response.send_message(
                    "Channel konnte nicht entsperrt werden.", ephemeral=True
                )
        else:
            # Sperren: connect verweigern
            try:
                overwrites.connect = False
                await channel.set_permissions(everyone_role, overwrite=overwrites)
                await interaction.response.send_message(
                    "Channel **gesperrt** — Nur aktuelle Mitglieder können bleiben.",
                    ephemeral=True,
                )
                logger.info(
                    f"Temp-Voice gesperrt: {channel.id} "
                    f"von {member.display_name}"
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.error(f"Channel sperren fehlgeschlagen: {e}")
                await interaction.response.send_message(
                    "Channel konnte nicht gesperrt werden.", ephemeral=True
                )

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

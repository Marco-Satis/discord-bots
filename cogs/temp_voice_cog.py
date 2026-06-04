"""
Temp Voice Cog — Phase 12a (F17)
Cog für den Admin Bot: Temporaere Voice-Channels mit Join-to-Create.

Wenn ein Mitglied den konfigurierten "Join-to-Create" Voice-Channel
betritt, wird automatisch ein persoenlicher Voice-Channel erstellt.
Der Ersteller (Owner) kann den Channel über interaktive Buttons
steuern (umbenennen, Limit, sperren, Ownership transferieren).
Leere Channels werden nach einer kurzen Verzoegerung automatisch gelöscht.

Commands:
  /tempvoice setup <join_channel> [kategorie]  — Join-to-Create konfigurieren (Admin)
  /tempvoice interface <channel> [entfernen]   — Persistentes Steuer-Panel einrichten (Admin)
  /tempvoice info                              — Aktive Temp-Channels und Setup anzeigen

Features:
  - Join-to-Create: Voice-Channel betreten -> Temp-Channel wird erstellt
  - Persistente Control-View mit Buttons (überlebt Bot-Neustarts)
  - Automatisches Loeschen leerer Channels
  - Ownership-Transfer bei Verlassen des Owners
  - Cleanup beim Bot-Start: Verwaiste leere Channels entfernen
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.temp_voice import TempVoiceManager
from modules.temp_voice_views import (
    TempVoiceControlView,
    build_interface_embed,
    build_panel_embed,
    refresh_panel,
)
from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR
from utils.permissions import admin_only

logger = get_logger("cogs.temp_voice")

# Verzoegerung in Sekunden bevor ein leerer Temp-Channel gelöscht wird
EMPTY_CHANNEL_DELETE_DELAY = 3


class TempVoiceCog(commands.Cog):
    """Temporaere Voice-Channels mit Join-to-Create und interaktiver Steuerung"""

    # Slash-Command-Gruppe
    tempvoice_grp = app_commands.Group(
        name="tempvoice",
        description="Temporaere Voice-Channels verwalten",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.manager = TempVoiceManager(
            data_file=ADMIN_DATA_DIR / "temp_voice.json",
            config_file=ADMIN_DATA_DIR / "temp_voice_config.json",
        )
        # Set zum Tracken von Channels die gerade gelöscht werden
        # (verhindert Race Conditions bei mehreren Leave-Events)
        self._deleting: set[int] = set()

    async def cog_load(self) -> None:
        """Persistente Views beim Laden registrieren und Cleanup ausführen."""
        # Persistente Control-View registrieren
        self.bot.add_view(TempVoiceControlView())
        logger.info("Temp-Voice-Cog geladen, persistente Views registriert")

        # Cleanup verwaister Channels beim Start
        # (wird als Task gestartet, damit cog_load nicht blockiert)
        task = asyncio.create_task(self._startup_cleanup())
        task.add_done_callback(
            lambda t: t.exception() and logger.error(
                f"Startup-Cleanup fehlgeschlagen: {t.exception()}"
            ) if not t.cancelled() and t.exception() else None
        )

    # ==================================================================
    # Startup-Cleanup: Verwaiste leere Channels entfernen
    # ==================================================================

    async def _startup_cleanup(self) -> None:
        """
        Beim Bot-Start verwaiste temporaere Channels aufraeumen.

        Wartet auf bot.wait_until_ready() und prueft dann alle
        registrierten Temp-Channels. Leere oder nicht mehr existierende
        Channels werden entfernt.
        """
        await self.bot.wait_until_ready()
        await asyncio.sleep(2)  # Kurz warten bis Guild-Daten geladen sind

        # Interface-Panel (persistentes Steuer-Panel) sicherstellen
        await self._ensure_interface_panel()

        channels = self.manager.get_all_channels()
        if not channels:
            return

        removed = 0
        for channel_id_str, data in list(channels.items()):
            try:
                channel_id = int(channel_id_str)
            except (ValueError, TypeError):
                self.manager.delete_channel(0)  # Ungueltige ID entfernen
                removed += 1
                continue

            # Channel im Discord suchen
            channel = self.bot.get_channel(channel_id)

            if channel is None:
                # Channel existiert nicht mehr in Discord
                self.manager.delete_channel(channel_id)
                removed += 1
                continue

            if not isinstance(channel, discord.VoiceChannel):
                # Kein Voice-Channel (sollte nicht vorkommen)
                self.manager.delete_channel(channel_id)
                removed += 1
                continue

            # Pruefen ob der Channel leer ist (keine User, nur Bots zaehlen nicht)
            human_members = [m for m in channel.members if not m.bot]
            if not human_members:
                # Leerer Channel — löschen
                try:
                    await channel.delete(
                        reason="Temp-Voice Startup-Cleanup: leerer Channel"
                    )
                except (discord.Forbidden, discord.HTTPException) as e:
                    logger.warning(
                        f"Startup-Cleanup: Channel {channel_id} löschen fehlgeschlagen: {e}"
                    )
                self.manager.delete_channel(channel_id)
                removed += 1

        if removed > 0:
            logger.info(f"Startup-Cleanup: {removed} verwaiste Temp-Voice-Channels entfernt")
        else:
            logger.info(
                f"Startup-Cleanup: Alle {len(channels)} Temp-Voice-Channels sind aktiv"
            )

    # ==================================================================
    # Interface-Kanal: persistentes Steuer-Panel (VOICEPANEL-Style)
    # ==================================================================

    async def _ensure_interface_panel(self) -> None:
        """
        Interface-Panel beim Start sicherstellen.

        Ist ein Interface-Kanal konfiguriert, pruefen ob die Panel-Message noch
        existiert; bei geloeschter/unbekannter Message neu posten. Die Buttons
        funktionieren nach Restart durch die in cog_load registrierte persistente
        View — diese Methode haelt nur die Message am Leben.
        """
        ch_id = self.manager.interface_channel_id
        if not ch_id:
            return
        channel = self.bot.get_channel(ch_id)
        if channel is None:
            # Cache evtl. noch unvollstaendig kurz nach Ready -> einmal fetchen.
            try:
                channel = await self.bot.fetch_channel(ch_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Interface-Kanal {ch_id} nicht abrufbar: {e}")
                return
        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                f"Interface-Kanal {ch_id} nicht gefunden oder kein Text-Channel."
            )
            return

        msg_id = self.manager.interface_message_id
        if msg_id:
            try:
                await channel.fetch_message(msg_id)
                return  # Message existiert -> persistente View greift, fertig
            except discord.NotFound:
                pass  # geloescht -> neu posten
            except discord.HTTPException as e:
                # Transienter API-Fehler: konservativ nicht neu posten (Doppel-Post-
                # Schutz), aber sichtbar loggen statt stumm.
                logger.warning(f"Interface-Message-Check fehlgeschlagen: {e}")
                return

        await self._post_interface_panel(channel)

    async def _post_interface_panel(self, channel: discord.TextChannel) -> bool:
        """Interface-Panel in den Channel posten + Message-ID merken."""
        try:
            msg = await channel.send(
                embed=build_interface_embed(), view=TempVoiceControlView()
            )
            self.manager.set_interface_message(msg.id)
            logger.info(f"Interface-Panel in #{channel.name} gepostet ({msg.id})")
            return True
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Interface-Panel posten fehlgeschlagen: {e}")
            return False

    # ==================================================================
    # Event: Voice State Update
    # ==================================================================

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """
        Reagiert auf Voice-State-Aenderungen:
        1. Join-to-Create: User betritt den konfigurierten Channel
        2. Channel leer: Temp-Channel nach Verzoegerung löschen
        3. Owner verlassen: Ownership an nächstes Mitglied übertragen
        """
        # Bots ignorieren
        if member.bot:
            return

        # --- Join-to-Create: User betritt einen konfigurierten Hub-Channel ---
        # Multi-Hub (C1): is_hub() prueft die gesamte Hub-Liste, nicht nur einen.
        if (
            after.channel is not None
            and isinstance(after.channel, discord.VoiceChannel)
            and self.manager.is_hub(after.channel.id)
        ):
            await self._handle_join_to_create(member, after.channel)
            return

        # --- Join in einen bestehenden Temp-Channel: Slot-Liste aktualisieren ---
        if (
            after.channel is not None
            and after.channel != before.channel
            and isinstance(after.channel, discord.VoiceChannel)
            and self.manager.is_temp_channel(after.channel.id)
        ):
            self.manager.record_join(after.channel.id, member.id)
            await refresh_panel(after.channel, self.manager)

        # --- Channel verlassen: Pruefen ob Temp-Channel leer geworden ist ---
        if before.channel is not None and before.channel != after.channel:
            await self._handle_channel_leave(member, before.channel)

    async def _handle_join_to_create(
        self,
        member: discord.Member,
        join_channel: discord.VoiceChannel,
    ) -> None:
        """
        User hat den Join-to-Create Channel betreten.

        Erstellt einen neuen temporaeren Channel und verschiebt
        den User dorthin. Sendet ausserdem die Control-View als
        Nachricht in einen Textkanal (falls vorhanden).

        Args:
            member: Das Mitglied das beigetreten ist
            join_channel: Der Join-to-Create Channel
        """
        guild = member.guild

        try:
            # Temporaeren Channel erstellen (Hub-Config via join_channel.id)
            temp_channel = await self.manager.create_channel(
                guild, member, hub_id=join_channel.id
            )

            # User in den neuen Channel verschieben
            await member.move_to(temp_channel)

            logger.info(
                f"Join-to-Create: {member.display_name} -> #{temp_channel.name}"
            )

            # Control-Embed mit Buttons senden
            # Versuche es im Text-in-Voice des Channels zu senden
            await self._send_control_panel(temp_channel, member)

        except discord.Forbidden as e:
            logger.error(
                f"Join-to-Create fehlgeschlagen (keine Berechtigung): {e}"
            )
        except discord.HTTPException as e:
            logger.error(f"Join-to-Create fehlgeschlagen (HTTP-Fehler): {e}")

    async def _send_control_panel(
        self,
        channel: discord.VoiceChannel,
        owner: discord.Member,
    ) -> None:
        """
        Kontrollpanel-Embed mit Buttons in den Channel senden.

        Args:
            channel: Der temporaere Voice-Channel
            owner: Der Channel-Owner
        """
        embed = build_panel_embed(channel, self.manager)
        view = TempVoiceControlView()

        try:
            msg = await channel.send(embed=embed, view=view)
            # Panel-Message merken -> Slot-Liste kann bei Join/Leave aktualisiert werden
            self.manager.set_panel_message(channel.id, msg.id)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Control-Panel senden fehlgeschlagen: {e}")

    async def _handle_channel_leave(
        self,
        member: discord.Member,
        channel: discord.abc.GuildChannel,
    ) -> None:
        """
        Ein User hat einen Channel verlassen.

        Prueft ob es ein Temp-Channel ist und ob er jetzt leer ist.
        Falls der Owner gegangen ist und noch andere da sind, wird
        die Ownership übertragen.

        Args:
            member: Das Mitglied das den Channel verlassen hat
            channel: Der verlassene Channel
        """
        if not self.manager.is_temp_channel(channel.id):
            return

        if not isinstance(channel, discord.VoiceChannel):
            return

        # Menschliche Mitglieder im Channel zaehlen
        human_members = [m for m in channel.members if not m.bot]

        if not human_members:
            # Channel ist leer — nach Verzoegerung löschen
            await self._schedule_channel_delete(channel)
            return

        # Leave vermerken (joined_at-Cleanup + Event fuer die Slot-Liste)
        self.manager.record_leave(channel.id, member.id)

        # Pruefen ob der Owner den Channel verlassen hat
        owner_id = self.manager.get_owner(channel.id)
        if owner_id == member.id:
            # Owner ist gegangen — Ownership an nächstes Mitglied übertragen
            new_owner = human_members[0]  # Erstes verbleibendes Mitglied
            self.manager.transfer_ownership(channel.id, new_owner.id)

            # Berechtigungen aktualisieren
            try:
                # Alter Owner: Standard-Berechtigungen
                await channel.set_permissions(
                    member,
                    overwrite=None,  # Individuelle Ueberschreibung entfernen
                )
                # Neuer Owner: Volle Kontrolle
                await channel.set_permissions(
                    new_owner,
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
                logger.warning(
                    f"Berechtigungen beim Owner-Transfer aktualisieren fehlgeschlagen: {e}"
                )

            # Benachrichtigung im Channel
            try:
                await channel.send(
                    f"Der Owner hat den Channel verlassen. "
                    f"**{new_owner.display_name}** ist jetzt der neue Owner."
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

            logger.info(
                f"Owner-Transfer: {member.display_name} hat Channel {channel.id} "
                f"verlassen, neuer Owner: {new_owner.display_name}"
            )

        # Panel aktualisieren (Slot-Liste + ggf. neuer Owner-Crown)
        await refresh_panel(channel, self.manager)

    async def _schedule_channel_delete(
        self,
        channel: discord.VoiceChannel,
    ) -> None:
        """
        Loeschen eines leeren Temp-Channels nach kurzer Verzoegerung einplanen.

        Die Verzoegerung gibt Usern die Moeglichkeit, schnell
        zurückzukommen (z.B. bei einem Disconnect).

        Args:
            channel: Der leere Voice-Channel
        """
        channel_id = channel.id

        # Race-Condition verhindern: Wenn bereits ein Delete geplant ist
        if channel_id in self._deleting:
            return
        self._deleting.add(channel_id)

        try:
            await asyncio.sleep(EMPTY_CHANNEL_DELETE_DELAY)

            # Nochmal prüfen ob der Channel noch existiert und leer ist
            # (User koennten in der Zwischenzeit zurückgekommen sein)
            updated_channel = self.bot.get_channel(channel_id)
            if updated_channel is None:
                # Channel existiert nicht mehr
                self.manager.delete_channel(channel_id)
                return

            if not isinstance(updated_channel, discord.VoiceChannel):
                self.manager.delete_channel(channel_id)
                return

            # Nochmal prüfen ob leer
            human_members = [m for m in updated_channel.members if not m.bot]
            if human_members:
                # Nicht mehr leer — Loeschen abbrechen
                return

            # Channel löschen
            try:
                await updated_channel.delete(
                    reason="Temp-Voice-Channel leer — automatisch gelöscht"
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Temp-Channel {channel_id} löschen fehlgeschlagen: {e}")

            self.manager.delete_channel(channel_id)
            logger.info(f"Temp-Voice-Channel gelöscht (leer): {channel_id}")

        finally:
            self._deleting.discard(channel_id)

    # ==================================================================
    # /tempvoice setup
    # ==================================================================

    @tempvoice_grp.command(
        name="setup",
        description="Join-to-Create Voice-Channel und Kategorie konfigurieren",
    )
    @app_commands.describe(
        join_channel="Voice-Channel der als Join-to-Create Trigger dient",
        kategorie="Kategorie in der temporaere Channels erstellt werden (optional)",
    )
    @admin_only()
    async def tempvoice_setup(
        self,
        interaction: discord.Interaction,
        join_channel: discord.VoiceChannel,
        kategorie: Optional[discord.CategoryChannel] = None,
    ) -> None:
        """Join-to-Create Voice-Channel und optionale Kategorie konfigurieren."""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.",
                ephemeral=True,
            )
            return

        # Kategorie zuerst setzen, damit der Hub-Eintrag sie uebernimmt
        if kategorie:
            self.manager.set_category(kategorie.id)

        # Join-to-Create Channel setzen (registriert ihn auch als Hub)
        self.manager.set_join_channel(join_channel.id)

        # Bestaetigung
        info_lines: list[str] = [
            f"Join-to-Create Channel: {join_channel.mention}",
        ]
        if kategorie:
            info_lines.append(f"Kategorie: {kategorie.name}")
        else:
            current_cat = self.manager.category_id
            if current_cat:
                cat = interaction.guild.get_channel(current_cat)
                if cat:
                    info_lines.append(f"Kategorie: {cat.name} (bestehend)")
            else:
                info_lines.append("Kategorie: Keine (Channels werden ohne Kategorie erstellt)")

        embed = discord.Embed(
            title="Temp-Voice Setup",
            description="\n".join(f"- {line}" for line in info_lines),
            color=0x2ecc71,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Konfiguriert von {interaction.user.display_name}")

        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"Temp-Voice Setup von {interaction.user.display_name}: "
            f"Join-Channel={join_channel.name}"
            + (f", Kategorie={kategorie.name}" if kategorie else "")
        )

    # ==================================================================
    # /tempvoice hubadd / hubremove / hubs  (Multi-Hub, C1)
    # ==================================================================

    @tempvoice_grp.command(
        name="hubadd",
        description="Einen Join-to-Create-Hub hinzufuegen oder aktualisieren",
    )
    @app_commands.describe(
        join_channel="Voice-Channel der als Join-to-Create-Trigger dient",
        kategorie="Kategorie fuer neue Channels dieses Hubs (optional)",
        naming="Namens-Template, Platzhalter: {user} {count} {game}",
        limit="Standard-Userlimit 0-99 (0 = unbegrenzt)",
        privat="Neue Channels direkt privat erstellen",
        spiel="Wert fuer den {game}-Platzhalter (optional)",
    )
    @admin_only()
    async def tempvoice_hubadd(
        self,
        interaction: discord.Interaction,
        join_channel: discord.VoiceChannel,
        kategorie: Optional[discord.CategoryChannel] = None,
        naming: Optional[str] = None,
        limit: Optional[int] = None,
        privat: bool = False,
        spiel: Optional[str] = None,
    ) -> None:
        """Multi-Hub: einen Hub mit eigener Kategorie/Naming/Limit anlegen."""
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True
            )
            return

        ok = self.manager.add_hub(
            join_channel.id,
            category_id=kategorie.id if kategorie else None,
            naming=naming,
            default_limit=limit if limit is not None else 0,
            default_private=privat,
            game=spiel or "",
        )
        if not ok:
            await interaction.followup.send(
                "Hub konnte nicht angelegt werden (ungueltige Daten).",
                ephemeral=True,
            )
            return

        hub = self.manager.get_hub(join_channel.id) or {}
        cat_name = kategorie.name if kategorie else "Keine"
        limit_txt = "Unbegrenzt" if hub.get("default_limit", 0) == 0 else str(hub.get("default_limit"))
        embed = discord.Embed(
            title="Temp-Voice Hub gespeichert",
            description=(
                f"- Hub-Channel: {join_channel.mention}\n"
                f"- Kategorie: {cat_name}\n"
                f"- Naming: `{discord.utils.escape_markdown(hub.get('naming', ''))}`\n"
                f"- Limit: {limit_txt}\n"
                f"- Privat: {'Ja' if hub.get('default_private') else 'Nein'}"
                + (f"\n- Spiel: {discord.utils.escape_markdown(hub.get('game', ''))}"
                   if hub.get("game") else "")
            ),
            color=0x2ecc71,
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(
            f"Temp-Voice Hub von {interaction.user.display_name}: "
            f"{join_channel.name} ({join_channel.id})"
        )

    @tempvoice_grp.command(
        name="hubremove",
        description="Einen Join-to-Create-Hub entfernen",
    )
    @app_commands.describe(join_channel="Hub-Channel der entfernt werden soll")
    @admin_only()
    async def tempvoice_hubremove(
        self,
        interaction: discord.Interaction,
        join_channel: discord.VoiceChannel,
    ) -> None:
        """Multi-Hub: einen Hub aus der Liste entfernen."""
        await interaction.response.defer(ephemeral=True)
        removed = self.manager.remove_hub(join_channel.id)
        if removed:
            await interaction.followup.send(
                f"Hub {join_channel.mention} entfernt. Bestehende Temp-Channels "
                "bleiben bis sie leer sind.",
                ephemeral=True,
            )
            logger.info(
                f"Temp-Voice Hub entfernt von {interaction.user.display_name}: "
                f"{join_channel.id}"
            )
        else:
            await interaction.followup.send(
                f"{join_channel.mention} ist kein konfigurierter Hub.",
                ephemeral=True,
            )

    @tempvoice_grp.command(
        name="hubs",
        description="Alle konfigurierten Join-to-Create-Hubs anzeigen",
    )
    @admin_only()
    async def tempvoice_hubs(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Multi-Hub: alle Hubs mit ihrer Config auflisten."""
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True
            )
            return

        guild = interaction.guild
        hubs = self.manager.get_hubs()
        embed = discord.Embed(
            title="Temp-Voice Hubs",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        if not hubs:
            embed.description = (
                "Keine Hubs konfiguriert. Lege einen an mit `/tempvoice hubadd` "
                "oder `/tempvoice setup`."
            )
        else:
            for hub in hubs[:25]:  # Embed-Field-Limit
                ch = guild.get_channel(hub["hub_id"])
                ch_ref = ch.mention if ch else f"ID {hub['hub_id']} (nicht gefunden)"
                cat = guild.get_channel(hub["category_id"]) if hub["category_id"] else None
                cat_name = cat.name if cat else "Keine"
                limit_txt = "∞" if hub["default_limit"] == 0 else str(hub["default_limit"])
                embed.add_field(
                    name=ch_ref,
                    value=(
                        f"Kategorie: {cat_name}\n"
                        f"Naming: `{discord.utils.escape_markdown(hub['naming'])}`\n"
                        f"Limit: {limit_txt} · Privat: {'Ja' if hub['default_private'] else 'Nein'}"
                        + (f" · Spiel: {discord.utils.escape_markdown(hub['game'])}"
                           if hub["game"] else "")
                    ),
                    inline=False,
                )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # /tempvoice interface
    # ==================================================================

    @tempvoice_grp.command(
        name="interface",
        description="Persistentes Steuer-Panel in einem Text-Channel einrichten",
    )
    @app_commands.describe(
        channel="Text-Channel für das persistente Steuer-Panel",
        entfernen="True = Interface-Kanal-Konfiguration entfernen",
    )
    @admin_only()
    async def tempvoice_interface(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        entfernen: bool = False,
    ) -> None:
        """Interface-Kanal (persistentes Steuer-Panel) setzen oder entfernen."""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True
            )
            return

        if entfernen:
            self.manager.clear_interface()
            await interaction.followup.send(
                "Interface-Kanal entfernt. Ein bereits gepostetes Panel bleibt "
                "stehen (kann manuell gelöscht werden).",
                ephemeral=True,
            )
            return

        if channel is None:
            await interaction.followup.send(
                "Bitte einen Text-Channel angeben (oder `entfernen:True`).",
                ephemeral=True,
            )
            return

        self.manager.set_interface_channel(channel.id)
        posted = await self._post_interface_panel(channel)
        if posted:
            await interaction.followup.send(
                f"Interface-Panel in {channel.mention} eingerichtet. User steuern "
                "darüber den Channel in dem sie gerade sind.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"Interface-Kanal gesetzt, aber Posten in {channel.mention} schlug "
                "fehl — Bot-Berechtigung (Nachrichten senden) prüfen.",
                ephemeral=True,
            )

        logger.info(
            f"Temp-Voice Interface-Kanal von {interaction.user.display_name}: "
            f"#{channel.name}"
        )

    # ==================================================================
    # /tempvoice info
    # ==================================================================

    @tempvoice_grp.command(
        name="info",
        description="Aktuelles Setup und aktive temporaere Channels anzeigen",
    )
    @admin_only()
    async def tempvoice_info(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Setup-Informationen und aktive Temp-Channels als Embed anzeigen."""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        config = self.manager.config

        # Setup-Informationen
        join_id = config.get("join_channel_id")
        cat_id = config.get("category_id")
        default_limit = config.get("default_limit", 0)
        afk_timeout = config.get("afk_timeout_minutes", 5)

        # Channel/Kategorie-Namen aufloesen
        join_ref = "Nicht konfiguriert"
        if join_id:
            join_ch = guild.get_channel(join_id)
            join_ref = join_ch.mention if join_ch else f"ID: {join_id} (nicht gefunden)"

        cat_ref = "Nicht konfiguriert"
        if cat_id:
            cat_ch = guild.get_channel(cat_id)
            cat_ref = cat_ch.name if cat_ch else f"ID: {cat_id} (nicht gefunden)"

        iface_id = config.get("interface_channel_id")
        iface_ref = "Nicht konfiguriert"
        if iface_id:
            iface_ch = guild.get_channel(iface_id)
            iface_ref = (
                iface_ch.mention if iface_ch else f"ID: {iface_id} (nicht gefunden)"
            )

        limit_text = "Unbegrenzt" if default_limit == 0 else str(default_limit)
        hub_count = len(self.manager.get_hubs())

        embed = discord.Embed(
            title="Temp-Voice Informationen",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="Konfiguration",
            value=(
                f"**Hubs (Multi-Hub):** {hub_count} konfiguriert "
                "(Details: `/tempvoice hubs`)\n"
                f"**Join-to-Create (Legacy):** {join_ref}\n"
                f"**Kategorie:** {cat_ref}\n"
                f"**Interface-Kanal:** {iface_ref}\n"
                f"**Standard-Limit:** {limit_text}\n"
                f"**AFK-Timeout:** {afk_timeout} Minuten"
            ),
            inline=False,
        )

        # Aktive Channels auflisten
        channels = self.manager.get_all_channels()

        if channels:
            channel_lines: list[str] = []
            for cid_str, data in channels.items():
                cid = int(cid_str)
                ch = guild.get_channel(cid)
                owner_id = data.get("owner_id", 0)
                name = data.get("name", "?")
                user_limit = data.get("user_limit", 0)

                if ch and isinstance(ch, discord.VoiceChannel):
                    member_count = len([m for m in ch.members if not m.bot])
                    limit_info = f"/{user_limit}" if user_limit > 0 else ""
                    channel_lines.append(
                        f"**{ch.mention}** — Owner: <@{owner_id}> "
                        f"({member_count}{limit_info} User)"
                    )
                else:
                    channel_lines.append(
                        f"**{name}** (ID: {cid}) — "
                        f"Owner: <@{owner_id}> (Channel nicht gefunden)"
                    )

            # Maximal 15 Channels anzeigen (Embed-Limit)
            display_lines = channel_lines[:15]
            if len(channel_lines) > 15:
                display_lines.append(
                    f"... und {len(channel_lines) - 15} weitere"
                )

            embed.add_field(
                name=f"Aktive Channels ({len(channels)})",
                value="\n".join(display_lines),
                inline=False,
            )
        else:
            embed.add_field(
                name="Aktive Channels",
                value="Keine temporaeren Channels aktiv.",
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung für alle Commands in dieser Cog."""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung für diesen Befehl.", ephemeral=True
                )
            return

        cmd_name = interaction.command.name if interaction.command else "unknown"
        logger.error(f"Command error in {cmd_name}: {error}", exc_info=True)

        try:
            msg = f"Ein Fehler ist aufgetreten: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")


async def setup(bot: commands.Bot) -> None:
    """Cog zum Bot hinzufuegen."""
    await bot.add_cog(TempVoiceCog(bot))

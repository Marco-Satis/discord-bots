"""
Server Backup Cog — Phase 12e (F19)
Cog für den Admin Bot: Discord-Server-Struktur sichern und wiederherstellen.

Erstellt vollstaendige Snapshots der Server-Struktur (Channels, Rollen,
Einstellungen, Emojis) und ermöglicht Vergleich und Wiederherstellung.

Command-Struktur (app_commands.Group):
  /server backup create                        — Owner: Backup erstellen
  /server backup list                          — Admin: Alle Backups anzeigen
  /server backup info <backup_id>              — Admin: Backup-Details
  /server backup compare <backup_id>           — Admin: Mit aktuellem Server vergleichen
  /server backup restore <backup_id> [modus]   — Owner: Wiederherstellen (Bestaetigung!)
  /server backup delete <backup_id>            — Owner: Backup löschen
  /server backup auto [intervall_tage]         — Owner: Auto-Backup konfigurieren

Interaktive Elemente:
  - Restore-Bestaetigung via Button (gefaehrliche Aktion)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.server_backup import ServerBackupManager
from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR
from utils.permissions import admin_only, owner_only

logger = get_logger("cogs.server_backup")

# Konfigurationsdatei für Auto-Backup
AUTO_BACKUP_CONFIG = ADMIN_DATA_DIR / "server_backup_auto.json"

# Modus-Auswahl für Restore-Command
_RESTORE_MODE_CHOICES = [
    app_commands.Choice(name="Vollstaendig (gefaehrlich!)", value="full"),
    app_commands.Choice(name="Nur Rollen", value="roles_only"),
    app_commands.Choice(name="Nur Channels", value="channels_only"),
    app_commands.Choice(name="Nur Fehlendes hinzufuegen", value="add_missing"),
]

# Modus-Beschreibungen für die Info-Anzeige
_MODE_DESCRIPTIONS = {
    "full": "Alles wiederherstellen (loescht nicht-vorhandene Elemente!)",
    "roles_only": "Nur Rollen erstellen/aktualisieren",
    "channels_only": "Nur Channels und Kategorien erstellen",
    "add_missing": "Nur fehlende Channels und Rollen hinzufuegen (sicher)",
}


# ======================================================================
# Bestaetigung-Views für gefaehrliche Aktionen
# ======================================================================

class RestoreConfirmView(discord.ui.View):
    """
    Bestaetigung-View für die Backup-Wiederherstellung.

    Zeigt Bestaetigen/Abbrechen-Buttons an. Der Timeout betraegt 60 Sekunden.
    """

    def __init__(
        self,
        cog: ServerBackupCog,
        guild: discord.Guild,
        backup_id: str,
        mode: str,
        user_id: int,
    ) -> None:
        super().__init__(timeout=60.0)
        self.cog = cog
        self.guild = guild
        self.backup_id = backup_id
        self.mode = mode
        self.user_id = user_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Nur der urspruengliche User darf die Buttons bedienen"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Nur der Ersteller dieser Aktion kann bestaetigen.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Wiederherstellen",
        style=discord.ButtonStyle.danger,
    )
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Wiederherstellung bestaetigt — ausführen"""
        self.confirmed = True

        # Buttons deaktivieren
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        # Wiederherstellung starten
        await interaction.followup.send(
            f"Wiederherstellung gestartet (Modus: **{self.mode}**)...\n"
            f"Dies kann einige Sekunden dauern.",
            ephemeral=True,
        )

        try:
            results = await self.cog.backup_mgr.restore_backup(
                self.guild, self.backup_id, mode=self.mode
            )
            embed = self.cog._build_restore_result_embed(results)
            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(
                f"Backup {self.backup_id} wiederhergestellt von User {self.user_id} "
                f"(Modus: {self.mode})"
            )
        except Exception as e:
            logger.error(
                f"Restore fehlgeschlagen ({self.backup_id}): {e}", exc_info=True
            )
            await interaction.followup.send(
                f"Fehler bei der Wiederherstellung: {e}",
                ephemeral=True,
            )

        self.stop()

    @discord.ui.button(
        label="Abbrechen",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Wiederherstellung abgebrochen"""
        self.confirmed = False

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="Wiederherstellung abgebrochen.",
            view=self,
        )

        self.stop()

    async def on_timeout(self) -> None:
        """Timeout — Buttons deaktivieren"""
        for child in self.children:
            child.disabled = True


# ======================================================================
# Server Backup Cog
# ======================================================================

class ServerBackupCog(commands.Cog):
    """
    Server-Backup-System für den Admin Bot.

    Ermöglicht vollstaendige Snapshots der Discord-Server-Struktur
    (Channels, Rollen, Einstellungen) und deren Wiederherstellung.
    """

    # Slash-Command-Gruppen: /server backup <subcommand>
    server_grp = app_commands.Group(
        name="server",
        description="Server-Verwaltung",
    )
    backup_grp = app_commands.Group(
        name="backup",
        parent=server_grp,
        description="Server-Struktur-Backups verwalten",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.backup_mgr = ServerBackupManager()
        self._auto_config: dict = self._load_auto_config()
        logger.info("ServerBackupCog initialisiert")

    async def cog_load(self) -> None:
        """Auto-Backup-Task starten wenn konfiguriert"""
        if self._auto_config.get("enabled", False):
            interval = self._auto_config.get("interval_days", 7)
            self._start_auto_backup(interval)
            logger.info(f"Auto-Backup-Task gestartet (alle {interval} Tage)")

    async def cog_unload(self) -> None:
        """Background-Tasks stoppen"""
        if self.auto_backup_task.is_running():
            self.auto_backup_task.cancel()
            logger.info("Auto-Backup-Task gestoppt")

    # ------------------------------------------------------------------
    # Auto-Backup Konfiguration
    # ------------------------------------------------------------------

    def _load_auto_config(self) -> dict:
        """Auto-Backup-Konfiguration von Disk laden"""
        try:
            if AUTO_BACKUP_CONFIG.exists():
                with open(AUTO_BACKUP_CONFIG, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Auto-Backup-Config laden fehlgeschlagen: {e}")
        return {"enabled": False, "interval_days": 7, "guild_id": 0}

    def _save_auto_config(self) -> None:
        """Auto-Backup-Konfiguration auf Disk speichern"""
        try:
            ADMIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(AUTO_BACKUP_CONFIG, "w", encoding="utf-8") as f:
                json.dump(self._auto_config, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Auto-Backup-Config speichern fehlgeschlagen: {e}")

    def _start_auto_backup(self, interval_days: int) -> None:
        """Auto-Backup-Task mit neuem Intervall starten/neustarten"""
        if self.auto_backup_task.is_running():
            self.auto_backup_task.cancel()

        # Intervall setzen (in Stunden, da tasks.loop hours nutzt)
        self.auto_backup_task.change_interval(hours=interval_days * 24)
        self.auto_backup_task.start()

    @tasks.loop(hours=168)  # Standard: 7 Tage (wird dynamisch geändert)
    async def auto_backup_task(self) -> None:
        """Background-Task: Automatisches Backup erstellen"""
        guild_id = self._auto_config.get("guild_id", 0)
        if not guild_id:
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            logger.warning(
                f"Auto-Backup: Guild {guild_id} nicht gefunden"
            )
            return

        try:
            backup = await self.backup_mgr.create_backup(
                guild, created_by_id=self.bot.user.id
            )
            logger.info(
                f"Auto-Backup erstellt: {backup['backup_id']} "
                f"für '{guild.name}'"
            )
        except Exception as e:
            logger.error(f"Auto-Backup fehlgeschlagen: {e}", exc_info=True)

    @auto_backup_task.before_loop
    async def before_auto_backup(self) -> None:
        """Warten bis der Bot bereit ist"""
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Hilfs-Methoden für Embeds
    # ------------------------------------------------------------------

    @staticmethod
    def _build_restore_result_embed(results: dict) -> discord.Embed:
        """Ergebnis der Wiederherstellung als Embed formatieren"""
        success = results.get("success", False)
        mode = results.get("mode", "unbekannt")

        embed = discord.Embed(
            title="Wiederherstellung abgeschlossen" if success
            else "Wiederherstellung fehlgeschlagen",
            color=0x2ecc71 if success else 0xe74c3c,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(name="Modus", value=mode, inline=True)

        # Rollen
        roles_info = (
            f"Erstellt: {results.get('roles_created', 0)}\n"
            f"Geloescht: {results.get('roles_deleted', 0)}"
        )
        embed.add_field(name="Rollen", value=roles_info, inline=True)

        # Channels
        channels_info = (
            f"Erstellt: {results.get('channels_created', 0)}\n"
            f"Geloescht: {results.get('channels_deleted', 0)}\n"
            f"Kategorien erstellt: {results.get('categories_created', 0)}"
        )
        embed.add_field(name="Channels", value=channels_info, inline=True)

        # Einstellungen
        settings_updated = results.get("settings_updated", [])
        if settings_updated:
            embed.add_field(
                name="Einstellungen aktualisiert",
                value="\n".join(f"- {s}" for s in settings_updated[:10]),
                inline=False,
            )

        # Fehler
        all_errors: list[str] = []
        all_errors.extend(results.get("roles_errors", []))
        all_errors.extend(results.get("channels_errors", []))
        all_errors.extend(results.get("settings_errors", []))

        if all_errors:
            error_text = "\n".join(f"- {e}" for e in all_errors[:10])
            if len(all_errors) > 10:
                error_text += f"\n... und {len(all_errors) - 10} weitere Fehler"
            embed.add_field(
                name="Fehler",
                value=error_text[:1024],
                inline=False,
            )

        if results.get("error"):
            embed.add_field(
                name="Kritischer Fehler",
                value=str(results["error"])[:1024],
                inline=False,
            )

        return embed

    # ==================================================================
    # /server backup create — Owner only
    # ==================================================================

    @backup_grp.command(
        name="create",
        description="Vollstaendiges Server-Backup erstellen (Owner)",
    )
    @owner_only()
    async def backup_create(self, interaction: discord.Interaction) -> None:
        """Vollstaendigen Server-Snapshot erstellen"""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.",
                ephemeral=True,
            )
            return

        try:
            backup = await self.backup_mgr.create_backup(
                interaction.guild, created_by_id=interaction.user.id
            )
        except Exception as e:
            logger.error(f"Backup erstellen fehlgeschlagen: {e}", exc_info=True)
            await interaction.followup.send(
                f"Backup erstellen fehlgeschlagen: {e}",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Server-Backup erstellt",
            description=f"Backup-ID: `{backup['backup_id']}`",
            color=0x2ecc71,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Server",
            value=backup["guild_name"],
            inline=True,
        )
        embed.add_field(
            name="Kategorien",
            value=str(len(backup.get("categories", []))),
            inline=True,
        )
        embed.add_field(
            name="Channels",
            value=str(len(backup.get("channels", []))),
            inline=True,
        )
        embed.add_field(
            name="Rollen",
            value=str(len(backup.get("roles", []))),
            inline=True,
        )
        embed.add_field(
            name="Emojis",
            value=str(len(backup.get("emojis", []))),
            inline=True,
        )
        embed.set_footer(
            text=f"Erstellt von {interaction.user.display_name}"
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"Backup {backup['backup_id']} erstellt von "
            f"{interaction.user.display_name}"
        )

    # ==================================================================
    # /server backup list — Admin
    # ==================================================================

    @backup_grp.command(
        name="list",
        description="Alle Server-Backups anzeigen (Admin)",
    )
    @admin_only()
    async def backup_list(self, interaction: discord.Interaction) -> None:
        """Alle Backups für diesen Server auflisten"""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.",
                ephemeral=True,
            )
            return

        backups = await self.backup_mgr.list_backups(interaction.guild.id)

        if not backups:
            await interaction.followup.send(
                "Keine Backups für diesen Server vorhanden.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Server-Backups ({len(backups)})",
            description="Alle gespeicherten Server-Struktur-Backups:",
            color=0x3498db,
            timestamp=datetime.now(timezone.utc),
        )

        for backup in backups[:15]:  # Maximal 15 im Embed
            backup_id = backup["backup_id"]
            created_by = backup.get("created_by", 0)

            # Datum formatieren
            created_at = backup.get("created_at", "")
            try:
                dt = datetime.fromisoformat(created_at)
                date_str = dt.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                date_str = created_at

            # Zusammenfassung
            summary = (
                f"**Erstellt:** {date_str}\n"
                f"**Von:** <@{created_by}>\n"
                f"**Inhalt:** {backup.get('categories_count', 0)} Kategorien, "
                f"{backup.get('channels_count', 0)} Channels, "
                f"{backup.get('roles_count', 0)} Rollen, "
                f"{backup.get('emojis_count', 0)} Emojis"
            )

            embed.add_field(
                name=f"ID: `{backup_id}`",
                value=summary,
                inline=False,
            )

        if len(backups) > 15:
            embed.set_footer(
                text=f"... und {len(backups) - 15} weitere Backups"
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # /server backup info <backup_id> — Admin
    # ==================================================================

    @backup_grp.command(
        name="info",
        description="Details eines bestimmten Backups anzeigen (Admin)",
    )
    @app_commands.describe(backup_id="Die eindeutige Backup-ID")
    @admin_only()
    async def backup_info(
        self,
        interaction: discord.Interaction,
        backup_id: str,
    ) -> None:
        """Detaillierte Informationen zu einem Backup anzeigen"""
        await interaction.response.defer(ephemeral=True)

        backup = await self.backup_mgr.get_backup(backup_id)
        if not backup or backup.get("guild_id") != interaction.guild.id:
            await interaction.followup.send(
                f"Backup `{backup_id}` nicht gefunden.",
                ephemeral=True,
            )
            return

        # Datum formatieren
        created_at = backup.get("created_at", "")
        try:
            dt = datetime.fromisoformat(created_at)
            date_str = dt.strftime("%d.%m.%Y %H:%M:%S")
        except (ValueError, TypeError):
            date_str = created_at

        embed = discord.Embed(
            title=f"Backup-Details: `{backup_id}`",
            color=0x3498db,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(name="Server", value=backup.get("guild_name", "Unbekannt"), inline=True)
        embed.add_field(name="Erstellt am", value=date_str, inline=True)
        embed.add_field(name="Erstellt von", value=f"<@{backup.get('created_by', 0)}>", inline=True)

        # Kategorien auflisten
        categories = backup.get("categories", [])
        if categories:
            cat_list = "\n".join(
                f"- {c['name']} (Pos. {c['position']})"
                for c in sorted(categories, key=lambda x: x["position"])[:15]
            )
            if len(categories) > 15:
                cat_list += f"\n... und {len(categories) - 15} weitere"
            embed.add_field(
                name=f"Kategorien ({len(categories)})",
                value=cat_list[:1024],
                inline=False,
            )

        # Channels auflisten
        channels = backup.get("channels", [])
        if channels:
            ch_list = "\n".join(
                f"- {c['name']} ({c.get('type', 'text')})"
                for c in sorted(channels, key=lambda x: x.get("position", 0))[:15]
            )
            if len(channels) > 15:
                ch_list += f"\n... und {len(channels) - 15} weitere"
            embed.add_field(
                name=f"Channels ({len(channels)})",
                value=ch_list[:1024],
                inline=False,
            )

        # Rollen auflisten
        roles = backup.get("roles", [])
        if roles:
            role_list = "\n".join(
                f"- {r['name']}"
                for r in sorted(roles, key=lambda x: x.get("position", 0), reverse=True)[:15]
            )
            if len(roles) > 15:
                role_list += f"\n... und {len(roles) - 15} weitere"
            embed.add_field(
                name=f"Rollen ({len(roles)})",
                value=role_list[:1024],
                inline=False,
            )

        # Emojis
        emojis = backup.get("emojis", [])
        if emojis:
            emoji_list = ", ".join(e["name"] for e in emojis[:30])
            if len(emojis) > 30:
                emoji_list += f" ... und {len(emojis) - 30} weitere"
            embed.add_field(
                name=f"Emojis ({len(emojis)})",
                value=emoji_list[:1024],
                inline=False,
            )

        # Einstellungen
        settings = backup.get("settings", {})
        if settings:
            settings_text = (
                f"**Verifizierung:** {settings.get('verification_level', 'N/A')}\n"
                f"**Benachrichtigungen:** {settings.get('default_notifications', 'N/A')}\n"
                f"**AFK-Channel:** {settings.get('afk_channel_name') or 'Keiner'}\n"
                f"**AFK-Timeout:** {settings.get('afk_timeout', 0)}s\n"
                f"**System-Channel:** {settings.get('system_channel_name') or 'Keiner'}"
            )
            embed.add_field(
                name="Einstellungen",
                value=settings_text,
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # /server backup compare <backup_id> — Admin
    # ==================================================================

    @backup_grp.command(
        name="compare",
        description="Backup mit aktuellem Server vergleichen (Admin)",
    )
    @app_commands.describe(backup_id="Die eindeutige Backup-ID")
    @admin_only()
    async def backup_compare(
        self,
        interaction: discord.Interaction,
        backup_id: str,
    ) -> None:
        """Aktuellen Server-Zustand mit einem Backup vergleichen"""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.",
                ephemeral=True,
            )
            return

        # Guild-ID prüfen bevor Vergleich
        backup = await self.backup_mgr.get_backup(backup_id)
        if not backup or backup.get("guild_id") != interaction.guild.id:
            await interaction.followup.send(
                f"Backup `{backup_id}` nicht gefunden.",
                ephemeral=True,
            )
            return

        result = await self.backup_mgr.compare_backup(interaction.guild, backup_id)

        if not result.get("found"):
            await interaction.followup.send(
                f"Backup `{backup_id}` nicht gefunden.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Vergleich mit Backup `{backup_id}`",
            description="Unterschiede zwischen dem Backup und dem aktuellen Server:",
            color=0xf39c12,
            timestamp=datetime.now(timezone.utc),
        )

        # Channel-Aenderungen
        ch = result.get("channels", {})
        ch_added = ch.get("added", [])
        ch_removed = ch.get("removed", [])
        ch_changed = ch.get("changed", [])

        if ch_added or ch_removed or ch_changed:
            ch_text_parts: list[str] = []
            if ch_added:
                added_str = "\n".join(f"+ {c}" for c in ch_added[:5])
                if len(ch_added) > 5:
                    added_str += f"\n... +{len(ch_added) - 5} weitere"
                ch_text_parts.append(f"**Neu ({len(ch_added)}):**\n{added_str}")
            if ch_removed:
                removed_str = "\n".join(f"- {c}" for c in ch_removed[:5])
                if len(ch_removed) > 5:
                    removed_str += f"\n... +{len(ch_removed) - 5} weitere"
                ch_text_parts.append(f"**Entfernt ({len(ch_removed)}):**\n{removed_str}")
            if ch_changed:
                changed_str = "\n".join(f"~ {c}" for c in ch_changed[:5])
                if len(ch_changed) > 5:
                    changed_str += f"\n... +{len(ch_changed) - 5} weitere"
                ch_text_parts.append(f"**Geaendert ({len(ch_changed)}):**\n{changed_str}")

            embed.add_field(
                name="Channels",
                value="\n".join(ch_text_parts)[:1024],
                inline=False,
            )
        else:
            embed.add_field(name="Channels", value="Keine Aenderungen", inline=False)

        # Rollen-Aenderungen
        ro = result.get("roles", {})
        ro_added = ro.get("added", [])
        ro_removed = ro.get("removed", [])
        ro_changed = ro.get("changed", [])

        if ro_added or ro_removed or ro_changed:
            ro_text_parts: list[str] = []
            if ro_added:
                added_str = "\n".join(f"+ {r}" for r in ro_added[:5])
                if len(ro_added) > 5:
                    added_str += f"\n... +{len(ro_added) - 5} weitere"
                ro_text_parts.append(f"**Neu ({len(ro_added)}):**\n{added_str}")
            if ro_removed:
                removed_str = "\n".join(f"- {r}" for r in ro_removed[:5])
                if len(ro_removed) > 5:
                    removed_str += f"\n... +{len(ro_removed) - 5} weitere"
                ro_text_parts.append(f"**Entfernt ({len(ro_removed)}):**\n{removed_str}")
            if ro_changed:
                changed_str = "\n".join(f"~ {r}" for r in ro_changed[:5])
                if len(ro_changed) > 5:
                    changed_str += f"\n... +{len(ro_changed) - 5} weitere"
                ro_text_parts.append(f"**Geaendert ({len(ro_changed)}):**\n{changed_str}")

            embed.add_field(
                name="Rollen",
                value="\n".join(ro_text_parts)[:1024],
                inline=False,
            )
        else:
            embed.add_field(name="Rollen", value="Keine Aenderungen", inline=False)

        # Einstellungs-Aenderungen
        se = result.get("settings", {})
        se_changed = se.get("changed", [])

        if se_changed:
            se_text = "\n".join(f"~ {s}" for s in se_changed[:10])
            embed.add_field(
                name="Einstellungen",
                value=se_text[:1024],
                inline=False,
            )
        else:
            embed.add_field(name="Einstellungen", value="Keine Aenderungen", inline=False)

        # Zusammenfassung
        total_changes = (
            len(ch_added) + len(ch_removed) + len(ch_changed)
            + len(ro_added) + len(ro_removed) + len(ro_changed)
            + len(se_changed)
        )
        embed.set_footer(text=f"Gesamt: {total_changes} Unterschiede")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # /server backup restore <backup_id> [modus] — Owner only
    # ==================================================================

    @backup_grp.command(
        name="restore",
        description="Server aus Backup wiederherstellen (Owner, gefaehrlich!)",
    )
    @app_commands.describe(
        backup_id="Die eindeutige Backup-ID",
        modus="Wiederherstellungsmodus (Standard: add_missing)",
    )
    @app_commands.choices(modus=_RESTORE_MODE_CHOICES)
    @owner_only()
    async def backup_restore(
        self,
        interaction: discord.Interaction,
        backup_id: str,
        modus: app_commands.Choice[str] = None,
    ) -> None:
        """Server-Struktur aus einem Backup wiederherstellen (mit Bestaetigung)"""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.",
                ephemeral=True,
            )
            return

        # Backup prüfen
        backup = await self.backup_mgr.get_backup(backup_id)
        if not backup:
            await interaction.followup.send(
                f"Backup `{backup_id}` nicht gefunden.",
                ephemeral=True,
            )
            return

        # Guild-ID prüfen
        if backup.get("guild_id") != interaction.guild.id:
            await interaction.followup.send(
                "Dieses Backup gehoert zu einem anderen Server.",
                ephemeral=True,
            )
            return

        mode = modus.value if modus else "add_missing"
        mode_desc = _MODE_DESCRIPTIONS.get(mode, mode)

        # Bestaetigung-Embed
        embed = discord.Embed(
            title="Backup-Wiederherstellung bestaetigen",
            description=(
                f"**Backup-ID:** `{backup_id}`\n"
                f"**Server:** {backup.get('guild_name', 'Unbekannt')}\n"
                f"**Erstellt am:** {backup.get('created_at', 'Unbekannt')}\n\n"
                f"**Modus:** {mode}\n"
                f"**Beschreibung:** {mode_desc}\n\n"
                f"**Inhalt:**\n"
                f"- {len(backup.get('categories', []))} Kategorien\n"
                f"- {len(backup.get('channels', []))} Channels\n"
                f"- {len(backup.get('roles', []))} Rollen\n"
                f"- {len(backup.get('emojis', []))} Emojis"
            ),
            color=0xe74c3c if mode == "full" else 0xf39c12,
            timestamp=datetime.now(timezone.utc),
        )

        if mode == "full":
            embed.add_field(
                name="WARNUNG",
                value=(
                    "Der Modus **full** ist destruktiv!\n"
                    "Channels und Rollen die nicht im Backup sind werden **gelöscht**.\n"
                    "Diese Aktion kann nicht rueckgaengig gemacht werden!"
                ),
                inline=False,
            )

        embed.set_footer(
            text="Klicke 'Wiederherstellen' zum Bestaetigen oder 'Abbrechen'"
        )

        # Bestaetigung-View
        view = RestoreConfirmView(
            cog=self,
            guild=interaction.guild,
            backup_id=backup_id,
            mode=mode,
            user_id=interaction.user.id,
        )

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ==================================================================
    # /server backup delete <backup_id> — Owner only
    # ==================================================================

    @backup_grp.command(
        name="delete",
        description="Ein Server-Backup löschen (Owner)",
    )
    @app_commands.describe(backup_id="Die eindeutige Backup-ID")
    @owner_only()
    async def backup_delete(
        self,
        interaction: discord.Interaction,
        backup_id: str,
    ) -> None:
        """Backup-Datei endgueltig löschen"""
        await interaction.response.defer(ephemeral=True)

        # Guild-ID prüfen: Nur Backups des eigenen Servers löschen
        backup = await self.backup_mgr.get_backup(backup_id)
        if not backup or backup.get("guild_id") != interaction.guild.id:
            await interaction.followup.send(
                f"Backup `{backup_id}` nicht gefunden.",
                ephemeral=True,
            )
            return

        deleted = await self.backup_mgr.delete_backup(backup_id)

        if deleted:
            embed = discord.Embed(
                title="Backup gelöscht",
                description=f"Backup `{backup_id}` wurde endgueltig gelöscht.",
                color=0x2ecc71,
                timestamp=datetime.now(timezone.utc),
            )
            logger.info(
                f"Backup {backup_id} gelöscht von {interaction.user.display_name}"
            )
        else:
            embed = discord.Embed(
                title="Fehler",
                description=f"Backup `{backup_id}` nicht gefunden.",
                color=0xe74c3c,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # /server backup auto [intervall_tage] — Owner only
    # ==================================================================

    @backup_grp.command(
        name="auto",
        description="Automatische Backups konfigurieren (Owner)",
    )
    @app_commands.describe(
        intervall_tage="Intervall in Tagen (0 = deaktivieren, leer = Status anzeigen)",
    )
    @owner_only()
    async def backup_auto(
        self,
        interaction: discord.Interaction,
        intervall_tage: Optional[int] = None,
    ) -> None:
        """Automatische Backups aktivieren, deaktivieren oder Status anzeigen"""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.",
                ephemeral=True,
            )
            return

        # Nur Status anzeigen wenn kein Intervall angegeben
        if intervall_tage is None:
            enabled = self._auto_config.get("enabled", False)
            interval = self._auto_config.get("interval_days", 7)
            running = self.auto_backup_task.is_running()

            embed = discord.Embed(
                title="Auto-Backup Status",
                color=0x2ecc71 if enabled else 0x95a5a6,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(
                name="Status",
                value="Aktiviert" if enabled else "Deaktiviert",
                inline=True,
            )
            embed.add_field(
                name="Intervall",
                value=f"{interval} Tag(e)" if enabled else "N/A",
                inline=True,
            )
            embed.add_field(
                name="Task läuft",
                value="Ja" if running else "Nein",
                inline=True,
            )

            if running and self.auto_backup_task.next_iteration:
                next_run = self.auto_backup_task.next_iteration
                embed.add_field(
                    name="Naechstes Backup",
                    value=discord.utils.format_dt(next_run, "R"),
                    inline=True,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Deaktivieren
        if intervall_tage <= 0:
            self._auto_config["enabled"] = False
            self._save_auto_config()

            if self.auto_backup_task.is_running():
                self.auto_backup_task.cancel()

            embed = discord.Embed(
                title="Auto-Backup deaktiviert",
                description="Automatische Server-Backups wurden deaktiviert.",
                color=0x95a5a6,
                timestamp=datetime.now(timezone.utc),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(
                f"Auto-Backup deaktiviert von {interaction.user.display_name}"
            )
            return

        # Aktivieren / Intervall ändern
        if intervall_tage > 365:
            await interaction.followup.send(
                "Maximales Intervall: 365 Tage.",
                ephemeral=True,
            )
            return

        self._auto_config["enabled"] = True
        self._auto_config["interval_days"] = intervall_tage
        self._auto_config["guild_id"] = interaction.guild.id
        self._save_auto_config()

        self._start_auto_backup(intervall_tage)

        embed = discord.Embed(
            title="Auto-Backup aktiviert",
            description=(
                f"Automatische Server-Backups alle **{intervall_tage} Tag(e)**.\n"
                f"Server: **{interaction.guild.name}**"
            ),
            color=0x2ecc71,
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"Auto-Backup aktiviert: alle {intervall_tage} Tage "
            f"(von {interaction.user.display_name})"
        )

    # ==================================================================
    # Autocomplete für backup_id
    # ==================================================================

    async def _backup_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete für Backup-IDs — zeigt verfügbare Backups"""
        if not interaction.guild:
            return []

        backups = await self.backup_mgr.list_backups(interaction.guild.id)
        choices: list[app_commands.Choice[str]] = []

        for backup in backups[:25]:
            bid = backup["backup_id"]
            created = backup.get("created_at", "")
            try:
                dt = datetime.fromisoformat(created)
                date_str = dt.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                date_str = created

            label = f"{bid} ({date_str})"

            if current and current.lower() not in bid.lower():
                continue

            choices.append(app_commands.Choice(name=label[:100], value=bid))

        return choices[:25]

    # Autocomplete registrieren für alle Commands mit backup_id Parameter
    backup_info = app_commands.autocomplete(backup_id=_backup_id_autocomplete)(backup_info)
    backup_compare = app_commands.autocomplete(backup_id=_backup_id_autocomplete)(backup_compare)
    backup_restore = app_commands.autocomplete(backup_id=_backup_id_autocomplete)(backup_restore)
    backup_delete = app_commands.autocomplete(backup_id=_backup_id_autocomplete)(backup_delete)

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung für alle Commands in dieser Cog"""
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
    """Cog zum Bot hinzufuegen"""
    await bot.add_cog(ServerBackupCog(bot))

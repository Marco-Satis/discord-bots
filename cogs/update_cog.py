"""
Update-Cog - Discord-Commands für MC-Modpack und SAT-Updates

Aufgaben I7 + I8: Stellt Slash-Commands bereit für:
  /modpack status|update|cancel|rollback|history|check
  /update start|cancel

Beide Gruppen werden als Top-Level-Commands registriert
(/modpack für MC-Modpack-Updates, /update für SAT-SteamCMD-Updates).
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from typing import Optional, Dict, Any

from utils import get_logger
from utils.permissions import admin_only, owner_only, is_admin
from modules.database.db_manager import get_db
from utils.embeds import (
    COLOR_WARNING,
    error_embed,
    info_embed,
    success_embed,
    warning_embed,
)

logger = get_logger("cogs.update")

# Standard-Countdown für manuelle Updates (Minuten)
DEFAULT_COUNTDOWN_MINUTES = 10


class UpdateCog(commands.Cog):
    """Discord-Commands für MC-Modpack-Updates und SAT-Updates."""

    # ==================================================================
    # Gruppen-Definitionen (Top-Level-Commands: /modpack für MC, /update für SAT)
    # ==================================================================

    modpack_grp = app_commands.Group(
        name="modpack", description="Modpack-Update-Verwaltung"
    )
    sat_update_grp = app_commands.Group(
        name="update", description="SAT-Server-Update-Verwaltung"
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Properties für Bot-Level-Services (graceful wenn nicht vorhanden)
    # ------------------------------------------------------------------

    @property
    def mc_update_managers(self) -> Dict[str, Any]:
        """Dict[server_id, UpdateManager] oder leeres Dict."""
        return getattr(self.bot, "mc_update_managers", {}) or {}

    @property
    def mc_modpack_updaters(self) -> Dict[str, Any]:
        """Dict[server_id, ModpackUpdater] oder leeres Dict."""
        return getattr(self.bot, "mc_modpack_updaters", {}) or {}

    @property
    def update_checker(self):
        """SAT UpdateChecker oder None."""
        return getattr(self.bot, "update_checker", None)

    @property
    def sat_server(self):
        """SatisfactoryServer-Instanz oder None."""
        return getattr(self.bot, "sat_server", None)

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _get_server_choices(self) -> list[str]:
        """Gibt verfügbare MC-Server-IDs zurück."""
        return list(self.mc_update_managers.keys()) or list(self.mc_modpack_updaters.keys())

    def _resolve_server_id(self, server_id: Optional[str]) -> str:
        """Loest server_id auf (Default: BMC)."""
        if server_id:
            return server_id.upper()
        return "BMC"

    async def _get_update_manager(self, server_id: str):
        """Holt den UpdateManager für einen Server (oder None)."""
        return self.mc_update_managers.get(server_id)

    async def _get_modpack_updater(self, server_id: str):
        """Holt den ModpackUpdater für einen Server (oder None)."""
        return self.mc_modpack_updaters.get(server_id)

    # ------------------------------------------------------------------
    # Cog-Lifecycle
    # ------------------------------------------------------------------

    async def cog_load(self) -> None:
        """Gruppen werden als Top-Level registriert (/modpack, /update)."""
        logger.info("UpdateCog geladen")

    async def cog_unload(self) -> None:
        """Entfernt die Top-Level-Gruppen beim Entladen."""
        try:
            self.bot.tree.remove_command("modpack")
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")
        try:
            self.bot.tree.remove_command("update")
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  MC MODPACK: /modpack status                                 ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="status", description="Zeigt Modpack-Version und Update-Status"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC)")
    async def mc_modpack_status(
        self, interaction: discord.Interaction, server: Optional[str] = None
    ):
        """Zeigt aktuellen Modpack-Status: Version, Update-Verfuegbarkeit, laufendes Update."""
        await interaction.response.defer()

        server_id = self._resolve_server_id(server)
        update_mgr = await self._get_update_manager(server_id)
        updater = await self._get_modpack_updater(server_id)

        if not update_mgr and not updater:
            await interaction.followup.send(
                f"Kein Update-System für Server `{server_id}` konfiguriert.",
                ephemeral=True,
            )
            return

        embed = info_embed(
            title=f"\U0001f4e6 Modpack-Status: {server_id}",
        )

        # Status vom UpdateManager
        if update_mgr:
            status = update_mgr.get_status()
            in_progress = status.get("update_in_progress", False)
            timer_active = status.get("timer_active", False)

            if in_progress:
                phase_text = "\U0001f504 Update läuft..."
                if timer_active:
                    phase_text = "\u23f3 Countdown läuft..."
                embed.color = COLOR_WARNING
            else:
                phase_text = "\u2705 Kein Update aktiv"

            embed.add_field(name="Update-Status", value=phase_text, inline=True)

        # Status vom ModpackUpdater (CurseForge-Info)
        if updater:
            up_status = updater.get_status()
            version = up_status.get("current_version", "?")
            latest = up_status.get("latest_version", "?")
            update_avail = up_status.get("update_available", False)
            last_check = up_status.get("last_check")
            source = up_status.get("source", "?")

            embed.add_field(name="Version", value=version, inline=True)
            embed.add_field(name="Quelle", value=source.title(), inline=True)

            if update_avail:
                embed.add_field(
                    name="Neue Version",
                    value=f"\U0001f195 {latest}",
                    inline=True,
                )
            else:
                embed.add_field(
                    name="Update",
                    value="\u2705 Keine neue Version",
                    inline=True,
                )

            if last_check:
                try:
                    check_dt = datetime.fromisoformat(last_check)
                    delta = datetime.now() - check_dt
                    hours = int(delta.total_seconds() // 3600)
                    if hours < 1:
                        minutes = int(delta.total_seconds() // 60)
                        check_str = f"Vor {minutes} Min."
                    else:
                        check_str = f"Vor {hours} Std."
                except (ValueError, TypeError):
                    check_str = str(last_check)[:16]
                embed.add_field(name="Letzter Check", value=check_str, inline=True)

            # CurseForge File-IDs
            cf_id = up_status.get("current_file_id")
            if cf_id:
                embed.set_footer(text=f"CurseForge File-ID: {cf_id}")

        await interaction.followup.send(embed=embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  MC MODPACK: /modpack update                                 ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="update", description="Startet manuelles Modpack-Update mit Countdown"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC)")
    @admin_only()
    async def mc_modpack_update(
        self, interaction: discord.Interaction, server: Optional[str] = None
    ):
        """Startet ein manuelles Update mit 10-Minuten-Countdown."""
        await interaction.response.defer()

        server_id = self._resolve_server_id(server)
        update_mgr = await self._get_update_manager(server_id)

        if not update_mgr:
            await interaction.followup.send(
                f"⚠️ UpdateManager für `{server_id}` nicht verfügbar.\n"
                f"Updates werden über den Monitor-Bot gesteuert.\n"
                f"Nutze `/modpack status` um den aktuellen Status zu prüfen.",
                ephemeral=True,
            )
            return

        # Pruefen ob bereits ein Update läuft
        status = update_mgr.get_status()
        if status.get("update_in_progress"):
            await interaction.followup.send(
                f"\u26a0\ufe0f Auf `{server_id}` läuft bereits ein Update. "
                f"Nutze `/modpack cancel` zum Abbrechen.",
                ephemeral=True,
            )
            return

        # Pruefen ob es ein neues Update gibt
        updater = await self._get_modpack_updater(server_id)
        version_info = None
        if updater:
            try:
                available, info = await updater.check()
                if available:
                    version_info = info
                    new_ver = info.get("latest_version", "?")
                else:
                    await interaction.followup.send(
                        f"\u2705 `{server_id}` ist bereits auf der neuesten Version. "
                        f"Kein Update nötig.",
                    )
                    return
            except Exception as e:
                logger.error(f"[{server_id}] Modpack-Check fehlgeschlagen: {e}")
                await interaction.followup.send(
                    f"\u274c CurseForge-Check fehlgeschlagen: {e}",
                    ephemeral=True,
                )
                return
        else:
            await interaction.followup.send(
                f"Kein ModpackUpdater für `{server_id}` konfiguriert.",
                ephemeral=True,
            )
            return

        # Update starten
        embed = warning_embed(
            title=f"\U0001f680 Modpack-Update gestartet: {server_id}",
            description=(
                f"**Neue Version:** {new_ver}\n"
                f"**Countdown:** {DEFAULT_COUNTDOWN_MINUTES} Minuten\n"
                f"**Gestartet von:** {interaction.user.mention}\n\n"
                f"Abbrechen mit `/modpack cancel`"
            ),
        )
        await interaction.followup.send(embed=embed)

        # Update im Hintergrund starten (blockiert nicht den Command)
        try:
            success, result = await update_mgr.run_update(
                trigger="manual",
                countdown_minutes=DEFAULT_COUNTDOWN_MINUTES,
                version_info=version_info,
            )

            if success:
                result_embed = success_embed(
                    title=f"\u2705 Update erfolgreich: {server_id}",
                    description=f"Version: {new_ver}",
                )
            else:
                error = result.get("error", "Unbekannter Fehler") if isinstance(result, dict) else str(result)
                result_embed = error_embed(
                    title=f"\u274c Update fehlgeschlagen: {server_id}",
                    description=f"Fehler: {error}",
                )

            # Ergebnis in den Kanal posten
            await interaction.channel.send(embed=result_embed)
        except Exception as e:
            logger.error(f"[{server_id}] Manuelles Update Fehler: {e}")
            await interaction.channel.send(
                f"\u274c Update-Fehler auf `{server_id}`: {e}"
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  MC MODPACK: /modpack force-update (Timer überspringen)      ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="force-update", description="Sofortiges Update OHNE Countdown (Testing)"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC)")
    @admin_only()
    async def mc_modpack_force_update(
        self, interaction: discord.Interaction, server: Optional[str] = None
    ):
        """Startet ein Update sofort ohne Timer (für Testing/Notfälle)."""
        await interaction.response.defer()

        server_id = self._resolve_server_id(server)
        update_mgr = await self._get_update_manager(server_id)

        if not update_mgr:
            await interaction.followup.send(
                f"⚠️ UpdateManager für `{server_id}` nicht verfügbar.",
                ephemeral=True,
            )
            return

        status = update_mgr.get_status()
        if status.get("update_in_progress"):
            await interaction.followup.send(
                f"⚠️ Auf `{server_id}` läuft bereits ein Update.",
                ephemeral=True,
            )
            return

        # Prüfen ob Update verfügbar
        updater = await self._get_modpack_updater(server_id)
        version_info = None
        if updater:
            try:
                available, info = await updater.check()
                if available:
                    version_info = info
                    new_ver = info.get("latest_version", "?")
                else:
                    await interaction.followup.send(
                        f"✅ `{server_id}` ist bereits auf der neuesten Version.",
                    )
                    return
            except Exception as e:
                await interaction.followup.send(
                    f"❌ CurseForge-Check fehlgeschlagen: {e}",
                    ephemeral=True,
                )
                return
        else:
            await interaction.followup.send(
                f"Kein ModpackUpdater für `{server_id}` konfiguriert.",
                ephemeral=True,
            )
            return

        # Sofort starten ohne Timer
        embed = error_embed(
            title=f"⚡ Force-Update gestartet: {server_id}",
            description=(
                f"**Neue Version:** {new_ver}\n"
                f"**Countdown:** ÜBERSPRUNGEN\n"
                f"**Gestartet von:** {interaction.user.mention}\n\n"
                f"⚠️ Server wird SOFORT gestoppt!"
            ),
        )
        await interaction.followup.send(embed=embed)

        try:
            success, result = await update_mgr.run_update(
                trigger="manual_force",
                countdown_minutes=0,
                version_info=version_info,
                skip_timer=True,
            )

            if success:
                result_embed = success_embed(
                    title=f"✅ Force-Update erfolgreich: {server_id}",
                    description=f"Version: {new_ver}",
                )
            else:
                error = result.get("error", "Unbekannter Fehler") if isinstance(result, dict) else str(result)
                result_embed = error_embed(
                    title=f"❌ Force-Update fehlgeschlagen: {server_id}",
                    description=f"Fehler: {error}",
                )

            await interaction.channel.send(embed=result_embed)
        except Exception as e:
            logger.error(f"[{server_id}] Force-Update Fehler: {e}")
            await interaction.channel.send(
                f"❌ Force-Update-Fehler auf `{server_id}`: {e}"
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  MC MODPACK: /modpack cancel                                 ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="cancel", description="Bricht laufendes Update/Countdown ab"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC)")
    @admin_only()
    async def mc_modpack_cancel(
        self, interaction: discord.Interaction, server: Optional[str] = None
    ):
        """Bricht ein laufendes Update oder den Countdown ab."""
        await interaction.response.defer(ephemeral=True)

        server_id = self._resolve_server_id(server)
        update_mgr = await self._get_update_manager(server_id)

        if not update_mgr:
            await interaction.followup.send(
                f"Kein UpdateManager für Server `{server_id}` konfiguriert.",
            )
            return

        status = update_mgr.get_status()
        if not status.get("update_in_progress") and not status.get("timer_active"):
            await interaction.followup.send(
                f"Kein laufendes Update auf `{server_id}` zum Abbrechen.",
            )
            return

        cancelled = await update_mgr.cancel()
        if cancelled:
            embed = warning_embed(
                title=f"\u23f9\ufe0f Update abgebrochen: {server_id}",
                description=f"Abgebrochen von {interaction.user.mention}",
            )
            # Oeffentlich im Kanal posten
            await interaction.followup.send("\u2705 Abbruch eingeleitet.")
            await interaction.channel.send(embed=embed)
        else:
            await interaction.followup.send(
                f"Abbruch auf `{server_id}` nicht möglich "
                f"(Update befindet sich in einer nicht-abbrechbaren Phase).",
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  MC MODPACK: /modpack rollback                               ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="rollback", description="Manueller Rollback auf vorherige Version"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC)")
    @owner_only()
    async def mc_modpack_rollback(
        self, interaction: discord.Interaction, server: Optional[str] = None
    ):
        """Manueller Rollback — nur wenn kein Update läuft. Nur für Owner."""
        await interaction.response.defer()

        server_id = self._resolve_server_id(server)
        update_mgr = await self._get_update_manager(server_id)

        if not update_mgr:
            await interaction.followup.send(
                f"Kein UpdateManager für Server `{server_id}` konfiguriert.",
                ephemeral=True,
            )
            return

        # Pruefen ob ein Update läuft
        status = update_mgr.get_status()
        if status.get("update_in_progress"):
            await interaction.followup.send(
                f"\u26a0\ufe0f Auf `{server_id}` läuft ein Update. "
                f"Erst abbrechen mit `/modpack cancel`.",
                ephemeral=True,
            )
            return

        # Letztes erfolgreiches Update aus DB holen (für Rollback-Pfad)
        try:
            db_conn = await get_db()
            cursor = await db_conn.execute(
                """SELECT id, old_version, new_version, rollback_path, backup_path
                   FROM modpack_updates
                   WHERE server_id = ? AND status = 'success'
                   ORDER BY completed_at DESC LIMIT 1""",
                (server_id,),
            )
            row = await cursor.fetchone()

            if not row:
                await interaction.followup.send(
                    f"Kein erfolgreiches Update für `{server_id}` in der Historie "
                    f"gefunden — Rollback nicht möglich.",
                    ephemeral=True,
                )
                return

            # row kann als Tuple oder Dict kommen (aiosqlite row_factory)
            if isinstance(row, dict):
                update_id = row["id"]
                old_ver = row["old_version"]
                new_ver = row["new_version"]
            else:
                update_id = row[0]
                old_ver = row[1]
                new_ver = row[2]

        except Exception as e:
            logger.error(f"[{server_id}] DB-Abfrage für Rollback fehlgeschlagen: {e}")
            await interaction.followup.send(
                f"\u274c DB-Fehler: {e}", ephemeral=True
            )
            return

        # Rollback-Verzeichnis prüfen (UpdateManager._perform_rollback erwartet Pfade)
        # Der UpdateManager hat _perform_rollback als private Methode.
        # Wir delegieren an die Crash-Recovery-Logik, die den gleichen Pfad nutzt.
        from pathlib import Path

        server_path = None
        if hasattr(update_mgr, '_get_server_path'):
            server_path = update_mgr._get_server_path()

        if not server_path:
            await interaction.followup.send(
                f"Server-Pfad für `{server_id}` nicht ermittelt — "
                f"Rollback nicht möglich.",
                ephemeral=True,
            )
            return

        rollback_dir = server_path.parent / f"{server_path.name}_rollback_{update_id}"
        if not rollback_dir.exists():
            await interaction.followup.send(
                f"Rollback-Verzeichnis nicht gefunden:\n`{rollback_dir}`\n\n"
                f"Moeglicherweise wurde es bereits bereinigt.",
                ephemeral=True,
            )
            return

        # Bestaetigung und Rollback ausführen
        embed = warning_embed(
            title=f"\u21a9\ufe0f Rollback gestartet: {server_id}",
            description=(
                f"**Von:** {new_ver} **Auf:** {old_ver}\n"
                f"**Gestartet von:** {interaction.user.mention}"
            ),
        )
        await interaction.followup.send(embed=embed)

        try:
            # Server stoppen
            mc_server = getattr(update_mgr, 'mc_server', None)
            if mc_server:
                try:
                    await mc_server.stop()
                except Exception as e:
                    logger.warning(f"[{server_id}] Server-Stop vor Rollback: {e}")

            # Rollback durchfuehren
            await update_mgr._perform_rollback(rollback_dir, server_path)

            # DB-Status aktualisieren
            try:
                db_conn = await get_db()
                await db_conn.execute(
                    """UPDATE modpack_updates SET status='rolled_back',
                       error_message='Manueller Rollback via Discord'
                       WHERE id=?""",
                    (update_id,),
                )
                await db_conn.commit()
            except Exception as e:
                logger.warning(f"[{server_id}] DB-Update nach Rollback: {e}")

            result_embed = success_embed(
                title=f"\u2705 Rollback erfolgreich: {server_id}",
                description=f"Version: {old_ver}",
            )
            await interaction.channel.send(embed=result_embed)

        except Exception as e:
            logger.error(f"[{server_id}] Manueller Rollback fehlgeschlagen: {e}")
            # Nicht "error_embed = error_embed(...)": der Name waere dadurch
            # in der ganzen Funktion lokal und der Aufruf liefe in einen
            # UnboundLocalError \u2014 ausgerechnet im einzigen Fehlerpfad.
            fehler_embed = error_embed(
                title=f"\u274c Rollback fehlgeschlagen: {server_id}",
                description=str(e),
            )
            await interaction.channel.send(embed=fehler_embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  MC MODPACK: /modpack history                                ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="history", description="Zeigt die letzten 5 Updates"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC)")
    async def mc_modpack_history(
        self, interaction: discord.Interaction, server: Optional[str] = None
    ):
        """Zeigt die letzten 5 Updates aus der modpack_updates Tabelle."""
        await interaction.response.defer()

        server_id = self._resolve_server_id(server)

        try:
            db_conn = await get_db()
            cursor = await db_conn.execute(
                """SELECT old_version, new_version, update_type, status,
                          started_at, completed_at, error_message,
                          neoforge_updated, old_neoforge, new_neoforge
                   FROM modpack_updates
                   WHERE server_id = ?
                   ORDER BY started_at DESC
                   LIMIT 5""",
                (server_id,),
            )
            rows = await cursor.fetchall()
        except Exception as e:
            logger.error(f"[{server_id}] History-Abfrage fehlgeschlagen: {e}")
            await interaction.followup.send(
                f"\u274c DB-Fehler: {e}", ephemeral=True
            )
            return

        if not rows:
            await interaction.followup.send(
                f"Keine Update-Historie für `{server_id}` vorhanden."
            )
            return

        embed = info_embed(
            title=f"\U0001f4dc Update-Historie: {server_id}",
        )

        status_icons = {
            "success": "\u2705",
            "failed": "\u274c",
            "rolled_back": "\u21a9\ufe0f",
            "in_progress": "\U0001f504",
            "scheduled": "\U0001f552",
        }

        for row in rows:
            if isinstance(row, dict):
                old_ver = row["old_version"]
                new_ver = row["new_version"]
                up_type = row["update_type"]
                status = row["status"]
                started = row["started_at"]
                completed = row["completed_at"]
                error = row["error_message"]
                nf_updated = row["neoforge_updated"]
                old_nf = row["old_neoforge"]
                new_nf = row["new_neoforge"]
            else:
                old_ver, new_ver, up_type, status = row[0], row[1], row[2], row[3]
                started, completed, error = row[4], row[5], row[6]
                nf_updated, old_nf, new_nf = row[7], row[8], row[9]

            icon = status_icons.get(status, "\u2753")
            date_str = _format_date(started)

            value_lines = [
                f"{old_ver} \u2192 {new_ver}",
                f"Typ: {up_type} | Status: {icon} {status}",
            ]

            if nf_updated and old_nf and new_nf:
                value_lines.append(f"NeoForge: {old_nf} \u2192 {new_nf}")

            if error:
                # Fehlertext kuerzen
                short_error = error[:80] + "..." if len(error) > 80 else error
                value_lines.append(f"Fehler: {short_error}")

            embed.add_field(
                name=f"{icon} {date_str}",
                value="\n".join(value_lines),
                inline=False,
            )

        embed.set_footer(text=f"Letzte {len(rows)} Updates")
        await interaction.followup.send(embed=embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  MC MODPACK: /modpack check                                  ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="check", description="Prueft CurseForge auf neue Version"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC)")
    @admin_only()
    async def mc_modpack_check(
        self, interaction: discord.Interaction, server: Optional[str] = None
    ):
        """Manueller CurseForge-Check ohne automatisches Update."""
        await interaction.response.defer()

        server_id = self._resolve_server_id(server)
        updater = await self._get_modpack_updater(server_id)

        if not updater:
            await interaction.followup.send(
                f"Kein ModpackUpdater für Server `{server_id}` konfiguriert.",
                ephemeral=True,
            )
            return

        try:
            available, info = await updater.check()
        except Exception as e:
            logger.error(f"[{server_id}] CurseForge-Check fehlgeschlagen: {e}")
            await interaction.followup.send(
                f"\u274c CurseForge-Check fehlgeschlagen: {e}",
                ephemeral=True,
            )
            return

        if available:
            latest = info.get("latest_version", "?")
            current = info.get("current_version", "?")
            server_pack = info.get("server_pack", {}) or {}
            sp_url = server_pack.get("url", "")

            embed = warning_embed(
                title=f"\U0001f195 Neue Version verfügbar: {server_id}",
                description=(
                    f"**Aktuell:** {current}\n"
                    f"**Neu:** {latest}\n"
                ),
            )

            if sp_url:
                embed.add_field(name="Server Pack", value="Verfuegbar", inline=True)

            # NeoForge-Info falls vorhanden
            nf_info = info.get("neoforge")
            if nf_info:
                embed.add_field(
                    name="NeoForge",
                    value=str(nf_info),
                    inline=True,
                )

            embed.set_footer(
                text=f"Verwende /modpack update {server_id} zum Aktualisieren"
            )
        else:
            current = info.get("current_version", "?")
            embed = success_embed(
                title=f"\u2705 Keine neue Version: {server_id}",
                description=f"Aktuelle Version: {current}",
            )

        await interaction.followup.send(embed=embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  SAT: /update start                                          ║
    # ╚════════════════════════════════════════════════════════════════╝

    @sat_update_grp.command(
        name="start", description="Startet manuelles SAT-Update via SteamCMD"
    )
    @admin_only()
    async def sat_update_start(self, interaction: discord.Interaction):
        """Startet ein manuelles SAT-Update (SteamCMD app_update)."""
        await interaction.response.defer()

        if not self.update_checker:
            await interaction.followup.send(
                "SAT UpdateChecker ist nicht konfiguriert.", ephemeral=True
            )
            return

        if not self.sat_server:
            await interaction.followup.send(
                "SAT Server-Instanz ist nicht verfügbar.", ephemeral=True
            )
            return

        # Aktuellen Status prüfen
        try:
            available, info = await self.update_checker.check()
        except Exception as e:
            logger.error(f"SAT Update-Check fehlgeschlagen: {e}")
            await interaction.followup.send(
                f"\u274c SteamCMD-Check fehlgeschlagen: {e}", ephemeral=True
            )
            return

        installed = info.get("installed_buildid", "?")
        new_build = info.get("available_buildid", "?")

        if not available:
            await interaction.followup.send(
                f"\u2705 SAT Server ist aktuell (Build: {installed}). "
                f"Kein Update nötig.",
            )
            return

        # Update bestaetigen und starten
        embed = warning_embed(
            title="\U0001f680 SAT-Update gestartet",
            description=(
                f"**Installiert:** Build {installed}\n"
                f"**Verfuegbar:** Build {new_build}\n"
                f"**Gestartet von:** {interaction.user.mention}\n\n"
                f"Abbrechen mit `/update cancel`"
            ),
        )
        await interaction.followup.send(embed=embed)

        # HAR unterdruecken
        har = getattr(self.bot, "health_auto_restart", None)
        if har:
            try:
                har.suppress("sat", "main", duration_seconds=900)
            except Exception as e:
                logger.debug(f"HAR-Suppress Fehler: {e}")

        # Update durchfuehren
        try:
            running = await self.sat_server.is_running()

            if running:
                # In-Game Warnung
                sat_api = getattr(self.bot, "sat_api", None)
                if sat_api:
                    try:
                        await sat_api.run_command(
                            "ServerChat Server wird für ein Update heruntergefahren!"
                        )
                    except Exception as e:
                        logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

                # Server stoppen
                stop_ok, stop_msg = await self.sat_server.stop()
                if not stop_ok:
                    await interaction.channel.send(
                        f"\u274c Server-Stop fehlgeschlagen: {stop_msg}"
                    )
                    return

            # SteamCMD Update
            update_ok, update_msg = await self.update_checker.perform_update(
                self.sat_server, har=har
            )

            if update_ok:
                # Neue Build-ID prüfen
                new_info_build = "?"
                try:
                    _, new_info = await self.update_checker.check()
                    new_info_build = new_info.get("installed_buildid", "?")
                except Exception as e:
                    logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

                result_embed = success_embed(
                    title="\u2705 SAT-Update erfolgreich",
                    description=(
                        f"{update_msg}\n\n"
                        f"**Alte Build-ID:** {installed}\n"
                        f"**Neue Build-ID:** {new_info_build}"
                    ),
                )
            else:
                result_embed = error_embed(
                    title="\u274c SAT-Update fehlgeschlagen",
                    description=update_msg,
                )

            await interaction.channel.send(embed=result_embed)

        except Exception as e:
            logger.error(f"SAT manuelles Update Fehler: {e}")
            await interaction.channel.send(f"\u274c SAT-Update Fehler: {e}")

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  SAT: /update cancel                                         ║
    # ╚════════════════════════════════════════════════════════════════╝

    @sat_update_grp.command(
        name="cancel", description="Bricht laufendes SAT-Update ab"
    )
    @admin_only()
    async def sat_update_cancel(self, interaction: discord.Interaction):
        """Bricht ein laufendes SAT-Update ab (sofern möglich)."""
        await interaction.response.defer(ephemeral=True)

        if not self.update_checker:
            await interaction.followup.send(
                "SAT UpdateChecker ist nicht konfiguriert.",
            )
            return

        # SAT hat keinen Timer-basierten Countdown wie MC,
        # daher ist Abbruch nur möglich, solange SteamCMD noch nicht gestartet hat.
        # Wir setzen update_available zurück und informieren.
        self.update_checker.update_available = False

        # HAR wieder aktivieren
        har = getattr(self.bot, "health_auto_restart", None)
        if har:
            try:
                har.unsuppress("sat", "main")
            except Exception as e:
                logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

        embed = warning_embed(
            title="\u23f9\ufe0f SAT-Update abgebrochen",
            description=(
                f"Abgebrochen von {interaction.user.mention}\n\n"
                f"Update-Flag zurückgesetzt. Falls SteamCMD bereits läuft, "
                f"muss der Prozess manuell gestoppt werden."
            ),
        )
        await interaction.followup.send("\u2705 SAT-Update abgebrochen.")
        await interaction.channel.send(embed=embed)


# ======================================================================
# Hilfsfunktionen (Modul-Ebene)
# ======================================================================

def _format_date(iso_string: Optional[str]) -> str:
    """Formatiert einen ISO-Datetime-String für die Anzeige."""
    if not iso_string:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return str(iso_string)[:16]


async def setup(bot: commands.Bot) -> None:
    """Cog laden. Subgruppen werden als Top-Level registriert (/modpack, /update)."""
    cog = UpdateCog(bot)
    await bot.add_cog(cog)
    logger.info("UpdateCog geladen — /modpack und /update Commands registriert")

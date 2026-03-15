"""
Update-Cog - Discord-Commands fuer MC-Modpack und SAT-Updates

Aufgaben I7 + I8: Stellt Slash-Commands bereit fuer:
  /mc modpack status|update|cancel|rollback|history|check
  /sat update start|cancel

Die Subgruppen werden zur Laufzeit in die bestehenden /mc und /sat
Gruppen eingehaengt (MinecraftCog bzw. SatisfactoryCog besitzen die
Top-Level-Gruppen).
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from typing import Optional, Dict, Any

from utils import get_logger
from utils.permissions import admin_only, owner_only, is_admin
from modules.database.db_manager import get_db

logger = get_logger("cogs.update")

# Standard-Countdown fuer manuelle Updates (Minuten)
DEFAULT_COUNTDOWN_MINUTES = 10


class UpdateCog(commands.Cog):
    """Discord-Commands fuer MC-Modpack-Updates und SAT-Updates."""

    # ==================================================================
    # Subgruppen-Definitionen (werden in cog_load an /mc und /sat gehaengt)
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
    # Properties fuer Bot-Level-Services (graceful wenn nicht vorhanden)
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
        """Gibt verfuegbare MC-Server-IDs zurueck."""
        return list(self.mc_update_managers.keys()) or list(self.mc_modpack_updaters.keys())

    def _resolve_server_id(self, server_id: Optional[str]) -> str:
        """Loest server_id auf (Default: BMC)."""
        if server_id:
            return server_id.upper()
        return "BMC"

    async def _get_update_manager(self, server_id: str):
        """Holt den UpdateManager fuer einen Server (oder None)."""
        return self.mc_update_managers.get(server_id)

    async def _get_modpack_updater(self, server_id: str):
        """Holt den ModpackUpdater fuer einen Server (oder None)."""
        return self.mc_modpack_updaters.get(server_id)

    # ------------------------------------------------------------------
    # Cog-Lifecycle: Subgruppen dynamisch in bestehende Gruppen einhaengen
    # ------------------------------------------------------------------

    async def cog_load(self) -> None:
        """Haengt modpack_grp unter /mc und sat_update_grp unter /sat ein."""
        # MC: modpack-Subgruppe in /mc einhaengen
        mc_group = self._find_group_in_tree("mc")
        if mc_group is not None:
            try:
                mc_group.add_command(self.modpack_grp)
                logger.info("/mc modpack Subgruppe registriert")
            except Exception as e:
                logger.warning(f"/mc modpack Registrierung fehlgeschlagen: {e}")
        else:
            logger.warning(
                "/mc Gruppe nicht im Tree gefunden — "
                "/mc modpack Commands nicht verfuegbar"
            )

        # SAT: update-Subgruppe in /sat einhaengen
        sat_group = self._find_group_in_tree("sat")
        if sat_group is not None:
            try:
                sat_group.add_command(self.sat_update_grp)
                logger.info("/sat update Subgruppe registriert")
            except Exception as e:
                logger.warning(f"/sat update Registrierung fehlgeschlagen: {e}")
        else:
            logger.warning(
                "/sat Gruppe nicht im Tree gefunden — "
                "/sat update Commands nicht verfuegbar"
            )

    async def cog_unload(self) -> None:
        """Entfernt die Subgruppen beim Entladen."""
        mc_group = self._find_group_in_tree("mc")
        if mc_group is not None:
            try:
                mc_group.remove_command("modpack")
            except Exception:
                pass

        sat_group = self._find_group_in_tree("sat")
        if sat_group is not None:
            try:
                sat_group.remove_command("update")
            except Exception:
                pass

    def _find_group_in_tree(self, name: str) -> Optional[app_commands.Group]:
        """Sucht eine Top-Level app_commands.Group im Bot-Tree."""
        for cmd in self.bot.tree.get_commands():
            if isinstance(cmd, app_commands.Group) and cmd.name == name:
                return cmd
        return None

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  MC MODPACK: /mc modpack status                              ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="status", description="Zeigt Modpack-Version und Update-Status"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC, VANILLA)")
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
                f"Kein Update-System fuer Server `{server_id}` konfiguriert.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"\U0001f4e6 Modpack-Status: {server_id}",
            color=0x3498db,
            timestamp=datetime.now(),
        )

        # Status vom UpdateManager
        if update_mgr:
            status = update_mgr.get_status()
            in_progress = status.get("update_in_progress", False)
            timer_active = status.get("timer_active", False)

            if in_progress:
                phase_text = "\U0001f504 Update laeuft..."
                if timer_active:
                    phase_text = "\u23f3 Countdown laeuft..."
                embed.color = 0xf39c12
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
    # ║  MC MODPACK: /mc modpack update                              ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="update", description="Startet manuelles Modpack-Update mit Countdown"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC, VANILLA)")
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
                f"Kein UpdateManager fuer Server `{server_id}` konfiguriert.",
                ephemeral=True,
            )
            return

        # Pruefen ob bereits ein Update laeuft
        status = update_mgr.get_status()
        if status.get("update_in_progress"):
            await interaction.followup.send(
                f"\u26a0\ufe0f Auf `{server_id}` laeuft bereits ein Update. "
                f"Nutze `/mc modpack cancel` zum Abbrechen.",
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
                        f"Kein Update noetig.",
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
                f"Kein ModpackUpdater fuer `{server_id}` konfiguriert.",
                ephemeral=True,
            )
            return

        # Update starten
        embed = discord.Embed(
            title=f"\U0001f680 Modpack-Update gestartet: {server_id}",
            description=(
                f"**Neue Version:** {new_ver}\n"
                f"**Countdown:** {DEFAULT_COUNTDOWN_MINUTES} Minuten\n"
                f"**Gestartet von:** {interaction.user.mention}\n\n"
                f"Abbrechen mit `/mc modpack cancel`"
            ),
            color=0xf39c12,
            timestamp=datetime.now(),
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
                result_embed = discord.Embed(
                    title=f"\u2705 Update erfolgreich: {server_id}",
                    description=f"Version: {new_ver}",
                    color=0x2ecc71,
                )
            else:
                error = result.get("error", "Unbekannter Fehler") if isinstance(result, dict) else str(result)
                result_embed = discord.Embed(
                    title=f"\u274c Update fehlgeschlagen: {server_id}",
                    description=f"Fehler: {error}",
                    color=0xe74c3c,
                )

            # Ergebnis in den Kanal posten
            await interaction.channel.send(embed=result_embed)
        except Exception as e:
            logger.error(f"[{server_id}] Manuelles Update Fehler: {e}")
            await interaction.channel.send(
                f"\u274c Update-Fehler auf `{server_id}`: {e}"
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  MC MODPACK: /mc modpack cancel                              ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="cancel", description="Bricht laufendes Update/Countdown ab"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC, VANILLA)")
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
                f"Kein UpdateManager fuer Server `{server_id}` konfiguriert.",
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
            embed = discord.Embed(
                title=f"\u23f9\ufe0f Update abgebrochen: {server_id}",
                description=f"Abgebrochen von {interaction.user.mention}",
                color=0xe67e22,
                timestamp=datetime.now(),
            )
            # Oeffentlich im Kanal posten
            await interaction.followup.send("\u2705 Abbruch eingeleitet.")
            await interaction.channel.send(embed=embed)
        else:
            await interaction.followup.send(
                f"Abbruch auf `{server_id}` nicht moeglich "
                f"(Update befindet sich in einer nicht-abbrechbaren Phase).",
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  MC MODPACK: /mc modpack rollback                            ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="rollback", description="Manueller Rollback auf vorherige Version"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC, VANILLA)")
    @owner_only()
    async def mc_modpack_rollback(
        self, interaction: discord.Interaction, server: Optional[str] = None
    ):
        """Manueller Rollback — nur wenn kein Update laeuft. Nur fuer Owner."""
        await interaction.response.defer()

        server_id = self._resolve_server_id(server)
        update_mgr = await self._get_update_manager(server_id)

        if not update_mgr:
            await interaction.followup.send(
                f"Kein UpdateManager fuer Server `{server_id}` konfiguriert.",
                ephemeral=True,
            )
            return

        # Pruefen ob ein Update laeuft
        status = update_mgr.get_status()
        if status.get("update_in_progress"):
            await interaction.followup.send(
                f"\u26a0\ufe0f Auf `{server_id}` laeuft ein Update. "
                f"Erst abbrechen mit `/mc modpack cancel`.",
                ephemeral=True,
            )
            return

        # Letztes erfolgreiches Update aus DB holen (fuer Rollback-Pfad)
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
                    f"Kein erfolgreiches Update fuer `{server_id}` in der Historie "
                    f"gefunden — Rollback nicht moeglich.",
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
            logger.error(f"[{server_id}] DB-Abfrage fuer Rollback fehlgeschlagen: {e}")
            await interaction.followup.send(
                f"\u274c DB-Fehler: {e}", ephemeral=True
            )
            return

        # Rollback-Verzeichnis pruefen (UpdateManager._perform_rollback erwartet Pfade)
        # Der UpdateManager hat _perform_rollback als private Methode.
        # Wir delegieren an die Crash-Recovery-Logik, die den gleichen Pfad nutzt.
        from pathlib import Path

        server_path = None
        if hasattr(update_mgr, '_get_server_path'):
            server_path = update_mgr._get_server_path()

        if not server_path:
            await interaction.followup.send(
                f"Server-Pfad fuer `{server_id}` nicht ermittelt — "
                f"Rollback nicht moeglich.",
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

        # Bestaetigung und Rollback ausfuehren
        embed = discord.Embed(
            title=f"\u21a9\ufe0f Rollback gestartet: {server_id}",
            description=(
                f"**Von:** {new_ver} **Auf:** {old_ver}\n"
                f"**Gestartet von:** {interaction.user.mention}"
            ),
            color=0xe67e22,
            timestamp=datetime.now(),
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

            result_embed = discord.Embed(
                title=f"\u2705 Rollback erfolgreich: {server_id}",
                description=f"Version: {old_ver}",
                color=0x2ecc71,
            )
            await interaction.channel.send(embed=result_embed)

        except Exception as e:
            logger.error(f"[{server_id}] Manueller Rollback fehlgeschlagen: {e}")
            error_embed = discord.Embed(
                title=f"\u274c Rollback fehlgeschlagen: {server_id}",
                description=str(e),
                color=0xe74c3c,
            )
            await interaction.channel.send(embed=error_embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  MC MODPACK: /mc modpack history                             ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="history", description="Zeigt die letzten 5 Updates"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC, VANILLA)")
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
                f"Keine Update-Historie fuer `{server_id}` vorhanden."
            )
            return

        embed = discord.Embed(
            title=f"\U0001f4dc Update-Historie: {server_id}",
            color=0x3498db,
            timestamp=datetime.now(),
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
    # ║  MC MODPACK: /mc modpack check                               ║
    # ╚════════════════════════════════════════════════════════════════╝

    @modpack_grp.command(
        name="check", description="Prueft CurseForge auf neue Version"
    )
    @app_commands.describe(server="Server-ID (z.B. BMC, VANILLA)")
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
                f"Kein ModpackUpdater fuer Server `{server_id}` konfiguriert.",
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

            embed = discord.Embed(
                title=f"\U0001f195 Neue Version verfuegbar: {server_id}",
                description=(
                    f"**Aktuell:** {current}\n"
                    f"**Neu:** {latest}\n"
                ),
                color=0xf39c12,
                timestamp=datetime.now(),
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
                text=f"Verwende /mc modpack update {server_id} zum Aktualisieren"
            )
        else:
            current = info.get("current_version", "?")
            embed = discord.Embed(
                title=f"\u2705 Keine neue Version: {server_id}",
                description=f"Aktuelle Version: {current}",
                color=0x2ecc71,
                timestamp=datetime.now(),
            )

        await interaction.followup.send(embed=embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  SAT: /sat update start                                      ║
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
                "SAT Server-Instanz ist nicht verfuegbar.", ephemeral=True
            )
            return

        # Aktuellen Status pruefen
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
                f"Kein Update noetig.",
            )
            return

        # Update bestaetigen und starten
        embed = discord.Embed(
            title="\U0001f680 SAT-Update gestartet",
            description=(
                f"**Installiert:** Build {installed}\n"
                f"**Verfuegbar:** Build {new_build}\n"
                f"**Gestartet von:** {interaction.user.mention}\n\n"
                f"Abbrechen mit `/sat update cancel`"
            ),
            color=0xf39c12,
            timestamp=datetime.now(),
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
                            "ServerChat Server wird fuer ein Update heruntergefahren!"
                        )
                    except Exception:
                        pass

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
                # Neue Build-ID pruefen
                new_info_build = "?"
                try:
                    _, new_info = await self.update_checker.check()
                    new_info_build = new_info.get("installed_buildid", "?")
                except Exception:
                    pass

                result_embed = discord.Embed(
                    title="\u2705 SAT-Update erfolgreich",
                    description=(
                        f"{update_msg}\n\n"
                        f"**Alte Build-ID:** {installed}\n"
                        f"**Neue Build-ID:** {new_info_build}"
                    ),
                    color=0x2ecc71,
                )
            else:
                result_embed = discord.Embed(
                    title="\u274c SAT-Update fehlgeschlagen",
                    description=update_msg,
                    color=0xe74c3c,
                )

            await interaction.channel.send(embed=result_embed)

        except Exception as e:
            logger.error(f"SAT manuelles Update Fehler: {e}")
            await interaction.channel.send(f"\u274c SAT-Update Fehler: {e}")

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  SAT: /sat update cancel                                     ║
    # ╚════════════════════════════════════════════════════════════════╝

    @sat_update_grp.command(
        name="cancel", description="Bricht laufendes SAT-Update ab"
    )
    @admin_only()
    async def sat_update_cancel(self, interaction: discord.Interaction):
        """Bricht ein laufendes SAT-Update ab (sofern moeglich)."""
        await interaction.response.defer(ephemeral=True)

        if not self.update_checker:
            await interaction.followup.send(
                "SAT UpdateChecker ist nicht konfiguriert.",
            )
            return

        # SAT hat keinen Timer-basierten Countdown wie MC,
        # daher ist Abbruch nur moeglich, solange SteamCMD noch nicht gestartet hat.
        # Wir setzen update_available zurueck und informieren.
        self.update_checker.update_available = False

        # HAR wieder aktivieren
        har = getattr(self.bot, "health_auto_restart", None)
        if har:
            try:
                har.unsuppress("sat", "main")
            except Exception:
                pass

        embed = discord.Embed(
            title="\u23f9\ufe0f SAT-Update abgebrochen",
            description=(
                f"Abgebrochen von {interaction.user.mention}\n\n"
                f"Update-Flag zurueckgesetzt. Falls SteamCMD bereits laeuft, "
                f"muss der Prozess manuell gestoppt werden."
            ),
            color=0xe67e22,
            timestamp=datetime.now(),
        )
        await interaction.followup.send("\u2705 SAT-Update abgebrochen.")
        await interaction.channel.send(embed=embed)


# ======================================================================
# Hilfsfunktionen (Modul-Ebene)
# ======================================================================

def _format_date(iso_string: Optional[str]) -> str:
    """Formatiert einen ISO-Datetime-String fuer die Anzeige."""
    if not iso_string:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return str(iso_string)[:16]


async def setup(bot: commands.Bot) -> None:
    """Cog laden."""
    await bot.add_cog(UpdateCog(bot))

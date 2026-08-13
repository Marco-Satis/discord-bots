"""
F43: Wartungsmodus-Toggle — Zentraler Wartungsmodus für alle Server

Aktiviert/deaktiviert einen globalen Wartungsmodus. Wenn aktiv, werden alle
Server-Kacheln auf "Wartung" gesetzt. Der Status wird in SQLite gespeichert
und zusaetzlich als JSON-Datei geschrieben, damit StatusWriter und Dashboard
ihn lesen können.

Sicherheitsnetz: Wartungsmodus endet automatisch nach 6 Stunden.

Commands:
  /wartung an [grund]   — Wartungsmodus aktivieren (optional mit Grund)
  /wartung aus          — Wartungsmodus deaktivieren
  /wartung status       — Aktuellen Wartungsstatus anzeigen
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.database.db_manager import get_db
from utils.config import MONITOR_DATA_DIR
from utils.logger import get_logger
from utils.embeds import COLOR_SUCCESS, COLOR_WARNING

logger = get_logger("cogs.maintenance_mode")

# Automatisches Ende nach 6 Stunden (Sicherheitsnetz)
AUTO_TIMEOUT_HOURS = 6

# Pfad zur JSON-Datei für StatusWriter und Dashboard
MAINTENANCE_JSON = MONITOR_DATA_DIR / "maintenance.json"


class MaintenanceModeCog(commands.Cog):
    """Zentraler Wartungsmodus-Toggle für alle Server"""

    # ==================================================================
    # Command-Gruppe
    # ==================================================================

    wartung_grp = app_commands.Group(
        name="wartung",
        description="Wartungsmodus verwalten",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        logger.info("MaintenanceModeCog initialisiert")

    # ------------------------------------------------------------------
    # Cog Lifecycle
    # ------------------------------------------------------------------

    async def cog_load(self) -> None:
        """Tabelle sicherstellen und Auto-Timeout-Task starten"""
        await self._ensure_table()
        self._auto_timeout_check.start()
        logger.info("MaintenanceModeCog geladen — Auto-Timeout-Task gestartet")

    async def cog_unload(self) -> None:
        """Auto-Timeout-Task stoppen"""
        self._auto_timeout_check.cancel()
        logger.info("MaintenanceModeCog entladen")

    # ------------------------------------------------------------------
    # Datenbank-Schema
    # ------------------------------------------------------------------

    async def _ensure_table(self) -> None:
        """Erstellt die maintenance_mode-Tabelle falls nicht vorhanden"""
        db = await get_db()
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_mode (
                id INTEGER PRIMARY KEY DEFAULT 1,
                active INTEGER DEFAULT 0,
                reason TEXT DEFAULT '',
                activated_by TEXT DEFAULT '',
                activated_at TEXT DEFAULT '',
                auto_end_at TEXT DEFAULT ''
            )
            """
        )
        # Sicherstellen, dass genau eine Zeile existiert
        cursor = await db.execute("SELECT COUNT(*) FROM maintenance_mode")
        row = await cursor.fetchone()
        if row[0] == 0:
            await db.execute(
                "INSERT INTO maintenance_mode (id, active) VALUES (1, 0)"
            )
        await db.commit()

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    async def _get_state(self) -> dict:
        """Liest den aktuellen Wartungsstatus aus der Datenbank"""
        db = await get_db()
        cursor = await db.execute(
            "SELECT active, reason, activated_by, activated_at, auto_end_at "
            "FROM maintenance_mode WHERE id = 1"
        )
        row = await cursor.fetchone()
        if row is None:
            return {
                "active": False,
                "reason": "",
                "activated_by": "",
                "activated_at": "",
                "auto_end_at": "",
            }
        return {
            "active": bool(row[0]),
            "reason": row[1] or "",
            "activated_by": row[2] or "",
            "activated_at": row[3] or "",
            "auto_end_at": row[4] or "",
        }

    async def _set_active(
        self,
        active: bool,
        reason: str = "",
        activated_by: str = "",
    ) -> None:
        """Setzt den Wartungsmodus in DB und schreibt die JSON-Datei"""
        db = await get_db()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if active:
            auto_end = (
                datetime.now() + timedelta(hours=AUTO_TIMEOUT_HOURS)
            ).strftime("%Y-%m-%d %H:%M:%S")

            await db.execute(
                """
                UPDATE maintenance_mode
                SET active = 1,
                    reason = ?,
                    activated_by = ?,
                    activated_at = ?,
                    auto_end_at = ?
                WHERE id = 1
                """,
                (reason, activated_by, now_str, auto_end),
            )
        else:
            await db.execute(
                """
                UPDATE maintenance_mode
                SET active = 0,
                    reason = '',
                    activated_by = '',
                    activated_at = '',
                    auto_end_at = ''
                WHERE id = 1
                """
            )

        await db.commit()

        # JSON-Datei für StatusWriter und Dashboard schreiben
        self._write_json(active, reason)

    def _write_json(self, active: bool, reason: str = "") -> None:
        """Schreibt maintenance.json für externe Systeme"""
        try:
            MAINTENANCE_JSON.parent.mkdir(parents=True, exist_ok=True)
            data = {"active": active, "reason": reason}
            with open(MAINTENANCE_JSON, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug(f"maintenance.json geschrieben: active={active}")
        except Exception as e:
            logger.error(f"Fehler beim Schreiben von maintenance.json: {e}")

    # ------------------------------------------------------------------
    # Auto-Timeout Task (Sicherheitsnetz)
    # ------------------------------------------------------------------

    @tasks.loop(minutes=5)
    async def _auto_timeout_check(self) -> None:
        """Prueft alle 5 Minuten ob der Wartungsmodus abgelaufen ist"""
        try:
            state = await self._get_state()
            if not state["active"]:
                return

            auto_end_str = state["auto_end_at"]
            if not auto_end_str:
                return

            auto_end = datetime.strptime(auto_end_str, "%Y-%m-%d %H:%M:%S")
            if datetime.now() >= auto_end:
                logger.warning(
                    "Wartungsmodus Auto-Timeout erreicht "
                    f"(nach {AUTO_TIMEOUT_HOURS}h) — deaktiviere automatisch"
                )
                await self._set_active(False)

                # Benachrichtigung im Admin-Channel senden (falls verfügbar)
                notifier = getattr(self.bot, "notifier", None)
                if notifier:
                    try:
                        await notifier.send_admin(
                            "Wartungsmodus automatisch beendet",
                            f"Der Wartungsmodus wurde nach {AUTO_TIMEOUT_HOURS} Stunden "
                            "automatisch deaktiviert (Sicherheitsnetz).",
                        )
                    except Exception as e:
                        logger.debug(f"Admin-Benachrichtigung fehlgeschlagen: {e}")

        except Exception as e:
            logger.error(f"Auto-Timeout-Check Fehler: {e}", exc_info=True)

    @_auto_timeout_check.before_loop
    async def _before_auto_timeout(self) -> None:
        """Wartet bis der Bot bereit ist"""
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Slash Commands
    # ------------------------------------------------------------------

    @wartung_grp.command(
        name="an",
        description="Wartungsmodus aktivieren — alle Server-Kacheln zeigen 'Wartung'",
    )
    @app_commands.describe(
        grund="Optionaler Grund für die Wartung (z.B. 'Server-Update')"
    )
    async def wartung_an(
        self,
        interaction: discord.Interaction,
        grund: str = "",
    ) -> None:
        """Aktiviert den globalen Wartungsmodus"""
        await interaction.response.defer(ephemeral=True)

        # Pruefen ob bereits aktiv
        state = await self._get_state()
        if state["active"]:
            await interaction.followup.send(
                "Der Wartungsmodus ist bereits aktiv.\n"
                f"Grund: {state['reason'] or 'Kein Grund angegeben'}\n"
                f"Aktiviert von: {state['activated_by']}\n"
                f"Automatisches Ende: {state['auto_end_at']}",
                ephemeral=True,
            )
            return

        # Wartungsmodus aktivieren
        activated_by = str(interaction.user)
        await self._set_active(True, reason=grund, activated_by=activated_by)

        auto_end = (
            datetime.now() + timedelta(hours=AUTO_TIMEOUT_HOURS)
        ).strftime("%d.%m.%Y %H:%M")

        # Embed für die Bestaetigung
        embed = discord.Embed(
            title="Wartungsmodus aktiviert",
            color=COLOR_WARNING,
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="Grund",
            value=grund or "Kein Grund angegeben",
            inline=False,
        )
        embed.add_field(
            name="Automatisches Ende",
            value=f"{auto_end} (nach {AUTO_TIMEOUT_HOURS}h)",
            inline=False,
        )
        embed.set_footer(text=f"Aktiviert von {interaction.user.display_name}")

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Oeffentliche Nachricht im Channel
        public_embed = discord.Embed(
            title="Wartungsmodus aktiviert",
            description=(
                f"Alle Server befinden sich im Wartungsmodus.\n"
                f"**Grund:** {grund or 'Nicht angegeben'}"
            ),
            color=COLOR_WARNING,
            timestamp=datetime.now(),
        )
        public_embed.set_footer(
            text=f"Aktiviert von {interaction.user.display_name}"
        )
        await interaction.channel.send(embed=public_embed)

        logger.info(
            f"Wartungsmodus aktiviert von {interaction.user} — "
            f"Grund: {grund or 'keiner'}"
        )

    @wartung_grp.command(
        name="aus",
        description="Wartungsmodus deaktivieren — normaler Status wird wieder angezeigt",
    )
    async def wartung_aus(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Deaktiviert den globalen Wartungsmodus"""
        await interaction.response.defer(ephemeral=True)

        # Pruefen ob überhaupt aktiv
        state = await self._get_state()
        if not state["active"]:
            await interaction.followup.send(
                "Der Wartungsmodus ist nicht aktiv.",
                ephemeral=True,
            )
            return

        # Wartungsmodus deaktivieren
        await self._set_active(False)

        embed = discord.Embed(
            title="Wartungsmodus deaktiviert",
            description="Alle Server zeigen wieder den normalen Status an.",
            color=COLOR_SUCCESS,
            timestamp=datetime.now(),
        )
        embed.set_footer(
            text=f"Deaktiviert von {interaction.user.display_name}"
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Oeffentliche Nachricht im Channel
        public_embed = discord.Embed(
            title="Wartungsmodus beendet",
            description="Alle Server sind wieder im Normalbetrieb.",
            color=COLOR_SUCCESS,
            timestamp=datetime.now(),
        )
        public_embed.set_footer(
            text=f"Beendet von {interaction.user.display_name}"
        )
        await interaction.channel.send(embed=public_embed)

        logger.info(f"Wartungsmodus deaktiviert von {interaction.user}")

    @wartung_grp.command(
        name="status",
        description="Aktuellen Wartungsstatus anzeigen",
    )
    async def wartung_status(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Zeigt den aktuellen Wartungsstatus an"""
        await interaction.response.defer(ephemeral=True)

        state = await self._get_state()

        if state["active"]:
            embed = discord.Embed(
                title="Wartungsmodus: AKTIV",
                color=COLOR_WARNING,
                timestamp=datetime.now(),
            )
            embed.add_field(
                name="Grund",
                value=state["reason"] or "Kein Grund angegeben",
                inline=False,
            )
            embed.add_field(
                name="Aktiviert von",
                value=state["activated_by"] or "Unbekannt",
                inline=True,
            )
            embed.add_field(
                name="Aktiviert am",
                value=state["activated_at"] or "Unbekannt",
                inline=True,
            )
            embed.add_field(
                name="Automatisches Ende",
                value=state["auto_end_at"] or "Nicht gesetzt",
                inline=True,
            )

            # Verbleibende Zeit berechnen
            if state["auto_end_at"]:
                try:
                    auto_end = datetime.strptime(
                        state["auto_end_at"], "%Y-%m-%d %H:%M:%S"
                    )
                    remaining = auto_end - datetime.now()
                    if remaining.total_seconds() > 0:
                        hours = int(remaining.total_seconds() // 3600)
                        minutes = int((remaining.total_seconds() % 3600) // 60)
                        embed.add_field(
                            name="Verbleibend",
                            value=f"{hours}h {minutes}min",
                            inline=True,
                        )
                except ValueError:
                    pass
        else:
            embed = discord.Embed(
                title="Wartungsmodus: INAKTIV",
                description="Alle Server zeigen den normalen Status an.",
                color=COLOR_SUCCESS,
                timestamp=datetime.now(),
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # Oeffentliche API für andere Cogs / Module
    # ------------------------------------------------------------------

    async def is_maintenance_active(self) -> bool:
        """Prueft ob der Wartungsmodus aktiv ist (für externe Abfrage)"""
        state = await self._get_state()
        return state["active"]

    # ------------------------------------------------------------------
    # Fehlerbehandlung
    # ------------------------------------------------------------------

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung für diesen Befehl.", ephemeral=True
                )
        else:
            logger.error(
                f"Wartungsmodus Command-Fehler: {error}", exc_info=True
            )
            msg = f"Fehler: {str(error)[:200]}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Cog laden"""
    await bot.add_cog(MaintenanceModeCog(bot))

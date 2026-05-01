"""
Custom Commands Cog — Benutzerdefinierte Text-Antwort-Befehle

Ermöglicht Admins das Erstellen, Bearbeiten und Loeschen von
benutzerdefinierten Befehlen, die per !<name> im Chat ausgeloest werden.
Unterstuetzt einfache Textantworten und Embed-Format.

Command-Struktur (app_commands.Group):
  /customcmd add <name> <response>         — Neuen Befehl erstellen (Admin)
  /customcmd remove <name>                 — Befehl löschen (Admin)
  /customcmd edit <name> <new_response>    — Antwort bearbeiten (Admin)
  /customcmd list                          — Alle Befehle auflisten (Alle)
  /customcmd info <name>                   — Details eines Befehls anzeigen

Listener:
  on_message — Erkennt !<command_name> und sendet die gespeicherte Antwort.
               Unterstuetzt Embed-Format wenn die Antwort mit {embed} beginnt.

Variablen in Antworten:
  {user}        -> Mention des aufrufenden Users
  {server}      -> Name des Servers (Guild)
  {date}        -> Aktuelles Datum (DD.MM.YYYY)
  {membercount} -> Aktuelle Mitgliederzahl

Persistenz: SQLite-Tabelle 'custom_commands'
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.database.db_manager import get_db
from utils.logger import get_logger
from utils.permissions import admin_only

logger = get_logger("cogs.custom_commands")

# SQL für Tabellenerstellung
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS custom_commands (
    name TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    category TEXT DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    uses INTEGER DEFAULT 0
)
"""


class CustomCommandsCog(commands.Cog):
    """Benutzerdefinierte Text-Antwort-Befehle per !<name> im Chat"""

    cc_grp = app_commands.Group(
        name="customcmd",
        description="Custom Commands — Eigene Befehle verwalten",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Cog Lifecycle
    # ------------------------------------------------------------------

    async def cog_load(self) -> None:
        """Tabelle erstellen falls nicht vorhanden und Cog initialisieren"""
        await self._ensure_table()
        logger.info("Custom-Commands-Cog geladen")

    async def cog_unload(self) -> None:
        logger.info("Custom-Commands-Cog entladen")

    # ------------------------------------------------------------------
    # Datenbank-Hilfsmethoden
    # ------------------------------------------------------------------

    async def _ensure_table(self) -> None:
        """Stellt sicher, dass die Tabelle 'custom_commands' existiert"""
        try:
            db = await get_db()
            await db.execute(CREATE_TABLE_SQL)
            await db.commit()
            logger.info("Tabelle 'custom_commands' sichergestellt")
        except Exception as e:
            logger.error(f"Fehler beim Erstellen der Tabelle: {e}", exc_info=True)

    async def _get_command(self, name: str) -> Optional[dict]:
        """
        Einzelnen Custom Command aus der Datenbank holen.

        Args:
            name: Name des Commands (case-insensitive)

        Returns:
            Dict mit Command-Daten oder None wenn nicht gefunden
        """
        db = await get_db()
        cursor = await db.execute(
            "SELECT name, response, category, created_by, created_at, uses "
            "FROM custom_commands WHERE LOWER(name) = LOWER(?)",
            (name,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "name": row[0],
            "response": row[1],
            "category": row[2],
            "created_by": row[3],
            "created_at": row[4],
            "uses": row[5],
        }

    async def _get_all_commands(self) -> list[dict]:
        """
        Alle Custom Commands aus der Datenbank holen.

        Returns:
            Liste mit Command-Dicts, sortiert nach Name
        """
        db = await get_db()
        cursor = await db.execute(
            "SELECT name, response, category, created_by, created_at, uses "
            "FROM custom_commands ORDER BY name ASC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "name": row[0],
                "response": row[1],
                "category": row[2],
                "created_by": row[3],
                "created_at": row[4],
                "uses": row[5],
            }
            for row in rows
        ]

    async def _increment_uses(self, name: str) -> None:
        """Nutzungszaehler für einen Command erhoehen"""
        try:
            db = await get_db()
            await db.execute(
                "UPDATE custom_commands SET uses = uses + 1 WHERE LOWER(name) = LOWER(?)",
                (name,),
            )
            await db.commit()
        except Exception as e:
            logger.warning(f"Fehler beim Erhoehen des Nutzungszaehlers für '{name}': {e}")

    # ------------------------------------------------------------------
    # Variablen-Ersetzung
    # ------------------------------------------------------------------

    def _replace_variables(
        self, text: str, message: discord.Message
    ) -> str:
        """
        Variablen in der Antwort ersetzen.

        Unterstuetzte Variablen:
          {user}        -> Mention des Users
          {server}      -> Guild-Name
          {date}        -> Aktuelles Datum (DD.MM.YYYY)
          {membercount} -> Mitgliederzahl des Servers

        Args:
            text: Roher Antwort-Text mit Platzhaltern
            message: Die ausloesende Discord-Nachricht

        Returns:
            Text mit ersetzten Variablen
        """
        replacements = {
            "{user}": message.author.mention,
            "{server}": message.guild.name if message.guild else "DM",
            "{date}": datetime.now().strftime("%d.%m.%Y"),
            "{membercount}": str(message.guild.member_count) if message.guild else "0",
        }
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        return text

    # ------------------------------------------------------------------
    # Embed-Parsing
    # ------------------------------------------------------------------

    def _parse_embed_response(self, text: str, message: discord.Message) -> discord.Embed:
        """
        Erstellt ein Embed aus einer Antwort die mit {embed} beginnt.

        Erwartetes Format nach dem {embed}-Praefix:
        JSON-Objekt mit optionalen Feldern: title, description, color, footer,
        thumbnail, image, fields (Liste von {name, value, inline}).

        Alle Textfelder unterstuetzen Variablen-Ersetzung.

        Falls das JSON ungueltig ist, wird der rohe Text als Embed-Description verwendet.

        Args:
            text: Antwort-Text (ohne {embed}-Praefix)
            message: Die ausloesende Discord-Nachricht

        Returns:
            Fertiges discord.Embed-Objekt
        """
        # {embed}-Praefix entfernen
        raw = text[len("{embed}"):].strip()

        # Variablen im gesamten Text ersetzen
        raw = self._replace_variables(raw, message)

        # JSON parsen versuchen
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # Kein gueltiges JSON — rohen Text als Description verwenden
            return discord.Embed(
                description=raw if raw else "Leere Antwort",
                color=0x5865F2,
            )

        # Embed aus JSON-Daten bauen
        embed = discord.Embed()

        if "title" in data:
            embed.title = str(data["title"])

        if "description" in data:
            embed.description = str(data["description"])

        if "color" in data:
            try:
                color_val = data["color"]
                if isinstance(color_val, str):
                    color_val = int(color_val.lstrip("#"), 16)
                embed.color = discord.Color(int(color_val))
            except (ValueError, TypeError):
                embed.color = discord.Color(0x5865F2)
        else:
            embed.color = discord.Color(0x5865F2)

        if "footer" in data:
            embed.set_footer(text=str(data["footer"]))

        if "thumbnail" in data:
            embed.set_thumbnail(url=str(data["thumbnail"]))

        if "image" in data:
            embed.set_image(url=str(data["image"]))

        # Felder hinzufuegen
        for field in data.get("fields", []):
            fname = field.get("name", "")
            fvalue = field.get("value", "")
            if fname and fvalue:
                embed.add_field(
                    name=str(fname),
                    value=str(fvalue),
                    inline=field.get("inline", True),
                )

        return embed

    # ==================================================================
    # /customcmd add <name> <response>
    # ==================================================================

    @cc_grp.command(
        name="add",
        description="Neuen Custom Command erstellen",
    )
    @app_commands.describe(
        name="Name des Commands (ohne !-Praefix)",
        response="Antworttext (oder {embed}{...} für Embed-Format)",
    )
    @admin_only()
    async def cc_add(
        self,
        interaction: discord.Interaction,
        name: str,
        response: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        # Name normalisieren und validieren
        cmd_name = name.strip().lower()
        if not cmd_name:
            await interaction.followup.send(
                "Der Command-Name darf nicht leer sein.", ephemeral=True
            )
            return

        if " " in cmd_name:
            await interaction.followup.send(
                "Der Command-Name darf keine Leerzeichen enthalten.",
                ephemeral=True,
            )
            return

        # Pruefen ob Command bereits existiert
        existing = await self._get_command(cmd_name)
        if existing is not None:
            await interaction.followup.send(
                f"Der Command `!{cmd_name}` existiert bereits.\n"
                f"Verwende `/customcmd edit {cmd_name} <neue_antwort>` zum Bearbeiten.",
                ephemeral=True,
            )
            return

        # In Datenbank speichern
        try:
            db = await get_db()
            await db.execute(
                "INSERT INTO custom_commands (name, response, category, created_by, created_at, uses) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    cmd_name,
                    response,
                    "",
                    str(interaction.user),
                    datetime.now().isoformat(),
                    0,
                ),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Fehler beim Erstellen von Command '{cmd_name}': {e}", exc_info=True)
            await interaction.followup.send(
                f"Fehler beim Speichern: {e}", ephemeral=True
            )
            return

        # Antwort-Vorschau (gekuerzt)
        preview = response[:100] + "..." if len(response) > 100 else response

        embed = discord.Embed(
            title="Custom Command erstellt",
            color=0x2ECC71,
        )
        embed.add_field(name="Command", value=f"`!{cmd_name}`", inline=True)
        embed.add_field(name="Erstellt von", value=interaction.user.mention, inline=True)
        embed.add_field(name="Antwort", value=f"```{preview}```", inline=False)
        embed.set_footer(text="Nutze den Command mit !name im Chat")

        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"Custom Command erstellt: '!{cmd_name}' von {interaction.user}"
        )

    # ==================================================================
    # /customcmd remove <name>
    # ==================================================================

    @cc_grp.command(
        name="remove",
        description="Custom Command löschen",
    )
    @app_commands.describe(
        name="Name des zu löschenden Commands",
    )
    @admin_only()
    async def cc_remove(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        cmd_name = name.strip().lower()

        # Pruefen ob Command existiert
        existing = await self._get_command(cmd_name)
        if existing is None:
            await interaction.followup.send(
                f"Der Command `!{cmd_name}` existiert nicht.",
                ephemeral=True,
            )
            return

        # Aus Datenbank löschen
        try:
            db = await get_db()
            await db.execute(
                "DELETE FROM custom_commands WHERE LOWER(name) = LOWER(?)",
                (cmd_name,),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Fehler beim Loeschen von Command '{cmd_name}': {e}", exc_info=True)
            await interaction.followup.send(
                f"Fehler beim Loeschen: {e}", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Custom Command gelöscht",
            description=f"Der Command `!{cmd_name}` wurde entfernt.",
            color=0xE74C3C,
        )
        embed.add_field(
            name="Statistik",
            value=f"Wurde **{existing['uses']}** Mal verwendet",
            inline=False,
        )
        embed.set_footer(text=f"Geloescht von {interaction.user.display_name}")

        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"Custom Command gelöscht: '!{cmd_name}' von {interaction.user} "
            f"(hatte {existing['uses']} Nutzungen)"
        )

    # ==================================================================
    # /customcmd edit <name> <new_response>
    # ==================================================================

    @cc_grp.command(
        name="edit",
        description="Antwort eines Custom Commands bearbeiten",
    )
    @app_commands.describe(
        name="Name des zu bearbeitenden Commands",
        new_response="Neue Antwort für den Command",
    )
    @admin_only()
    async def cc_edit(
        self,
        interaction: discord.Interaction,
        name: str,
        new_response: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        cmd_name = name.strip().lower()

        # Pruefen ob Command existiert
        existing = await self._get_command(cmd_name)
        if existing is None:
            await interaction.followup.send(
                f"Der Command `!{cmd_name}` existiert nicht.\n"
                f"Erstelle ihn zuerst mit `/customcmd add`.",
                ephemeral=True,
            )
            return

        # Antwort in Datenbank aktualisieren
        try:
            db = await get_db()
            await db.execute(
                "UPDATE custom_commands SET response = ? WHERE LOWER(name) = LOWER(?)",
                (new_response, cmd_name),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Fehler beim Bearbeiten von Command '{cmd_name}': {e}", exc_info=True)
            await interaction.followup.send(
                f"Fehler beim Aktualisieren: {e}", ephemeral=True
            )
            return

        # Vorschau der alten und neuen Antwort
        old_preview = existing["response"][:80] + "..." if len(existing["response"]) > 80 else existing["response"]
        new_preview = new_response[:80] + "..." if len(new_response) > 80 else new_response

        embed = discord.Embed(
            title="Custom Command bearbeitet",
            color=0xF39C12,
        )
        embed.add_field(name="Command", value=f"`!{cmd_name}`", inline=False)
        embed.add_field(name="Alte Antwort", value=f"```{old_preview}```", inline=False)
        embed.add_field(name="Neue Antwort", value=f"```{new_preview}```", inline=False)
        embed.set_footer(text=f"Bearbeitet von {interaction.user.display_name}")

        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            f"Custom Command bearbeitet: '!{cmd_name}' von {interaction.user}"
        )

    # ==================================================================
    # /customcmd list
    # ==================================================================

    @cc_grp.command(
        name="list",
        description="Alle Custom Commands auflisten",
    )
    async def cc_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        commands_list = await self._get_all_commands()

        if not commands_list:
            await interaction.followup.send(
                "Keine Custom Commands vorhanden.\n"
                "Admins können welche mit `/customcmd add` erstellen.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Custom Commands ({len(commands_list)})",
            color=0x5865F2,
        )

        # Commands in Gruppen aufteilen (max. 25 Felder pro Embed)
        # Kompakte Darstellung: mehrere Commands pro Feld
        lines: list[str] = []
        for cmd in commands_list:
            # Antwort-Typ bestimmen
            response_type = "Embed" if cmd["response"].startswith("{embed}") else "Text"
            lines.append(
                f"`!{cmd['name']}` — {response_type} | {cmd['uses']}x genutzt"
            )

        # In Bloecke aufteilen (max. 15 pro Feld, damit Embed nicht zu gross wird)
        chunk_size = 15
        for i in range(0, len(lines), chunk_size):
            chunk = lines[i : i + chunk_size]
            field_name = "Befehle" if i == 0 else f"Befehle (Forts.)"
            embed.add_field(
                name=field_name,
                value="\n".join(chunk),
                inline=False,
            )

        embed.set_footer(text="Verwende !<name> im Chat um einen Command auszufuehren")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # /customcmd info <name>
    # ==================================================================

    @cc_grp.command(
        name="info",
        description="Details eines Custom Commands anzeigen",
    )
    @app_commands.describe(
        name="Name des Commands",
    )
    async def cc_info(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        cmd_name = name.strip().lower()
        cmd = await self._get_command(cmd_name)

        if cmd is None:
            await interaction.followup.send(
                f"Der Command `!{cmd_name}` existiert nicht.",
                ephemeral=True,
            )
            return

        # Erstellungsdatum formatieren
        created_at = cmd["created_at"]
        try:
            dt = datetime.fromisoformat(created_at)
            created_at = dt.strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            pass

        # Antwort-Typ bestimmen
        is_embed = cmd["response"].startswith("{embed}")
        response_type = "Embed (JSON)" if is_embed else "Text"

        # Antwort-Vorschau
        preview = cmd["response"][:200] + "..." if len(cmd["response"]) > 200 else cmd["response"]

        embed = discord.Embed(
            title=f"Command-Info: !{cmd['name']}",
            color=0x5865F2,
        )
        embed.add_field(name="Name", value=f"`!{cmd['name']}`", inline=True)
        embed.add_field(name="Typ", value=response_type, inline=True)
        embed.add_field(name="Nutzungen", value=str(cmd["uses"]), inline=True)
        embed.add_field(name="Erstellt von", value=cmd["created_by"], inline=True)
        embed.add_field(name="Erstellt am", value=created_at, inline=True)

        if cmd["category"]:
            embed.add_field(name="Kategorie", value=cmd["category"], inline=True)

        embed.add_field(name="Antwort", value=f"```{preview}```", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # Listener: on_message — Custom Commands per !<name> ausführen
    # ==================================================================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        Erkennt Custom Commands in Nachrichten.

        Reagiert auf Nachrichten die mit !<command_name> beginnen.
        Unterstuetzt einfache Text-Antworten und Embed-Format ({embed}).
        """
        # Bots ignorieren
        if message.author.bot:
            return

        # Nur in Guilds (keine DMs)
        if message.guild is None:
            return

        # Nur Nachrichten die mit ! beginnen
        if not message.content.startswith("!"):
            return

        # Command-Name extrahieren (erstes Wort nach dem !)
        content = message.content.strip()
        parts = content.split(maxsplit=1)
        cmd_name = parts[0][1:].lower()  # ! entfernen und lowercase

        if not cmd_name:
            return

        # Command in Datenbank suchen
        cmd = await self._get_command(cmd_name)
        if cmd is None:
            return

        response = cmd["response"]

        try:
            if response.startswith("{embed}"):
                # Embed-Antwort
                embed = self._parse_embed_response(response, message)
                await message.channel.send(embed=embed)
            else:
                # Text-Antwort mit Variablen-Ersetzung
                text = self._replace_variables(response, message)
                await message.channel.send(text)

            # Nutzungszaehler erhoehen
            await self._increment_uses(cmd_name)

            logger.debug(
                f"Custom Command ausgefuehrt: '!{cmd_name}' von {message.author} "
                f"in #{message.channel.name}"
            )
        except discord.Forbidden:
            logger.warning(
                f"Keine Berechtigung zum Senden in #{message.channel.name} "
                f"für Command '!{cmd_name}'"
            )
        except discord.HTTPException as e:
            logger.error(
                f"Fehler beim Ausfuehren von Command '!{cmd_name}': {e}"
            )

    # ==================================================================
    # Autocomplete für Command-Namen
    # ==================================================================

    async def _name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """
        Autocomplete für den 'name'-Parameter.

        Schlaegt vorhandene Command-Namen vor basierend auf der Eingabe.

        Args:
            interaction: Discord Interaction
            current: Aktuelle Eingabe des Users

        Returns:
            Liste mit passenden Choices (max. 25)
        """
        all_cmds = await self._get_all_commands()
        current_lower = current.lower()
        matches = [
            app_commands.Choice(name=f"!{cmd['name']}", value=cmd["name"])
            for cmd in all_cmds
            if current_lower in cmd["name"].lower()
        ]
        return matches[:25]

    # Autocomplete an die relevanten Commands binden
    cc_remove.autocomplete("name")(_name_autocomplete)
    cc_edit.autocomplete("name")(_name_autocomplete)
    cc_info.autocomplete("name")(_name_autocomplete)

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
    """Cog laden"""
    await bot.add_cog(CustomCommandsCog(bot))

"""
Roadmap Cog — was am Bot geplant ist, was gerade entsteht, was fertig ist (Marshal, 04.11.2026).

Ein Board-Embed im Roadmap-Kanal zeigt vier Abschnitte: In Arbeit, Geplant,
Fertig und Wünsche. Bei neuen Vorhaben und Statuswechseln schreibt der Bot eine
kurze Meldung in den Kanal und pingt die Update-Rolle; das Board rutscht danach
wieder ans Ende, damit es die letzte Nachricht bleibt.

  /roadmap posten                         — Board in diesem Kanal verankern (Admin)
  /roadmap neu <titel> [status] [ping]    — Vorhaben anlegen (Admin)
  /roadmap status <nummer> <status> [ping] — Status ändern (Admin)
  /roadmap entfernen <nummer>             — Eintrag löschen (Admin)
  /roadmap updaterolle <rolle>            — Rolle, die bei Meldungen gepingt wird (Admin)
  /wunsch <text>                          — Funktionswunsch einreichen (alle)

Daten: Tabelle ``roadmap`` (Migration v16). Kanal, Board-Nachricht und
Update-Rolle stehen in GuildConfig unter ``roadmap.*``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.database.db_manager import get_db, get_read_db
from modules.guild_context import GuildConfig
from utils.logger import get_logger
from utils.permissions import admin_only

logger = get_logger("cogs.roadmap")

STATUS = {
    "arbeit": ("🔨", "In Arbeit"),
    "geplant": ("🗓️", "Geplant"),
    "fertig": ("✅", "Fertig"),
    "wunsch": ("💡", "Wünsche"),
}
STATUS_CHOICES = [app_commands.Choice(name=f"{e} {n}", value=k) for k, (e, n) in STATUS.items()]
FERTIG_ANZEIGEN = 8
WUENSCHE_ANZEIGEN = 10
TITEL_MAX = 120
WUNSCH_OFFEN_MAX = 3
FARBE = 0x3BA6FF


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _zeile(row: dict[str, Any], mit_autor: bool = False) -> str:
    text = f"`#{row['id']}` {discord.utils.escape_markdown(row['titel'])}"
    if mit_autor and row.get("author_name"):
        text += f" — *{discord.utils.escape_markdown(row['author_name'])}*"
    return text


def _feld(zeilen: list[str], leer: str) -> str:
    """Zeilen auf das Feldlimit von 1024 Zeichen kuerzen."""
    if not zeilen:
        return leer
    out, laenge = [], 0
    for z in zeilen:
        if laenge + len(z) + 1 > 1000:
            out.append("…")
            break
        out.append(z)
        laenge += len(z) + 1
    return "\n".join(out)


class RoadmapCog(commands.Cog):
    """Roadmap-Board und Funktionswünsche."""

    grp = app_commands.Group(name="roadmap", description="Roadmap des Bots pflegen")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Daten
    # ------------------------------------------------------------------

    async def _eintraege(self, guild_id: int) -> list[dict[str, Any]]:
        db = await get_read_db()
        cur = await db.execute(
            "SELECT id, status, titel, author_id, author_name, created_at, updated_at "
            "FROM roadmap WHERE guild_id = ? ORDER BY COALESCE(updated_at, created_at) DESC, id DESC",
            (str(guild_id),),
        )
        spalten = [d[0] for d in cur.description]
        return [dict(zip(spalten, r)) for r in await cur.fetchall()]

    async def _eintrag(self, guild_id: int, nummer: int) -> Optional[dict[str, Any]]:
        return next((e for e in await self._eintraege(guild_id) if e["id"] == nummer), None)

    async def _anlegen(self, guild_id: int, status: str, titel: str, user: discord.abc.User) -> int:
        db = await get_db()
        cur = await db.execute(
            "INSERT INTO roadmap (guild_id, status, titel, author_id, author_name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(guild_id), status, titel, str(user.id), getattr(user, "display_name", user.name), _jetzt()),
        )
        await db.commit()
        return int(cur.lastrowid)

    # ------------------------------------------------------------------
    # Board
    # ------------------------------------------------------------------

    async def baue_embed(self, guild_id: int) -> discord.Embed:
        rows = await self._eintraege(guild_id)
        nach = {k: [r for r in rows if r["status"] == k] for k in STATUS}
        embed = discord.Embed(
            title="🗺️ Roadmap",
            description="Woran am Bot gearbeitet wird und was als Nächstes kommt. "
                        "Eigene Ideen reichst du mit `/wunsch` ein.",
            colour=discord.Colour(FARBE),
        )
        for k in ("arbeit", "geplant"):
            e, n = STATUS[k]
            embed.add_field(name=f"{e} {n}", value=_feld([_zeile(r) for r in nach[k]], "Gerade nichts."), inline=False)
        e, n = STATUS["fertig"]
        embed.add_field(name=f"{e} {n}", value=_feld([_zeile(r) for r in nach["fertig"][:FERTIG_ANZEIGEN]],
                                                      "Noch nichts abgehakt."), inline=False)
        e, n = STATUS["wunsch"]
        embed.add_field(name=f"{e} {n}", value=_feld([_zeile(r, True) for r in nach["wunsch"][:WUENSCHE_ANZEIGEN]],
                                                      "Noch keine Wünsche."), inline=False)
        embed.set_footer(text="Updates per Ping: Rolle „Bot-Updates“ im Rollen-Kanal holen")
        return embed

    async def board_aktualisieren(self, guild: discord.Guild, nach_unten: bool = False,
                                  kanal: Optional[discord.TextChannel] = None) -> Optional[discord.Message]:
        """Board bearbeiten oder (nach_unten / neuer Kanal) loeschen und neu posten."""
        kanal_id = await GuildConfig.get(guild.id, "roadmap.kanal_id", "")
        board_id = await GuildConfig.get(guild.id, "roadmap.board_id", "")
        alt_kanal = guild.get_channel(int(kanal_id)) if str(kanal_id).isdigit() else None
        ziel = kanal or alt_kanal
        if not isinstance(ziel, discord.TextChannel):
            return None
        embed = await self.baue_embed(guild.id)
        alt = None
        if isinstance(alt_kanal, discord.TextChannel) and str(board_id).isdigit():
            try:
                alt = await alt_kanal.fetch_message(int(board_id))
            except discord.NotFound:
                alt = None
        if alt is not None and not nach_unten and alt.channel.id == ziel.id:
            await alt.edit(embed=embed)
            return alt
        if alt is not None:
            try:
                await alt.delete()
            except discord.HTTPException as e:
                logger.warning(f"Roadmap: altes Board nicht geloescht: {e}")
        msg = await ziel.send(embed=embed)
        await GuildConfig.set(guild.id, "roadmap.kanal_id", str(ziel.id), updated_by="roadmap")
        await GuildConfig.set(guild.id, "roadmap.board_id", str(msg.id), updated_by="roadmap")
        return msg

    async def _melden(self, guild: discord.Guild, text: str, ping: bool) -> None:
        """Kurze Meldung in den Roadmap-Kanal, optional mit Ping der Update-Rolle."""
        kanal_id = await GuildConfig.get(guild.id, "roadmap.kanal_id", "")
        kanal = guild.get_channel(int(kanal_id)) if str(kanal_id).isdigit() else None
        if not isinstance(kanal, discord.TextChannel):
            return
        rolle_id = await GuildConfig.get(guild.id, "roadmap.update_rolle_id", "")
        rolle = guild.get_role(int(rolle_id)) if ping and str(rolle_id).isdigit() else None
        inhalt = f"{rolle.mention} {text}" if rolle else text
        try:
            await kanal.send(
                inhalt,
                allowed_mentions=discord.AllowedMentions(everyone=False, users=False,
                                                         roles=[rolle] if rolle else False),
            )
        except discord.HTTPException as e:
            logger.warning(f"Roadmap-Meldung fehlgeschlagen: {e}")
            return
        await self.board_aktualisieren(guild, nach_unten=True)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @grp.command(name="posten", description="Roadmap-Board in diesem Kanal verankern")
    @admin_only()
    async def posten(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.followup.send("Nur in einem Textkanal.", ephemeral=True)
            return
        await self.board_aktualisieren(interaction.guild, nach_unten=True, kanal=interaction.channel)
        await interaction.followup.send("Board steht.", ephemeral=True)

    @grp.command(name="neu", description="Vorhaben in die Roadmap aufnehmen")
    @app_commands.describe(titel="Kurz, was kommt", status="Abschnitt (Standard: Geplant)",
                           ping="Update-Rolle anpingen")
    @app_commands.choices(status=STATUS_CHOICES)
    @admin_only()
    async def neu(self, interaction: discord.Interaction, titel: str,
                  status: Optional[app_commands.Choice[str]] = None, ping: bool = True) -> None:
        await interaction.response.defer(ephemeral=True)
        st = status.value if status else "geplant"
        titel = titel.strip()[:TITEL_MAX]
        nummer = await self._anlegen(interaction.guild.id, st, titel, interaction.user)
        e, n = STATUS[st]
        await self._melden(interaction.guild, f"{e} **{n}:** {discord.utils.escape_markdown(titel)}", ping)
        await self.board_aktualisieren(interaction.guild)
        await interaction.followup.send(f"`#{nummer}` angelegt.", ephemeral=True)

    @grp.command(name="status", description="Status eines Eintrags ändern")
    @app_commands.describe(nummer="Nummer des Eintrags", status="Neuer Abschnitt", ping="Update-Rolle anpingen")
    @app_commands.choices(status=STATUS_CHOICES)
    @admin_only()
    async def status(self, interaction: discord.Interaction, nummer: int,
                     status: app_commands.Choice[str], ping: bool = True) -> None:
        await interaction.response.defer(ephemeral=True)
        row = await self._eintrag(interaction.guild.id, nummer)
        if row is None:
            await interaction.followup.send(f"`#{nummer}` gibt es nicht.", ephemeral=True)
            return
        db = await get_db()
        await db.execute("UPDATE roadmap SET status = ?, updated_at = ? WHERE id = ? AND guild_id = ?",
                         (status.value, _jetzt(), nummer, str(interaction.guild.id)))
        await db.commit()
        e, n = STATUS[status.value]
        # Wuensche, die zurueckgestuft werden, sind keine Meldung wert
        if status.value != "wunsch":
            await self._melden(interaction.guild, f"{e} **{n}:** {discord.utils.escape_markdown(row['titel'])}", ping)
        await self.board_aktualisieren(interaction.guild)
        await interaction.followup.send(f"`#{nummer}` steht jetzt unter {n}.", ephemeral=True)

    @grp.command(name="entfernen", description="Eintrag löschen")
    @admin_only()
    async def entfernen(self, interaction: discord.Interaction, nummer: int) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()
        cur = await db.execute("DELETE FROM roadmap WHERE id = ? AND guild_id = ?",
                               (nummer, str(interaction.guild.id)))
        await db.commit()
        await self.board_aktualisieren(interaction.guild)
        await interaction.followup.send(
            f"`#{nummer}` gelöscht." if cur.rowcount else f"`#{nummer}` gibt es nicht.", ephemeral=True)

    @grp.command(name="updaterolle", description="Rolle, die bei Roadmap-Meldungen gepingt wird")
    @admin_only()
    async def updaterolle(self, interaction: discord.Interaction, rolle: discord.Role) -> None:
        await GuildConfig.set(interaction.guild.id, "roadmap.update_rolle_id", str(rolle.id),
                              updated_by=str(interaction.user.id))
        hinweis = "" if rolle.mentionable else " Achtung: Die Rolle ist nicht erwähnbar, der Ping kommt so nicht an."
        await interaction.response.send_message(f"Meldungen pingen jetzt {rolle.name}.{hinweis}", ephemeral=True)

    @app_commands.command(name="wunsch", description="Funktion für den Bot oder Server wünschen")
    @app_commands.describe(text="Was soll der Bot können?")
    @app_commands.checks.cooldown(1, 600, key=lambda i: i.user.id)
    @app_commands.guild_only()
    async def wunsch(self, interaction: discord.Interaction, text: str) -> None:
        text = " ".join(text.split())[:TITEL_MAX]
        if len(text) < 5:
            await interaction.response.send_message("Beschreib den Wunsch bitte etwas genauer.", ephemeral=True)
            return
        offen = [r for r in await self._eintraege(interaction.guild.id)
                 if r["status"] == "wunsch" and r["author_id"] == str(interaction.user.id)]
        if len(offen) >= WUNSCH_OFFEN_MAX:
            await interaction.response.send_message(
                f"Du hast schon {len(offen)} offene Wünsche. Warte, bis einer bearbeitet ist.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        nummer = await self._anlegen(interaction.guild.id, "wunsch", text, interaction.user)
        await self.board_aktualisieren(interaction.guild)
        logger.info(f"Roadmap: Wunsch #{nummer} von {interaction.user.id}")
        await interaction.followup.send(f"Danke! Dein Wunsch steht als `#{nummer}` auf der Roadmap.", ephemeral=True)

    @wunsch.error
    async def wunsch_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"Nächster Wunsch in {int(error.retry_after // 60) + 1} Minuten möglich.", ephemeral=True)
            return
        logger.error(f"/wunsch fehlgeschlagen: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message("Das hat nicht geklappt.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RoadmapCog(bot))

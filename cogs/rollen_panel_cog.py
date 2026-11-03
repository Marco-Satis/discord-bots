"""
Rollen-Panel Cog — Rollen per Button holen und abgeben (Marshal, 04.11.2026).

Ein Embed im Rollen-Kanal erklaert jede Rolle in einem Satz, darunter steht je
Rolle ein Button. Klick = Rolle bekommen, zweiter Klick = Rolle abgeben. Die
Antwort ist nur fuer den Klickenden sichtbar.

  /rollenpanel posten              — Panel in diesem Kanal posten bzw. aktualisieren (Admin)
  /rollenpanel eintrag <rolle> <emoji> <text>  — Rolle aufnehmen oder Text aendern (Admin)
  /rollenpanel entfernen <rolle>   — Rolle aus dem Panel nehmen (Admin)

Konfiguration in GuildConfig:
  rollenpanel.eintraege   Liste [{"rolle": "<id>", "emoji": "🔔", "text": "..."}]
  rollenpanel.kanal_id    Kanal des Panels
  rollenpanel.nachricht_id  Nachricht des Panels

Sicherheit: Der Button traegt die Rollen-ID im custom_id. Vergeben wird nur, was
in ``rollenpanel.eintraege`` steht, was keine eigenen Rechte hat, nicht von einer
Integration verwaltet wird und unter der hoechsten Bot-Rolle liegt.
"""

from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.guild_context import GuildConfig
from utils.logger import get_logger
from utils.permissions import admin_only

logger = get_logger("cogs.rollen_panel")

TITEL = "🎭 Rollen holen"
EINLEITUNG = (
    "Hol dir die Rollen für die Bereiche, die dich interessieren. "
    "Ein Klick auf einen Button gibt dir die Rolle, ein zweiter Klick nimmt sie wieder weg."
)
FUSS = "Ohne Rolle bleiben die Kanäle ausgeblendet. Du kannst jederzeit wechseln."
FARBE = 0x7C4DFF
MAX_EINTRAEGE = 20  # 4 Reihen x 5 Buttons, eine Reihe bleibt frei
TEXT_MAX = 200


async def lade_eintraege(guild_id: int) -> list[dict[str, Any]]:
    """Panel-Eintraege aus der Guild-Config, kaputte Eintraege fallen weg."""
    roh = await GuildConfig.get(guild_id, "rollenpanel.eintraege", []) or []
    return [e for e in roh if isinstance(e, dict) and str(e.get("rolle", "")).isdigit()]


def rolle_vergebbar(rolle: discord.Role, bot_member: discord.Member) -> Optional[str]:
    """None wenn die Rolle per Panel vergeben werden darf, sonst der Grund."""
    if rolle.is_default() or rolle.managed:
        return "Diese Rolle kann nicht vergeben werden."
    if rolle.permissions.value != 0:
        return "Diese Rolle hat eigene Rechte und wird nicht per Panel vergeben."
    if rolle >= bot_member.top_role:
        return "Die Rolle steht über der Bot-Rolle."
    return None


def baue_embed(guild: discord.Guild, eintraege: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(title=TITEL, description=EINLEITUNG, colour=discord.Colour(FARBE))
    for e in eintraege:
        rolle = guild.get_role(int(e["rolle"]))
        if rolle is None:
            continue
        embed.add_field(
            name=f"{e.get('emoji') or '•'} {rolle.name}",
            value=str(e.get("text") or "—")[:1024],
            inline=False,
        )
    embed.set_footer(text=FUSS)
    return embed


def baue_view(guild: discord.Guild, eintraege: list[dict[str, Any]]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for e in eintraege[:MAX_EINTRAEGE]:
        rolle = guild.get_role(int(e["rolle"]))
        if rolle is None:
            continue
        view.add_item(RollenButton(rolle.id, rolle.name, e.get("emoji") or None))
    return view


class RollenButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rp:(?P<rolle>\d+)"):
    """Ein Button je Rolle. DynamicItem, damit er jeden Bot-Neustart ueberlebt."""

    def __init__(self, rolle_id: int, name: str = "Rolle", emoji: Optional[str] = None) -> None:
        self.rolle_id = rolle_id
        super().__init__(
            discord.ui.Button(
                label=name[:80], emoji=emoji, style=discord.ButtonStyle.secondary,
                custom_id=f"rp:{rolle_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match) -> "RollenButton":
        return cls(int(match["rolle"]), item.label or "Rolle", None)

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Nur auf dem Server möglich.", ephemeral=True)
            return
        erlaubt = {int(e["rolle"]) for e in await lade_eintraege(guild.id)}
        rolle = guild.get_role(self.rolle_id)
        if rolle is None or self.rolle_id not in erlaubt:
            await interaction.response.send_message(
                "Diese Rolle gibt es im Panel nicht mehr.", ephemeral=True)
            return
        grund = rolle_vergebbar(rolle, guild.me)
        if grund:
            await interaction.response.send_message(grund, ephemeral=True)
            return
        try:
            if rolle in member.roles:
                await member.remove_roles(rolle, reason="Rollen-Panel")
                text = f"Du hast **{discord.utils.escape_markdown(rolle.name)}** abgegeben."
            else:
                await member.add_roles(rolle, reason="Rollen-Panel")
                text = f"✅ Du hast jetzt **{discord.utils.escape_markdown(rolle.name)}**."
        except discord.Forbidden:
            logger.warning(f"Rollen-Panel: keine Berechtigung fuer Rolle {rolle.id}")
            await interaction.response.send_message(
                "Ich darf diese Rolle gerade nicht vergeben. Das Team ist informiert.", ephemeral=True)
            return
        except discord.HTTPException as e:
            logger.error(f"Rollen-Panel: Rollenwechsel fehlgeschlagen: {e}")
            await interaction.response.send_message(
                "Das hat nicht geklappt, versuch es gleich noch einmal.", ephemeral=True)
            return
        logger.info(f"Rollen-Panel: {member.id} -> {rolle.name}")
        await interaction.response.send_message(text, ephemeral=True)


class RollenPanelCog(commands.Cog):
    """Rollen per Button-Panel."""

    grp = app_commands.Group(name="rollenpanel", description="Rollen-Panel mit Buttons verwalten")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(RollenButton)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(RollenButton)

    async def panel_aktualisieren(
        self, guild: discord.Guild, kanal: Optional[discord.abc.Messageable] = None,
    ) -> Optional[discord.Message]:
        """Bestehendes Panel bearbeiten oder (in ``kanal``) neu posten."""
        eintraege = await lade_eintraege(guild.id)
        embed, view = baue_embed(guild, eintraege), baue_view(guild, eintraege)
        kanal_id = await GuildConfig.get(guild.id, "rollenpanel.kanal_id", "")
        msg_id = await GuildConfig.get(guild.id, "rollenpanel.nachricht_id", "")
        alt = guild.get_channel(int(kanal_id)) if str(kanal_id).isdigit() else None
        if kanal is None or (alt is not None and alt.id == getattr(kanal, "id", None)):
            if isinstance(alt, discord.TextChannel) and str(msg_id).isdigit():
                try:
                    msg = await alt.fetch_message(int(msg_id))
                    await msg.edit(embed=embed, view=view)
                    return msg
                except discord.NotFound:
                    logger.info("Rollen-Panel: alte Nachricht fehlt, poste neu")
            kanal = kanal or alt
        if kanal is None:
            return None
        msg = await kanal.send(embed=embed, view=view)
        await GuildConfig.set(guild.id, "rollenpanel.kanal_id", str(msg.channel.id), updated_by="rollenpanel")
        await GuildConfig.set(guild.id, "rollenpanel.nachricht_id", str(msg.id), updated_by="rollenpanel")
        return msg

    @grp.command(name="posten", description="Panel in diesem Kanal posten oder aktualisieren")
    @admin_only()
    async def posten(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await lade_eintraege(interaction.guild.id):
            await interaction.followup.send("Noch keine Rollen im Panel. Erst `/rollenpanel eintrag`.", ephemeral=True)
            return
        msg = await self.panel_aktualisieren(interaction.guild, interaction.channel)
        await interaction.followup.send(
            "Panel steht." if msg else "Panel konnte nicht gepostet werden.", ephemeral=True)

    @grp.command(name="eintrag", description="Rolle ins Panel aufnehmen oder ihren Text ändern")
    @app_commands.describe(rolle="Rolle", emoji="Emoji auf dem Button", text="Kurzerklärung im Embed")
    @admin_only()
    async def eintrag(self, interaction: discord.Interaction, rolle: discord.Role,
                      emoji: str, text: str) -> None:
        grund = rolle_vergebbar(rolle, interaction.guild.me)
        if grund:
            await interaction.response.send_message(grund, ephemeral=True)
            return
        eintraege = await lade_eintraege(interaction.guild.id)
        neu = {"rolle": str(rolle.id), "emoji": emoji.strip()[:32], "text": text.strip()[:TEXT_MAX]}
        for i, e in enumerate(eintraege):
            if e["rolle"] == neu["rolle"]:
                eintraege[i] = neu
                break
        else:
            if len(eintraege) >= MAX_EINTRAEGE:
                await interaction.response.send_message("Das Panel ist voll.", ephemeral=True)
                return
            eintraege.append(neu)
        await GuildConfig.set(interaction.guild.id, "rollenpanel.eintraege", eintraege,
                              updated_by=str(interaction.user.id))
        await interaction.response.defer(ephemeral=True)
        await self.panel_aktualisieren(interaction.guild)
        await interaction.followup.send(f"{rolle.name} steht im Panel.", ephemeral=True)

    @grp.command(name="entfernen", description="Rolle aus dem Panel nehmen")
    @admin_only()
    async def entfernen(self, interaction: discord.Interaction, rolle: discord.Role) -> None:
        eintraege = [e for e in await lade_eintraege(interaction.guild.id) if e["rolle"] != str(rolle.id)]
        await GuildConfig.set(interaction.guild.id, "rollenpanel.eintraege", eintraege,
                              updated_by=str(interaction.user.id))
        await interaction.response.defer(ephemeral=True)
        await self.panel_aktualisieren(interaction.guild)
        await interaction.followup.send(f"{rolle.name} ist raus.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RollenPanelCog(bot))

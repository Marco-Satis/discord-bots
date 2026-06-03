"""
Linked-Accounts Cog — verlinkte Spiel-Accounts (Activision/Steam/...).

Commands:
  /link <plattform> <id>     — eigenen Account verlinken/aktualisieren
  /unlink <plattform>        — Verlinkung entfernen
  /accounts [user]           — verlinkte Accounts anzeigen (eigene oder fremde)

Daten guild-scoped (Migration v7). Account-IDs werden in Embeds escaped
(Markdown-Injection-Schutz, CWE-79) + AllowedMentions.none.
"""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.linked_accounts import (
    LinkedAccountsManager, PLATFORMS, platform_display, MAX_ACCOUNT_ID_LEN,
)
from utils.logger import get_logger

logger = get_logger("cogs.linked_accounts")

# Plattform-Choices fuer die Slash-Commands (Whitelist, max 25 — wir haben < 25)
_PLATFORM_CHOICES = [
    app_commands.Choice(name=display, value=key)
    for key, display in PLATFORMS.items()
]


class LinkedAccountsCog(commands.Cog):
    """Verlinkte externe Spiel-Accounts pro User + Guild."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.accounts = LinkedAccountsManager()

    @app_commands.command(
        name="link",
        description="Verlinke einen Spiel-Account (Activision, Steam, ...)",
    )
    @app_commands.describe(
        plattform="Die Plattform",
        id="Deine Account-ID / dein Username auf der Plattform",
    )
    @app_commands.choices(plattform=_PLATFORM_CHOICES)
    @app_commands.checks.cooldown(3, 30.0)
    async def link_command(
        self,
        interaction: discord.Interaction,
        plattform: app_commands.Choice[str],
        id: str,
    ) -> None:
        """Eigenen Account verlinken/aktualisieren."""
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

        ok = await self.accounts.link(
            interaction.guild.id, interaction.user.id, plattform.value, id
        )
        if ok:
            safe_id = discord.utils.escape_markdown(id.strip()[:MAX_ACCOUNT_ID_LEN])
            await interaction.followup.send(
                f"**{platform_display(plattform.value)}** verlinkt: `{safe_id}`",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.followup.send(
                "Verlinkung fehlgeschlagen (leere ID?).", ephemeral=True
            )

    @app_commands.command(
        name="unlink",
        description="Entferne eine Account-Verlinkung",
    )
    @app_commands.describe(plattform="Die Plattform deren Verlinkung entfernt wird")
    @app_commands.choices(plattform=_PLATFORM_CHOICES)
    async def unlink_command(
        self,
        interaction: discord.Interaction,
        plattform: app_commands.Choice[str],
    ) -> None:
        """Eigene Verlinkung entfernen."""
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

        removed = await self.accounts.unlink(
            interaction.guild.id, interaction.user.id, plattform.value
        )
        if removed:
            await interaction.followup.send(
                f"**{platform_display(plattform.value)}**-Verlinkung entfernt.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"Du hattest keine **{platform_display(plattform.value)}**-Verlinkung.",
                ephemeral=True,
            )

    @app_commands.command(
        name="accounts",
        description="Zeigt verlinkte Spiel-Accounts (eigene oder die eines Users)",
    )
    @app_commands.describe(user="Wessen Accounts (leer = eigene)")
    @app_commands.checks.cooldown(1, 5.0)
    async def accounts_command(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
    ) -> None:
        """Verlinkte Accounts eines Users anzeigen."""
        await interaction.response.defer()
        if interaction.guild is None:
            await interaction.followup.send(
                "Nur auf einem Server nutzbar.", ephemeral=True
            )
            return

        target = user or interaction.user
        links = await self.accounts.get_links(interaction.guild.id, target.id)

        embed = discord.Embed(
            title=f"Verlinkte Accounts — {target.display_name}",
            color=0x5865F2,
        )
        if not links:
            embed.description = (
                "Keine verlinkten Accounts. Mit `/link` hinzufuegen."
            )
        else:
            for platform, account_id in links.items():
                safe_id = discord.utils.escape_markdown(str(account_id))
                embed.add_field(
                    name=platform_display(platform),
                    value=safe_id,
                    inline=True,
                )
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.followup.send(
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung."""
        if isinstance(error, app_commands.CommandOnCooldown):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"Bitte warte noch {error.retry_after:.0f}s.", ephemeral=True
                )
            return
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung für diesen Befehl.", ephemeral=True
                )
            return

        cmd_name = interaction.command.name if interaction.command else "unknown"
        logger.error(f"Command-Fehler in {cmd_name}: {error}", exc_info=True)
        try:
            msg = f"Ein Fehler ist aufgetreten: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            logger.debug(f"Exception swallowed: {e}")


async def setup(bot: commands.Bot) -> None:
    """Registriert den Linked-Accounts-Cog beim Bot."""
    await bot.add_cog(LinkedAccountsCog(bot))

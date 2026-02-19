"""
Permission system for Discord commands
Levels: Owner > Admin > Spieler > Alle
"""

import os
import functools
from discord import app_commands, Interaction

OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))
SATISFACTORY_ROLE_ID = int(os.getenv("SATISFACTORY_ROLE_ID", "0"))


def is_owner(interaction: Interaction) -> bool:
    """Check if user is the bot owner"""
    return interaction.user.id == OWNER_ID


def is_admin(interaction: Interaction) -> bool:
    """Check if user is owner or has admin role"""
    if is_owner(interaction):
        return True
    if interaction.guild and interaction.guild.get_member(interaction.user.id):
        member = interaction.guild.get_member(interaction.user.id)
        return any(r.id == ADMIN_ROLE_ID for r in member.roles)
    return False


def is_spieler(interaction: Interaction) -> bool:
    """Check if user is owner, admin, or has spieler role"""
    if is_admin(interaction):
        return True
    if interaction.guild and interaction.guild.get_member(interaction.user.id):
        member = interaction.guild.get_member(interaction.user.id)
        return any(r.id == SATISFACTORY_ROLE_ID for r in member.roles)
    return False


def owner_only():
    """Decorator: Only bot owner can use this command"""
    async def predicate(interaction: Interaction) -> bool:
        if not is_owner(interaction):
            await interaction.response.send_message(
                "Nur der Bot-Owner kann diesen Befehl nutzen.", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


def admin_only():
    """Decorator: Only admins can use this command"""
    async def predicate(interaction: Interaction) -> bool:
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Du brauchst die Admin-Rolle fuer diesen Befehl.", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


def spieler_only():
    """Decorator: Spieler role required"""
    async def predicate(interaction: Interaction) -> bool:
        if not is_spieler(interaction):
            await interaction.response.send_message(
                "Du brauchst die Satisfactory-Rolle fuer diesen Befehl.", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)

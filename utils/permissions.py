"""
Permission system for Discord commands
Levels: Owner > Admin > Spieler > Alle

Zusaetzlich: server_online_required() Decorator fuer Commands die einen laufenden Server benoetigen.
"""

import os
import functools
from discord import app_commands, Interaction

# M11-Fix: .env zuerst laden, BEVOR die drei int(os.getenv(...))-Reads laufen.
# Sonst waeren die IDs dauerhaft 0 (Owner-Lock bis Neustart), falls dieses Modul
# vor load_env() importiert wird. utils.config importiert utils.permissions nicht
# (kein Zirkel-Import), daher ist der direkte load_env()-Aufruf sicher.
from utils.config import load_env

load_env()

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
    if interaction.guild:
        member = interaction.guild.get_member(interaction.user.id)
        if member:
            return any(r.id == ADMIN_ROLE_ID for r in member.roles)
    return False


def is_spieler(interaction: Interaction) -> bool:
    """Check if user is owner, admin, or has spieler role"""
    if is_admin(interaction):
        return True
    if interaction.guild:
        member = interaction.guild.get_member(interaction.user.id)
        if member:
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


def server_online_required(server_attr: str):
    """Decorator: Prueft ob ein Server laeuft bevor der Command ausgefuehrt wird.

    Sucht das Server-Objekt zuerst auf dem Cog (self), dann auf self.bot.
    Das Server-Objekt muss eine async-Methode ``is_running()`` haben.

    Handhabt automatisch sowohl deferred als auch nicht-deferred Interactions:
    - Wenn bereits deferred: Fehlermeldung via followup
    - Wenn nicht deferred: Fehlermeldung via send_message (ephemeral)

    Fuer MC Multi-Server (dynamische Server-Auswahl) stattdessen
    ``MinecraftCog._require_online_server()`` verwenden.

    Beispiel::

        @server_online_required("server")
        async def my_command(self, interaction):
            # Server ist garantiert online
            ...

    Args:
        server_attr: Name des Server-Attributs (z.B. ``"server"``, ``"sat_server"``)
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, interaction: Interaction, *args, **kwargs):
            server = getattr(self, server_attr, None)
            if server is None:
                server = getattr(self.bot, server_attr, None)

            if server is not None and not await server.is_running():
                name = getattr(server, "display_name", "Server")
                if interaction.response.is_done():
                    await interaction.followup.send(f"{name} ist offline.")
                else:
                    await interaction.response.send_message(
                        f"{name} ist offline.", ephemeral=True
                    )
                return
            return await func(self, interaction, *args, **kwargs)
        return wrapper
    return decorator

"""
Pipeline-Approval-Cog (Phase E zweite Haelfte)

Empfaengt Discord-Component-Interactions von Approve/Dismiss-Buttons der
n8n-Pipeline-Notifications und ruft via subprocess das CLI-Helper-Script
`/home/marco/n8n_stack/scripts/pipeline_approve.py` auf.

Custom-ID-Format:
  - "pipeline_approve_<run_id>"
  - "pipeline_dismiss_<run_id>"

Permission:
  - ENV MARCO_DISCORD_UID muss gesetzt sein
  - Nur der User mit dieser UID darf approve/dismiss ausloesen
  - Andere User bekommen einen ephemeral-Hinweis

Sicherheits-Hinweise:
  - subprocess.run mit Argument-Liste (kein shell=True) — kein Injection-Risiko
  - run_id wird Format-validiert (alphanumerisch + Underscore, max 64 chars)
  - Ephemeral-Replies (nur fuer klickenden User sichtbar)
  - timeout=30s gegen haengende Scripts
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess

import discord
from discord.ext import commands

from utils.logger import get_logger

logger = get_logger(__name__)

# Pfad zum CLI-Helper auf dem Production-Server
PIPELINE_APPROVE_SCRIPT = "/home/marco/n8n_stack/scripts/pipeline_approve.py"

# Custom-ID-Pattern: pipeline_<action>_<run_id>
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_VALID_ACTIONS = {"approve", "dismiss"}


class PipelineApprovalCog(commands.Cog):
    """Cog fuer Pipeline-Approval-Button-Interactions."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.marco_uid = self._load_marco_uid()
        if not self.marco_uid:
            logger.warning(
                "MARCO_DISCORD_UID nicht gesetzt — Pipeline-Approval-Buttons "
                "werden fuer ALLE User abgewiesen (sicherer Default)."
            )

    @staticmethod
    def _load_marco_uid() -> int:
        """Liest MARCO_DISCORD_UID aus ENV, gibt 0 zurueck bei Fehler."""
        raw = os.getenv("MARCO_DISCORD_UID", "0")
        try:
            return int(raw)
        except ValueError:
            logger.error(f"MARCO_DISCORD_UID ist kein int: '{raw}'")
            return 0

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """
        Fangt Component-Interactions (Button-Clicks) auf Pipeline-Embeds ab.

        Nur reagiert auf custom_id mit Prefix "pipeline_approve_" oder
        "pipeline_dismiss_". Andere Interactions werden ignoriert (an andere
        Cogs durchgereicht).
        """
        # Nur Component-Interactions (Buttons), keine Slash-Commands
        if interaction.type != discord.InteractionType.component:
            return

        data = interaction.data or {}
        custom_id = data.get("custom_id", "") if isinstance(data, dict) else ""

        if not (custom_id.startswith("pipeline_approve_") or custom_id.startswith("pipeline_dismiss_")):
            return

        # Parse: pipeline_<action>_<run_id>
        parts = custom_id.split("_", 2)
        if len(parts) != 3 or parts[0] != "pipeline":
            await self._reply_ephemeral(interaction, "Ungueltiges Button-Format.")
            return

        _, action, run_id = parts
        if action not in _VALID_ACTIONS:
            await self._reply_ephemeral(interaction, f"Unbekannte Aktion: `{action}`.")
            return

        if not _RUN_ID_PATTERN.match(run_id):
            await self._reply_ephemeral(interaction, "Ungueltige Run-ID (nur alphanumerisch + Underscore/Bindestrich, max 64 Zeichen).")
            logger.warning(
                f"Pipeline-Button mit ungueltiger run_id: '{run_id[:80]}' "
                f"von user_id={interaction.user.id}"
            )
            return

        # Permission-Check: nur Marco darf approven/dismissen
        if not self.marco_uid:
            await self._reply_ephemeral(
                interaction,
                "Permission-Check nicht konfiguriert (MARCO_DISCORD_UID fehlt in .env).",
            )
            logger.error("MARCO_DISCORD_UID nicht gesetzt — Pipeline-Approval verweigert")
            return

        if interaction.user.id != self.marco_uid:
            await self._reply_ephemeral(
                interaction,
                "Nur Marco kann Pipeline-Findings approven/dismissen.",
            )
            logger.info(
                f"Pipeline-Approval verweigert fuer user_id={interaction.user.id} "
                f"(nur {self.marco_uid} erlaubt). action={action}, run_id={run_id}"
            )
            return

        # Defer-Response: Subprocess kann mehrere Sekunden brauchen,
        # Discord-Interactions haben ein 3s-Timeout fuer das initiale Response
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"Defer fehlgeschlagen: {e}")
            return

        # Subprocess in Thread-Pool ausfuehren (blocking call wrappen)
        try:
            result = await asyncio.to_thread(
                self._run_approve_script, run_id, action, interaction.user.id
            )
        except subprocess.TimeoutExpired:
            await self._followup_ephemeral(
                interaction,
                f"Pipeline-Approve fuer `{run_id}` timeout (>30s). Bitte manuell pruefen.",
            )
            logger.error(f"pipeline_approve.py timeout fuer run_id={run_id}, action={action}")
            return
        except FileNotFoundError:
            await self._followup_ephemeral(
                interaction,
                f"CLI-Helper nicht gefunden: `{PIPELINE_APPROVE_SCRIPT}`",
            )
            logger.error(f"PIPELINE_APPROVE_SCRIPT nicht gefunden: {PIPELINE_APPROVE_SCRIPT}")
            return
        except Exception as e:
            await self._followup_ephemeral(
                interaction,
                f"Unerwarteter Fehler beim Aufruf: `{type(e).__name__}: {str(e)[:200]}`",
            )
            logger.error(f"pipeline_approve.py Exception: {e}", exc_info=True)
            return

        if result.returncode == 0:
            emoji = "✅" if action == "approve" else "🗑"
            await self._followup_ephemeral(
                interaction,
                f"{emoji} Run `{run_id}` als **{action}d** markiert.",
            )
            logger.info(f"Pipeline-{action}d run_id={run_id} by admin_id={interaction.user.id}")
        else:
            stderr = (result.stderr or "")[:500]
            await self._followup_ephemeral(
                interaction,
                f"Pipeline-Approve fehlgeschlagen (rc={result.returncode}):\n```{stderr}```",
            )
            logger.warning(
                f"pipeline_approve.py rc={result.returncode} fuer run_id={run_id}, "
                f"action={action}, stderr={stderr[:200]}"
            )

    @staticmethod
    def _run_approve_script(run_id: str, action: str, admin_id: int) -> subprocess.CompletedProcess:
        """Blocking subprocess.run — wird per asyncio.to_thread aufgerufen."""
        return subprocess.run(
            [
                "python3", PIPELINE_APPROVE_SCRIPT,
                run_id, action,
                "--admin-id", str(admin_id),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    @staticmethod
    async def _reply_ephemeral(interaction: discord.Interaction, text: str) -> None:
        """Sicher Ephemeral-Reply senden, mit Fallback wenn schon responded."""
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException as e:
            logger.debug(f"Ephemeral-Reply konnte nicht gesendet werden: {e}")

    @staticmethod
    async def _followup_ephemeral(interaction: discord.Interaction, text: str) -> None:
        """Followup nach defer() senden."""
        try:
            await interaction.followup.send(text, ephemeral=True)
        except discord.HTTPException as e:
            logger.debug(f"Followup-Reply konnte nicht gesendet werden: {e}")


async def setup(bot: commands.Bot) -> None:
    """Cog-Setup-Funktion (von discord.py via load_extension aufgerufen)."""
    await bot.add_cog(PipelineApprovalCog(bot))
    logger.info("PipelineApprovalCog geladen — MARCO_DISCORD_UID=%s",
                "gesetzt" if os.getenv("MARCO_DISCORD_UID") else "FEHLT")

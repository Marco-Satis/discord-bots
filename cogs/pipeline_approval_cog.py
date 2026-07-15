"""
Pipeline-Approval-Cog (Phase E + Feature H Modell A)

Empfaengt Discord-Component-Interactions der n8n-Pipeline-Notifications und ruft
via subprocess den CLI-Helper `/home/marco/n8n_stack/scripts/pipeline_approve.py`.

Buttons (von pipeline_runner.step_discord_notify gesetzt) — Rework 2026-07-15:
  - "pipeline_delete_<run_id>"    🗑 -> delete   (Archiv + TikTok-Unsave-Queue, KEIN
        Few-Shot; Bot loescht zusaetzlich die Nachricht = aus Findings-View entfernt)
  - "pipeline_recat_<run_id>"     📁 -> oeffnet Kategorie-Select-Menu (ephemeral)
        -> Select-Callback ruft `recategorize --category <id>` (Korrektur + Few-Shot)

Legacy (Alt-Embeds vor dem Rework, weiter unterstuetzt):
  - "pipeline_approve_<run_id>"   ✅ -> approve  (Finding behalten, Sammlung = Kategorie)
  - "pipeline_dismiss_<run_id>"   🗑 -> dismiss  (Archiv-Sammlung, Few-Shot-negativ)

Permission:
  - ENV MARCO_DISCORD_UID muss gesetzt sein; nur dieser User darf entscheiden.

Sicherheit:
  - subprocess.run mit Argument-Liste (kein shell=True)
  - run_id Format-validiert (alphanumerisch + Underscore/Bindestrich, max 64)
  - --category zusaetzlich serverseitig in pipeline_approve.py gegen categories.yaml gewhitelistet
  - Ephemeral-Replies, timeout=30s
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from utils.logger import get_logger

logger = get_logger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")

PIPELINE_APPROVE_SCRIPT = os.getenv(
    "PIPELINE_APPROVE_SCRIPT", "/home/marco/n8n_stack/scripts/pipeline_approve.py"
)
PYTHON_BIN = os.getenv("PIPELINE_PYTHON_BIN", "/usr/bin/python3")

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# Feature H: Kategorie-Auswahl fuer das Recategorize-Select (id, Label).
# Serverseitig zusaetzlich gegen categories.yaml validiert (Drift-sicher).
CATEGORY_CHOICES: list[tuple[str, str]] = [
    ("claude-code-tips", "Claude Code Tips"),
    ("tech-tools", "Tech Tools"),
    ("smart-home", "Smart Home"),
    ("technik", "Technik"),
    ("cod-loadouts", "COD Loadouts"),
    ("cod-settings", "COD Settings"),
    ("cod-techniques", "COD Techniques"),
    ("cod-zombies", "COD Zombies"),
    ("cod-warzone", "COD Warzone"),
    ("essen", "Essen & Kochen"),
    ("fitness", "Fitness & Gesundheit"),
    ("anime", "Anime"),
    ("finanzen", "Finanzen"),
    ("haushalt", "Haushalt & Life-Hacks"),
    ("other", "Other"),
]


class _RecatSelect(discord.ui.Select):
    """Kategorie-Auswahl fuer 'Falsche Kategorie'. Ruft im Callback recategorize."""

    def __init__(self, cog: "PipelineApprovalCog", run_id: str, orig_msg: discord.Message | None):
        self._cog = cog
        self._run_id = run_id
        self._orig_msg = orig_msg
        options = [discord.SelectOption(label=lbl, value=cid) for cid, lbl in CATEGORY_CHOICES]
        super().__init__(
            placeholder="Neue Kategorie waehlen...",
            min_values=1, max_values=1, options=options,
            custom_id=f"pipeline_recatsel_{run_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # noqa: D401
        await self._cog._do_recategorize(interaction, self._run_id, self.values[0], self._orig_msg)


class _RecatView(discord.ui.View):
    """Kurzlebige ephemeral-View mit dem Kategorie-Select (Timeout 120s)."""

    def __init__(self, cog: "PipelineApprovalCog", run_id: str, orig_msg: discord.Message | None):
        super().__init__(timeout=120)
        self.add_item(_RecatSelect(cog, run_id, orig_msg))


class PipelineApprovalCog(commands.Cog):
    """Cog fuer Pipeline-Approval-Button-Interactions (Feature H Modell A)."""

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
        raw = os.getenv("MARCO_DISCORD_UID", "0")
        try:
            return int(raw)
        except ValueError:
            logger.error(f"MARCO_DISCORD_UID ist kein int: '{raw}'")
            return 0

    # ------------------------------------------------------------------ #
    # Gemeinsame Guards
    # ------------------------------------------------------------------ #
    def _permission_ok(self, interaction: discord.Interaction) -> bool:
        """True wenn der klickende User Marco ist (und UID konfiguriert)."""
        return bool(self.marco_uid) and interaction.user.id == self.marco_uid

    async def _deny(self, interaction: discord.Interaction) -> None:
        if not self.marco_uid:
            await self._reply_ephemeral(
                interaction, "Permission-Check nicht konfiguriert (MARCO_DISCORD_UID fehlt in .env)."
            )
        else:
            await self._reply_ephemeral(interaction, "Nur Marco kann Pipeline-Findings entscheiden.")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Faengt Pipeline-Button-Clicks ab (approve/dismiss/recat). Select laeuft ueber die View."""
        if interaction.type != discord.InteractionType.component:
            return
        data = interaction.data or {}
        custom_id = data.get("custom_id", "") if isinstance(data, dict) else ""

        # Select-Submits (pipeline_recatsel_) werden vom View-Callback behandelt — hier ignorieren.
        if custom_id.startswith("pipeline_recatsel_"):
            return

        # 📁 "Falsche Kategorie" — Select-Menu oeffnen.
        if custom_id.startswith("pipeline_recat_"):
            run_id = custom_id[len("pipeline_recat_"):]
            if not _RUN_ID_PATTERN.match(run_id):
                await self._reply_ephemeral(interaction, "Ungueltige Run-ID.")
                return
            if not self._permission_ok(interaction):
                await self._deny(interaction)
                return
            try:
                await interaction.response.send_message(
                    "📁 Neue Kategorie fuer dieses Finding waehlen:",
                    view=_RecatView(self, run_id, interaction.message),
                    ephemeral=True,
                )
            except discord.HTTPException as e:
                logger.error(f"Recat-Select konnte nicht gesendet werden: {e}")
            return

        # 🗑 delete (aktiv) · ✅ approve / 🗑 dismiss (Legacy: Alt-Embeds vor 2026-07-15)
        if (custom_id.startswith("pipeline_delete_")
                or custom_id.startswith("pipeline_approve_")
                or custom_id.startswith("pipeline_dismiss_")):
            parts = custom_id.split("_", 2)
            if len(parts) != 3 or parts[0] != "pipeline":
                await self._reply_ephemeral(interaction, "Ungueltiges Button-Format.")
                return
            _, action, run_id = parts
            if action not in ("approve", "dismiss", "delete"):
                await self._reply_ephemeral(interaction, f"Unbekannte Aktion: `{action}`.")
                return
            if not _RUN_ID_PATTERN.match(run_id):
                await self._reply_ephemeral(interaction, "Ungueltige Run-ID.")
                logger.warning(f"Pipeline-Button mit ungueltiger run_id von user_id={interaction.user.id}")
                return
            if not self._permission_ok(interaction):
                await self._deny(interaction)
                logger.info(f"Pipeline-Approval verweigert fuer user_id={interaction.user.id}")
                return
            await self._handle_decision(interaction, action, run_id, category=None,
                                        orig_msg=interaction.message)
            return
        # Sonst: nicht unseres — durchreichen.

    # ------------------------------------------------------------------ #
    # Entscheidungs-Ausfuehrung (approve/dismiss/recategorize)
    # ------------------------------------------------------------------ #
    async def _do_recategorize(self, interaction: discord.Interaction, run_id: str,
                               category: str, orig_msg: discord.Message | None) -> None:
        """Select-Callback: Kategorie-Korrektur durchfuehren."""
        if not self._permission_ok(interaction):
            await self._deny(interaction)
            return
        await self._handle_decision(interaction, "recategorize", run_id, category=category,
                                    orig_msg=orig_msg)

    async def _handle_decision(self, interaction: discord.Interaction, action: str, run_id: str,
                               category: str | None, orig_msg: discord.Message | None) -> None:
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"Defer fehlgeschlagen: {e}")
            return

        try:
            result = await asyncio.to_thread(
                self._run_approve_script, run_id, action, interaction.user.id, category
            )
        except subprocess.TimeoutExpired:
            await self._followup_ephemeral(interaction, f"Pipeline-Aktion `{run_id}` timeout (>30s).")
            logger.error(f"pipeline_approve.py timeout run_id={run_id} action={action}")
            return
        except FileNotFoundError:
            await self._followup_ephemeral(interaction, f"CLI-Helper nicht gefunden: `{PIPELINE_APPROVE_SCRIPT}`")
            logger.error(f"PIPELINE_APPROVE_SCRIPT nicht gefunden: {PIPELINE_APPROVE_SCRIPT}")
            return
        except Exception as e:
            await self._followup_ephemeral(interaction, f"Unerwarteter Fehler: `{type(e).__name__}: {str(e)[:200]}`")
            logger.error(f"pipeline_approve.py Exception: {e}", exc_info=True)
            return

        if result.returncode == 0:
            extra = ""
            if action == "recategorize" and category:
                extra = f" → `{category}`"
            if action == "delete":
                # "aus den Findings loeschen": Original-Nachricht komplett entfernen
                # (Archiv + Unsave laufen serverseitig ueber approval='dismissed').
                # Fallback auf Embed-Finalize, falls Loeschen scheitert (z.B. 403/älter 14 Tage).
                deleted = await self._delete_message(orig_msg, run_id)
                if not deleted:
                    await self._finalize_embed(orig_msg, action, run_id)
                await self._followup_ephemeral(
                    interaction,
                    f"🗑 Run `{run_id}` **gelöscht** (archiviert + für TikTok-Unsave vorgemerkt).")
            else:
                await self._finalize_embed(orig_msg, action, run_id, extra=extra)
                emoji = {"approve": "✅", "dismiss": "🗑", "recategorize": "📁"}.get(action, "•")
                label = {"approve": "approved", "dismiss": "dismissed",
                         "recategorize": f"recategorized{extra}"}.get(action, action)
                await self._followup_ephemeral(interaction, f"{emoji} Run `{run_id}` als **{label}** markiert.")
            logger.info(f"Pipeline-{action} run_id={run_id} cat={category} by admin_id={interaction.user.id}")
        else:
            stderr = (result.stderr or "")[:500]
            await self._followup_ephemeral(interaction,
                                           f"Pipeline-Aktion fehlgeschlagen (rc={result.returncode}):\n```{stderr}```")
            logger.warning(f"pipeline_approve.py rc={result.returncode} run_id={run_id} action={action} stderr={stderr[:200]}")

    @staticmethod
    def _run_approve_script(run_id: str, action: str, admin_id: int,
                            category: str | None = None) -> subprocess.CompletedProcess:
        """Blocking subprocess.run — per asyncio.to_thread aufgerufen."""
        cmd = [PYTHON_BIN, PIPELINE_APPROVE_SCRIPT, run_id, action, "--admin-id", str(admin_id)]
        if action == "recategorize" and category:
            cmd += ["--category", category]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    @staticmethod
    async def _delete_message(msg: discord.Message | None, run_id: str) -> bool:
        """Loescht die Finding-Nachricht (delete-Action). True bei Erfolg.

        429-Retry einmalig (Retry-After gedeckelt 30s). Bei anderem HTTPException
        (z.B. 403/404/älter-14-Tage) -> False, Aufrufer faellt auf Embed-Finalize zurueck.
        """
        if msg is None:
            return False
        for attempt in range(2):
            try:
                await msg.delete()
                return True
            except discord.HTTPException as e:
                if getattr(e, "status", None) == 429 and attempt == 0:
                    retry_after = 5.0
                    try:
                        retry_after = float(e.response.headers.get("Retry-After", "5"))
                    except (TypeError, ValueError, AttributeError):
                        pass
                    await asyncio.sleep(min(retry_after, 30.0))
                    continue
                logger.warning(f"Konnte Finding-Nachricht nicht loeschen fuer run_id={run_id} "
                               f"(msg_id={getattr(msg,'id','?')}): {e}")
                return False
        return False

    @staticmethod
    async def _finalize_embed(msg: discord.Message | None, action: str, run_id: str,
                              extra: str = "") -> None:
        """Original-Embed finalisieren: Buttons entfernen + Status-Field setzen."""
        if msg is None:
            return
        meta = {
            "approve": ("✅", "Approved", 0x57F287),
            "dismiss": ("🗑", "Dismissed", 0x95A5A6),
            "delete": ("🗑", "Gelöscht", 0x95A5A6),
            "recategorize": ("📁", "Recategorized", 0x5865F2),
        }.get(action, ("•", action.title(), 0x95A5A6))
        emoji, state_label, new_color = meta
        now_str = datetime.now(BERLIN).strftime("%Y-%m-%d %H:%M Berlin")
        field_value = f"{emoji} **{state_label}{extra}** · {now_str}"

        try:
            target_embed = None
            if msg.embeds:
                src = msg.embeds[0]
                new_embed = discord.Embed(
                    title=src.title, description=src.description, color=new_color,
                    url=src.url if src.url else None, timestamp=src.timestamp,
                )
                for f in src.fields:
                    if (f.name or "").lower().startswith("status"):
                        continue
                    new_embed.add_field(name=f.name or "​", value=f.value or "​", inline=f.inline)
                new_embed.add_field(name="Status", value=field_value, inline=False)
                if src.footer and src.footer.text:
                    new_embed.set_footer(text=src.footer.text, icon_url=src.footer.icon_url)
                if src.thumbnail and src.thumbnail.url:
                    new_embed.set_thumbnail(url=src.thumbnail.url)
                if src.image and src.image.url:
                    new_embed.set_image(url=src.image.url)
                if src.author and src.author.name:
                    new_embed.set_author(name=src.author.name, url=src.author.url, icon_url=src.author.icon_url)
                target_embed = new_embed

            # RR-06: 429-Retry beim Button-Entfernen (einmalig, Retry-After gedeckelt 30s).
            for attempt in range(2):
                try:
                    if target_embed is not None:
                        await msg.edit(embed=target_embed, view=None)
                    else:
                        await msg.edit(view=None)
                    break
                except discord.HTTPException as e:
                    if getattr(e, "status", None) == 429 and attempt == 0:
                        retry_after = 5.0
                        try:
                            retry_after = float(e.response.headers.get("Retry-After", "5"))
                        except (TypeError, ValueError, AttributeError):
                            pass
                        await asyncio.sleep(min(retry_after, 30.0))
                        continue
                    raise
        except discord.HTTPException as e:
            logger.warning(f"Konnte Embed/View nicht aktualisieren fuer run_id={run_id} (msg_id={getattr(msg,'id','?')}): {e}")

    @staticmethod
    async def _reply_ephemeral(interaction: discord.Interaction, text: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException as e:
            logger.debug(f"Ephemeral-Reply konnte nicht gesendet werden: {e}")

    @staticmethod
    async def _followup_ephemeral(interaction: discord.Interaction, text: str) -> None:
        try:
            await interaction.followup.send(text, ephemeral=True)
        except discord.HTTPException as e:
            logger.debug(f"Followup-Reply konnte nicht gesendet werden: {e}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PipelineApprovalCog(bot))
    logger.info("PipelineApprovalCog geladen (Feature H Modell A) — MARCO_DISCORD_UID=%s",
                "gesetzt" if os.getenv("MARCO_DISCORD_UID") else "FEHLT")

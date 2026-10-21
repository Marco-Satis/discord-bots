"""
Moderations-Akte — Slash-Commands fuer den Marshal-Bot.

Command-Struktur (app_commands.Group, alle Admin-only, alle Antworten ephemeral):
  /akte zeigen <mitglied>                      — Akte: Verwarnungen, Taten, Notizen
  /akte notiz <mitglied> <text> [beweis]       — Notiz anlegen, optional mit Bild
  /akte notiz-loeschen <id>                    — Notiz (und Beweisbild) loeschen
  /akte grund <id> <neuer_grund>               — Grund einer Verwarnung aendern

Datenlogik: modules/mod_akte.py. Beweisbilder werden heruntergeladen und lokal
abgelegt (Discord-Anhang-Links laufen ab).
"""
from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules import mod_akte
from modules.member_cache import upsert_member
from utils.embeds import COLOR_BRAND
from utils.logger import get_logger
from utils.permissions import admin_only

logger = get_logger("cogs.mod_akte")

_NO_MENTIONS = discord.AllowedMentions.none()
_MAX_TIMELINE_LINES = 12
_DESC_LIMIT = 3900


def _esc(text: Any) -> str:
    """Markdown entschaerfen (Nutzer- und Moderatoren-Texte)."""
    return discord.utils.escape_markdown(str(text or ""))


def _short(text: str, limit: int = 140) -> str:
    """Text fuer eine Zeitleisten-Zeile kuerzen."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _timeline_line(entry: dict[str, Any]) -> str:
    """Eine Zeile der Zeitleiste im Embed."""
    when = entry.get("ts_display", "")
    kind = entry.get("kind")
    if kind == "warn":
        state = "aktiv" if entry.get("active") else "abgelaufen"
        return (
            f"`{when}` **Verwarnung #{entry['id']}** · {entry['points']} P · {state}\n"
            f"↳ {_esc(_short(entry.get('reason', '')))} — {_esc(entry.get('moderator_name', ''))}"
        )
    if kind == "action":
        reason = entry.get("reason") or "kein Grund angegeben"
        return (
            f"`{when}` **{_esc(entry.get('label', 'Aktion'))}**\n"
            f"↳ {_esc(_short(reason))} — {_esc(entry.get('moderator_display', 'unbekannt'))}"
        )
    bild = " · 📎 Beweisbild" if entry.get("evidence_file") else ""
    return (
        f"`{when}` **Notiz #{entry['id']}**{bild}\n"
        f"↳ {_esc(_short(entry.get('text', '')))} — {_esc(entry.get('author_name', ''))}"
    )


class ModAkteCog(commands.Cog):
    """Moderations-Akte: Verwarnungen, Moderations-Taten und Notizen je Mitglied"""

    akte_grp = app_commands.Group(
        name="akte",
        description="Moderations-Akte eines Mitglieds",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Hilfen
    # ------------------------------------------------------------------

    def _warn_manager(self) -> Any:
        """WarnManager des geladenen WarnCog (fuer Cache-Konsistenz) oder None."""
        cog = self.bot.get_cog("WarnCog")
        return getattr(cog, "warn_mgr", None) if cog else None

    @staticmethod
    async def _remember(guild: discord.Guild, member: discord.abc.User) -> None:
        """Namen + Avatar in member_cache pflegen, damit das Dashboard Namen zeigt."""
        avatar = member.display_avatar.url if getattr(member, "display_avatar", None) else None
        await upsert_member(guild.id, member.id, getattr(member, "display_name", str(member)), avatar)

    # ------------------------------------------------------------------
    # /akte zeigen <mitglied>
    # ------------------------------------------------------------------

    @akte_grp.command(name="zeigen", description="Akte eines Mitglieds anzeigen")
    @app_commands.describe(mitglied="Mitglied, dessen Akte angezeigt wird")
    @admin_only()
    async def akte_zeigen(
        self, interaction: discord.Interaction, mitglied: discord.Member
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._remember(interaction.guild, mitglied)
        data = await mod_akte.akte(interaction.guild.id, mitglied.id)
        c = data["counts"]

        lines: list[str] = []
        used = 0
        shown = 0
        for entry in data["timeline"][:_MAX_TIMELINE_LINES]:
            line = _timeline_line(entry)
            if used + len(line) + 2 > _DESC_LIMIT:
                break
            lines.append(line)
            used += len(line) + 2
            shown += 1
        rest = len(data["timeline"]) - shown
        if not lines:
            lines.append("Keine Einträge. Die Akte ist leer.")
        elif rest > 0:
            lines.append(f"-# … und {rest} ältere Einträge (vollständig im Dashboard)")

        embed = discord.Embed(
            title=f"Akte: {_esc(mitglied.display_name)}",
            description="\n\n".join(lines),
            color=COLOR_BRAND,
        )
        embed.add_field(
            name="Verwarnungen",
            value=f"{c['warns_active']} aktiv · {c['points_active']} P\n{c['warns']} gesamt",
        )
        embed.add_field(name="Moderation", value=f"{c['actions']} Einträge")
        embed.add_field(name="Notizen", value=f"{c['notes']}")
        embed.set_thumbnail(url=mitglied.display_avatar.url)
        embed.set_footer(text="IDs (#…) für /akte grund und /akte notiz-loeschen")
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=_NO_MENTIONS)

    # ------------------------------------------------------------------
    # /akte notiz <mitglied> <text> [beweis]
    # ------------------------------------------------------------------

    @akte_grp.command(name="notiz", description="Notiz zur Akte hinzufügen (optional mit Beweisbild)")
    @app_commands.describe(
        mitglied="Mitglied, zu dem die Notiz gehört",
        text="Inhalt der Notiz",
        beweis="Optionales Bild (PNG, JPG, GIF, WEBP, höchstens 8 MB)",
    )
    @admin_only()
    async def akte_notiz(
        self,
        interaction: discord.Interaction,
        mitglied: discord.Member,
        text: app_commands.Range[str, 1, 1000],
        beweis: Optional[discord.Attachment] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        evidence: Optional[str] = None
        if beweis is not None:
            try:
                evidence = await mod_akte.download_evidence(beweis.url, beweis.filename, beweis.size)
            except mod_akte.EvidenceError as e:
                await interaction.followup.send(
                    f"Beweisbild abgelehnt: {e} Die Notiz wurde nicht gespeichert.",
                    ephemeral=True, allowed_mentions=_NO_MENTIONS,
                )
                return

        try:
            note_id = await mod_akte.add_note(
                interaction.guild.id, mitglied.id, interaction.user.id, text, evidence
            )
        except ValueError as e:
            if evidence:
                mod_akte.delete_evidence_file(evidence)
            await interaction.followup.send(
                f"Notiz nicht gespeichert: {_esc(e)}", ephemeral=True, allowed_mentions=_NO_MENTIONS
            )
            return

        await self._remember(interaction.guild, mitglied)
        await self._remember(interaction.guild, interaction.user)
        logger.info(f"Akte-Notiz #{note_id} angelegt von {interaction.user.id} (Beweisbild: {bool(evidence)})")
        extra = " mit Beweisbild" if evidence else ""
        await interaction.followup.send(
            f"Notiz **#{note_id}**{extra} zur Akte von **{_esc(mitglied.display_name)}** gespeichert.",
            ephemeral=True, allowed_mentions=_NO_MENTIONS,
        )

    # ------------------------------------------------------------------
    # /akte notiz-loeschen <id>
    # ------------------------------------------------------------------

    @akte_grp.command(name="notiz-loeschen", description="Notiz aus einer Akte löschen")
    @app_commands.describe(id="Nummer der Notiz (aus /akte zeigen)")
    @admin_only()
    async def akte_notiz_loeschen(
        self, interaction: discord.Interaction, id: app_commands.Range[int, 1]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        note = await mod_akte.delete_note(id, guild_id=interaction.guild.id)
        if note is None:
            await interaction.followup.send(
                f"Notiz #{id} gibt es nicht.", ephemeral=True, allowed_mentions=_NO_MENTIONS
            )
            return
        logger.info(f"Akte-Notiz #{id} geloescht von {interaction.user.id}")
        await interaction.followup.send(
            f"Notiz **#{id}** gelöscht.", ephemeral=True, allowed_mentions=_NO_MENTIONS
        )

    # ------------------------------------------------------------------
    # /akte grund <id> <neuer_grund>
    # ------------------------------------------------------------------

    @akte_grp.command(name="grund", description="Grund einer Verwarnung ändern")
    @app_commands.describe(
        id="Nummer der Verwarnung (aus /akte zeigen)",
        neuer_grund="Neuer Grund",
    )
    @admin_only()
    async def akte_grund(
        self,
        interaction: discord.Interaction,
        id: app_commands.Range[int, 1],
        neuer_grund: app_commands.Range[str, 1, 500],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            res = await mod_akte.update_warn_reason(id, neuer_grund, self._warn_manager())
        except ValueError as e:
            await interaction.followup.send(
                f"Nicht geändert: {_esc(e)}", ephemeral=True, allowed_mentions=_NO_MENTIONS
            )
            return
        if res is None:
            await interaction.followup.send(
                f"Verwarnung #{id} gibt es nicht.", ephemeral=True, allowed_mentions=_NO_MENTIONS
            )
            return
        logger.info(f"Warn #{id}: Grund geaendert von {interaction.user.id}")
        embed = discord.Embed(title=f"Verwarnung #{id}: Grund geändert", color=COLOR_BRAND)
        embed.add_field(name="Vorher", value=_esc(_short(res["old_reason"], 1000)) or "–", inline=False)
        embed.add_field(name="Jetzt", value=_esc(_short(res["new_reason"], 1000)), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=_NO_MENTIONS)

    # ------------------------------------------------------------------
    # Fehlerbehandlung
    # ------------------------------------------------------------------

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung fuer alle /akte-Commands"""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung für diesen Befehl.", ephemeral=True
                )
            return

        cmd_name = interaction.command.name if interaction.command else "unknown"
        logger.error(f"Command error in akte {cmd_name}: {error}", exc_info=True)
        try:
            msg = "Ein Fehler ist aufgetreten. Details stehen im Bot-Log."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException as e:
            logger.debug(f"Fehlermeldung nicht zustellbar: {e}")


async def setup(bot: commands.Bot) -> None:
    """Cog laden"""
    await bot.add_cog(ModAkteCog(bot))

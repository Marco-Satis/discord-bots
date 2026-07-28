"""
Pipeline-Control-Cog — Discord-Control-Panel fuer die TikTok-Curation-Pipeline.

Stand: 2026-07-14 (Umbau: Run-Achse von Modus-Achse entkoppelt)

Embed mit 4 persistenten Buttons (ueberleben Bot-Restart via bot.add_view):
  ▶ Start  (custom_id pipeline_ctl_start)  -> Pipeline AN (Pause + Datei-Flag loesen)
  ⏹ Stop   (custom_id pipeline_ctl_stop)   -> Pipeline AUS (pause_active=1)
  🔀 Modus (custom_id pipeline_ctl_mode)   -> Burst <-> Normal toggeln (sweep_active)
  🔄 Status (custom_id pipeline_ctl_status) -> aktuellen Stand anzeigen (ephemeral)

Run-Achse (Start/Stop) und Modus-Achse (Burst/Normal) sind unabhaengig:
Start/Stop schalten die Pipeline an/aus, der Modus-Toggle wechselt nur die Drosselung.

Jeder Klick ruft via subprocess den CLI-Helper
  /home/marco/n8n_stack/scripts/pipeline_control.py <action> --admin-id <uid>

Panel posten:  !pipeline_panel   (im gewuenschten Channel, oder postet in
PIPELINE_CONTROL_CHANNEL_ID).

Permission:
  - ENV MARCO_DISCORD_UID muss gesetzt sein; nur dieser User darf Buttons nutzen.

Sicherheit:
  - subprocess.run mit Argument-Liste (kein shell=True), timeout 40s.
  - action gegen feste Whitelist; nur Marco-UID erlaubt; ephemeral Replies.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

from utils.logger import get_logger

logger = get_logger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")

PANEL_TITLE = "🛠 Pipeline — Control"

PIPELINE_CONTROL_SCRIPT = os.getenv(
    "PIPELINE_CONTROL_SCRIPT", "/home/marco/n8n_stack/scripts/pipeline_control.py"
)
PYTHON_BIN = os.getenv("PIPELINE_PYTHON_BIN", "/usr/bin/python3")
CONTROL_CHANNEL_ID = int(os.getenv("PIPELINE_CONTROL_CHANNEL_ID", "222222222222222222") or "0")

_VALID_ACTIONS = ("start", "stop", "mode", "sweepmode", "daylimit", "status")

# Auto-Refresh (2026-07-27): das Panel aktualisiert sich selbst statt nur auf Knopfdruck.
# Discord kann nicht pushen — also pollt der Cog. 15s fuehlt sich live an und ist von
# Discords Edit-Rate-Limit weit entfernt; entscheidend ist, dass wir NUR editieren wenn
# sich der gerenderte Inhalt geaendert hat (siehe _panel_signature). Im Leerlauf geht
# also gar kein API-Call raus, nur ein billiger lokaler Subprocess.
REFRESH_SECONDS = int(os.getenv("PIPELINE_PANEL_REFRESH_SECONDS", "15") or "15")


def _marco_uid() -> int:
    try:
        return int(os.getenv("MARCO_DISCORD_UID", "0") or "0")
    except ValueError:
        logger.error("MARCO_DISCORD_UID ist kein int")
        return 0


def _run_control(action: str, admin_id: int, *extra: str) -> subprocess.CompletedProcess:
    """Blocking subprocess.run — per asyncio.to_thread aufgerufen."""
    cmd = [PYTHON_BIN, PIPELINE_CONTROL_SCRIPT, action, "--admin-id", str(admin_id), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=40)


async def _live_status() -> dict | None:
    """`pipeline_control.py status --json` -> dict. None bei jedem Fehler.

    Bewusst still: der Refresh-Loop laeuft alle paar Sekunden, eine kaputte Abfrage
    darf weder das Panel zerstoeren noch das Log fluten.
    """
    try:
        res = await asyncio.to_thread(_run_control, "status", 0, "--json")
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    try:
        return json.loads((res.stdout or "").strip())
    except (json.JSONDecodeError, ValueError):
        return None


def _fmt_live_status(st: dict) -> str:
    """Live-Felder -> Panel-Text. Reine Funktion, damit Signatur-Vergleich billig ist."""
    run_state = f"⏸ pausiert ({st.get('pause_reason') or '?'})" if st.get("paused") else "▶️ aktiv"
    daemon = "läuft" if st.get("daemon") else "AUS"
    lines = [
        f"{run_state} · Daemon {daemon}",
        f"Modus **{st.get('modus', '?')}** · Sweep **{st.get('sweep_mode', '?')}** "
        f"· Tageslimit **{st.get('tageslimit', '?')}**",
        f"Heute **{st.get('today', 0)}/{st.get('day_cap', 0)}** "
        f"· 5h-Fenster **{st.get('window', 0)}/{st.get('window_cap', 0)}**",
    ]
    pending = st.get("inbox_pending")
    if pending is not None:
        lines.append(f"Warteschlange **{pending}**"
                     + (f" · offene Keeper {st['keepers_left']}" if st.get("keepers_left") else ""))
    last = st.get("last_finding") or None
    if last:
        title = (last.get("title") or "")[:70]
        lines.append(f"Zuletzt: _{title}_ ({last.get('category', '?')}, {last.get('started_at', '?')})")
    return "\n".join(lines)


def _panel_signature(st: dict) -> str:
    """Alles ausser der Uhrzeit — sonst wuerde jeder Tick ein Edit ausloesen."""
    return _fmt_live_status(st)


async def _handle_action(interaction: discord.Interaction, action: str) -> None:
    """Gemeinsamer Button-Handler: Permission -> defer -> subprocess -> reply/panel-update."""
    uid = _marco_uid()
    if not uid or interaction.user.id != uid:
        msg = ("Permission-Check nicht konfiguriert (MARCO_DISCORD_UID fehlt)."
               if not uid else "Nur Marco kann die Pipeline steuern.")
        try:
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass
        logger.info("Pipeline-Control verweigert fuer user_id=%s action=%s", interaction.user.id, action)
        return

    if action not in _VALID_ACTIONS:
        await interaction.response.send_message(f"Unbekannte Aktion: `{action}`.", ephemeral=True)
        return

    try:
        await interaction.response.defer(ephemeral=True)
    except discord.HTTPException as e:
        logger.error("Defer fehlgeschlagen: %s", e)
        return

    try:
        result = await asyncio.to_thread(_run_control, action, interaction.user.id)
    except subprocess.TimeoutExpired:
        await interaction.followup.send(f"`{action}` timeout (>40s).", ephemeral=True)
        logger.error("pipeline_control.py timeout action=%s", action)
        return
    except FileNotFoundError:
        await interaction.followup.send(f"CLI-Helper nicht gefunden: `{PIPELINE_CONTROL_SCRIPT}`", ephemeral=True)
        return
    except Exception as e:  # noqa: BLE001
        await interaction.followup.send(f"Unerwarteter Fehler: `{type(e).__name__}`", ephemeral=True)
        logger.error("pipeline_control.py Exception: %s", e, exc_info=True)
        return

    line = (result.stdout or "").strip() or (result.stderr or "").strip()
    if result.returncode == 0:
        await interaction.followup.send(line or f"`{action}` ok.", ephemeral=True)
        logger.info("Pipeline-Control action=%s by admin_id=%s -> %s",
                    action, interaction.user.id, line[:160])
        if action != "status" and interaction.message is not None:
            await _refresh_panel(interaction.message, action, line)
    else:
        await interaction.followup.send(
            f"`{action}` fehlgeschlagen (rc={result.returncode}):\n```{line[:400]}```", ephemeral=True)
        logger.warning("pipeline_control.py rc=%s action=%s stderr=%s",
                       result.returncode, action, (result.stderr or "")[:200])


# Umbau 2026-07-27: frueher baute der Button-Pfad das Embed selbst neu und setzte ein
# "Status"-Field mit der letzten Aktion. Neben dem Auto-Refresh-Loop haette das zwei
# konkurrierende Schreiber auf derselben Nachricht ergeben (doppelte/veraltete Felder,
# je nachdem wer zuletzt editiert). Jetzt gehoert das Embed ausschliesslich dem Loop;
# eine Aktion markiert es nur als "neu zeichnen". Das Ergebnis der Aktion sieht Marco
# ohnehin als ephemerale Antwort — und der Live-Status zeigt den tatsaechlich
# erreichten Zustand, was aussagekraeftiger ist als ein Aktions-Echo.
_PANEL_DIRTY = False


async def _refresh_panel(msg: discord.Message, action: str, status_line: str) -> None:
    """Markiert das Panel als neu zu zeichnen (der Refresh-Loop uebernimmt)."""
    global _PANEL_DIRTY
    _PANEL_DIRTY = True
    logger.debug("Panel als dirty markiert nach action=%s", action)


class ControlView(discord.ui.View):
    """Persistente View (timeout=None) — via bot.add_view restart-fest."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success,
                       emoji="▶️", custom_id="pipeline_ctl_start", row=0)
    async def _start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _handle_action(interaction, "start")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger,
                       emoji="⏹️", custom_id="pipeline_ctl_stop", row=0)
    async def _stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _handle_action(interaction, "stop")

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary,
                       emoji="🔄", custom_id="pipeline_ctl_status", row=0)
    async def _status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _handle_action(interaction, "status")

    @discord.ui.button(label="Modus (Burst/Normal)", style=discord.ButtonStyle.primary,
                       emoji="🔀", custom_id="pipeline_ctl_mode", row=1)
    async def _mode(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _handle_action(interaction, "mode")

    @discord.ui.button(label="Sweep (Text/Multimodal)", style=discord.ButtonStyle.primary,
                       emoji="🎞️", custom_id="pipeline_ctl_sweepmode", row=1)
    async def _sweepmode(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _handle_action(interaction, "sweepmode")

    @discord.ui.button(label="Tageslimit (an/aus)", style=discord.ButtonStyle.primary,
                       emoji="⏱️", custom_id="pipeline_ctl_daylimit", row=1)
    async def _daylimit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _handle_action(interaction, "daylimit")


class PipelineControlCog(commands.Cog):
    """Control-Panel fuer die Pipeline (Start/Stop + Burst/Normal-Modus + Status)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._panel_posted = False
        # Auto-Refresh-Zustand: die Panel-Nachricht selbst + Signatur des zuletzt
        # gerenderten Inhalts (Edit nur bei echter Aenderung).
        self._panel_msg: discord.Message | None = None
        self._last_sig: str = ""
        if not _marco_uid():
            logger.warning("MARCO_DISCORD_UID fehlt — Control-Buttons werden fuer ALLE abgewiesen.")

    async def cog_unload(self) -> None:
        self._refresh_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Postet das Panel automatisch beim ersten Ready (dedupliziert) — kein
        manuelles !pipeline_panel noetig. Re-Post bei Reconnect via Flag + History-Scan verhindert."""
        if self._panel_posted:
            return
        self._panel_posted = True
        await self._ensure_panel()
        if not self._refresh_loop.is_running():
            self._refresh_loop.start()

    async def _ensure_panel(self) -> None:
        ch = self.bot.get_channel(CONTROL_CHANNEL_ID)
        if ch is None:
            logger.warning("Control-Channel %s nicht gefunden / kein Bot-Zugriff — Panel nicht gepostet.",
                           CONTROL_CHANNEL_ID)
            return
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            logger.warning("Control-Channel %s ist kein Text-Channel (%s) — Panel nicht gepostet.",
                           CONTROL_CHANNEL_ID, type(ch).__name__)
            return
        # Dedup: existiert schon ein Panel (Bot-Embed mit unserem Titel)?
        try:
            async for m in ch.history(limit=30):
                if (m.author.id == (self.bot.user.id if self.bot.user else 0)
                        and m.embeds and (m.embeds[0].title or "").startswith("🛠 Pipeline")):
                    # Nachricht merken: der Auto-Refresh-Loop editiert genau diese.
                    # Ohne das faende er nach einem Bot-Restart kein Panel mehr.
                    self._panel_msg = m
                    logger.info("Control-Panel existiert bereits (msg_id=%s) — kein Re-Post, "
                                "uebernehme es fuer den Auto-Refresh.", m.id)
                    return
        except discord.HTTPException as e:
            logger.warning("Panel-History-Scan fehlgeschlagen (%s) — poste trotzdem.", e)
        status_line = ""
        try:
            res = await asyncio.to_thread(_run_control, "status",
                                          self.bot.user.id if self.bot.user else 0)
            if res.returncode == 0:
                status_line = (res.stdout or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("Initial-Status fuer Auto-Panel fehlgeschlagen: %s", e)
        try:
            self._panel_msg = await ch.send(embed=self._build_panel_embed(status_line),
                                            view=ControlView())
            logger.info("Control-Panel AUTO-gepostet in Channel %s", CONTROL_CHANNEL_ID)
        except discord.HTTPException as e:
            logger.warning("Panel Auto-Post fehlgeschlagen: %s", e)

    @tasks.loop(seconds=REFRESH_SECONDS)
    async def _refresh_loop(self) -> None:
        """Haelt das Panel-Embed live.

        Editiert NUR wenn sich der gerenderte Status geaendert hat — im Leerlauf also
        null Discord-API-Calls. Jede Fehlerquelle (Nachricht geloescht, Status kaputt,
        HTTP-Fehler) ist abgefangen: ein Anzeige-Loop darf den Bot nie mitreissen.
        """
        global _PANEL_DIRTY
        msg = self._panel_msg
        if msg is None:
            return
        st = await _live_status()
        if st is None:
            return
        sig = _panel_signature(st)
        if _PANEL_DIRTY:
            # Button-Aktion: sofort neu zeichnen, auch wenn der Status (noch) gleich
            # aussieht — Marco soll unmittelbar sehen, dass sein Klick angekommen ist.
            _PANEL_DIRTY = False
        elif sig == self._last_sig:
            return
        embed = self._build_panel_embed(live=st)
        try:
            await msg.edit(embed=embed, view=ControlView())
            self._last_sig = sig
        except discord.NotFound:
            # Panel wurde geloescht — Referenz fallen lassen, on_ready/!pipeline_panel
            # legt bei Bedarf ein neues an. Weiter-Editieren waere sinnlos.
            logger.warning("Panel-Nachricht nicht mehr vorhanden — Auto-Refresh pausiert.")
            self._panel_msg = None
        except discord.HTTPException as e:
            logger.debug("Panel-Auto-Refresh fehlgeschlagen (retry naechster Tick): %s", e)

    @_refresh_loop.before_loop
    async def _before_refresh(self) -> None:
        await self.bot.wait_until_ready()

    def _build_panel_embed(self, status_line: str = "", live: dict | None = None) -> discord.Embed:
        embed = discord.Embed(
            title=PANEL_TITLE,
            description=(
                "Steuerung der Curation-Pipeline. Run-Zustand und Modus sind unabhaengig.\n\n"
                "▶️ **Start** — Pipeline AN: Pause + Datei-Flag aufheben. Der Modus bleibt, wie er ist.\n"
                "⏹️ **Stop** — Pipeline AUS: pausieren (kein Auto-Resume). Weiter via Start.\n"
                "🔀 **Modus (Burst/Normal)** — Drosselung umschalten. "
                "_Burst_ = interne Throttles aus (Anthropic-Limit bremst weiter), "
                "_Normal_ = Marco-Stunden + Tages-/5h-Cap aktiv.\n"
                "🎞️ **Sweep (Text/Multimodal)** — Modus des Re-Analyse-Sweeps: "
                "_Text_ = guenstig (nur Transcript), _Multimodal_ = genau (Vision-Keyframes).\n"
                "⏱️ **Tageslimit (an/aus)** — Marco-Stunden-Pause + Tages-/5h-Caps ab-/anschalten "
                "(fuer Bulk-Laeufe tagsueber ohne Burst).\n"
                "🔄 **Status** — aktuellen Stand anzeigen.\n\n"
                "_Nur Marco kann die Buttons nutzen._"
            ),
            # Farbe folgt dem Zustand (gruen = laeuft, rot = pausiert), nicht der
            # zuletzt gedrueckten Taste — auf einen Blick erkennbar ohne Mitlesen.
            color=(0x5865F2 if live is None else (0xED4245 if live.get("paused") else 0x57F287)),
        )
        if live is not None:
            embed.add_field(name="Live-Status", value=_fmt_live_status(live)[:1000], inline=False)
            stamp = datetime.now(BERLIN).strftime("%H:%M:%S")
            embed.set_footer(text=f"aktualisiert sich automatisch · Stand {stamp} Berlin")
        elif status_line:
            embed.add_field(name="Status", value=status_line[:600], inline=False)
        return embed

    @commands.command(name="pipeline_panel")
    async def pipeline_panel(self, ctx: commands.Context) -> None:
        """Postet das Control-Panel (nur Marco)."""
        if ctx.author.id != _marco_uid():
            await ctx.reply("Nur Marco.", mention_author=False)
            return
        # Initial-Status holen (best-effort).
        status_line = ""
        try:
            res = await asyncio.to_thread(_run_control, "status", ctx.author.id)
            if res.returncode == 0:
                status_line = (res.stdout or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("Initial-Status fuer Panel fehlgeschlagen: %s", e)

        channel = self.bot.get_channel(CONTROL_CHANNEL_ID) or ctx.channel
        try:
            await channel.send(embed=self._build_panel_embed(status_line), view=ControlView())
            if channel.id != ctx.channel.id:
                await ctx.reply(f"Panel gepostet in <#{channel.id}>.", mention_author=False)
        except discord.HTTPException as e:
            await ctx.reply(f"Panel konnte nicht gepostet werden: {e}", mention_author=False)


async def setup(bot: commands.Bot) -> None:
    bot.add_view(ControlView())   # persistente Buttons (restart-fest)
    await bot.add_cog(PipelineControlCog(bot))
    logger.info("PipelineControlCog geladen — MARCO_DISCORD_UID=%s, channel=%s",
                "gesetzt" if os.getenv("MARCO_DISCORD_UID") else "FEHLT", CONTROL_CHANNEL_ID)

"""
Pipeline-Control-Cog — Discord-Control-Panel fuer die TikTok-Curation-Pipeline.

Stand: 2026-07-14 (Umbau: Run-Achse von Modus-Achse entkoppelt)

Components-V2-Panel mit 6 persistenten Buttons (ueberleben Bot-Restart via
bot.add_view); seit 2026-08-13 kein Embed mehr, der Zustand steht im Kopf:
  ▶ Start  (custom_id pipeline_ctl_start)     -> Pipeline AN (Pause + Datei-Flag loesen)
  ⏹ Stop   (custom_id pipeline_ctl_stop)      -> Pipeline AUS (pause_active=1)
  🔄 Status (custom_id pipeline_ctl_status)    -> aktuellen Stand anzeigen (ephemeral)
  🔀 Modus (custom_id pipeline_ctl_mode)       -> Burst <-> Normal toggeln (sweep_active)
  🎞️ Sweep (custom_id pipeline_ctl_sweepmode)  -> Text <-> Multimodal
  ⏱️ Tageslimit (custom_id pipeline_ctl_daylimit) -> Caps ab-/anschalten

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
from utils.panels import action_row, hud_container
from utils.ui_kit import status_dot, subtext, truncate
from utils.loop_guard import guard

logger = get_logger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")

PANEL_TITLE = "PIPELINE"
# Titel-Anfang der alten Embed-Fassung — nur noch fuer die Erkennung beim Umstieg.
LEGACY_PANEL_PREFIX = "🛠 Pipeline"

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
# 30 Sekunden statt der frueheren 15: das Panel zeigt Zustaende, die sich im
# Minutentakt aendern (Dienst laeuft / laeuft nicht, letzte Ausfuehrung), und
# jeder Takt ist eine Nachrichten-Bearbeitung gegen die Discord-API. Der halbe
# Takt kostete das doppelte Kontingent ohne sichtbaren Gewinn. Ueber
# PIPELINE_PANEL_REFRESH_SECONDS weiterhin einstellbar.
REFRESH_SECONDS = int(os.getenv("PIPELINE_PANEL_REFRESH_SECONDS", "30") or "30")


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
        daten = json.loads((res.stdout or "").strip())
    except (json.JSONDecodeError, ValueError):
        return None
    # Ein JSON-Array waere gueltiges JSON, aber .get() gibt es dort nicht —
    # ohne diese Pruefung stuerbe der Refresh-Loop daran und bliebe fuer immer
    # stehen, waehrend die Buttons weiter funktionieren (kein Symptom).
    return daten if isinstance(daten, dict) else None


def _panel_inhalt(st: dict) -> tuple[str, list[tuple[str, str]], list[str]]:
    """
    Live-Felder -> (Zustand, Kennzahlen, Zeilen). Eine Quelle für Panel UND Signatur.

    Reine Funktion: der Refresh-Loop vergleicht damit billig, ob sich überhaupt
    etwas geändert hat, bevor er einen Discord-Edit abschickt.
    """
    pausiert = bool(st.get("paused"))
    zustand = "crit" if pausiert else "ok"

    kennzahlen = [
        (f"{st.get('today', 0)}/{st.get('day_cap', 0)}", "heute"),
        (f"{st.get('window', 0)}/{st.get('window_cap', 0)}", "im 5h-Fenster"),
    ]
    pending = st.get("inbox_pending")
    if pending is not None:
        kennzahlen.append((str(pending), "in der Warteschlange"))

    lauf = "pausiert" if pausiert else "aktiv"
    kopf = f"{status_dot('crit' if pausiert else 'ok')} **{lauf}**"
    if pausiert and st.get("pause_reason"):
        kopf += f" · {st['pause_reason']}"
    kopf += f" · Daemon {'läuft' if st.get('daemon') else 'aus'}"

    zeilen = [
        kopf,
        subtext(
            f"Modus {st.get('modus', '?')} · Sweep {st.get('sweep_mode', '?')} "
            f"· Tageslimit {st.get('tageslimit', '?')}"
        ),
    ]
    if st.get("keepers_left"):
        zeilen.append(subtext(f"offene Keeper {st['keepers_left']}"))
    letztes = st.get("last_finding") or None
    if letztes:
        zeilen.append(subtext(
            f"zuletzt {truncate(letztes.get('title') or '—', 60)} · "
            f"{letztes.get('category', '?')} · {letztes.get('started_at', '?')}"
        ))
    return zustand, kennzahlen, zeilen


def _panel_signature(st: dict) -> str:
    """Alles ausser der Uhrzeit — sonst wuerde jeder Tick ein Edit ausloesen."""
    return repr(_panel_inhalt(st))


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


def _ist_control_panel(msg: discord.Message) -> bool:
    """
    Traegt diese Nachricht unsere Buttons?

    Components V2 verschachtelt (Container -> Section/ActionRow -> Button), deshalb
    wird rekursiv gesucht statt eine feste Struktur anzunehmen.
    """
    def _suche(elemente) -> bool:
        for element in elemente or ():
            if str(getattr(element, "custom_id", "") or "").startswith("pipeline_ctl_"):
                return True
            zubehoer = getattr(element, "accessory", None)
            if zubehoer is not None and _suche([zubehoer]):
                return True
            for attribut in ("children", "components", "items"):
                kinder = getattr(element, attribut, None)
                if kinder and _suche(kinder):
                    return True
        return False

    return _suche(getattr(msg, "components", None))


class ControlButton(discord.ui.Button):
    """Ein Panel-Button; die ``custom_id`` traegt die Aktion und ueberlebt Neustarts."""

    def __init__(self, action: str, label: str, emoji: str,
                 style: discord.ButtonStyle = discord.ButtonStyle.primary) -> None:
        super().__init__(label=label, emoji=emoji, style=style,
                         custom_id=f"pipeline_ctl_{action}")
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_action(interaction, self.action)


# Reihenfolge und Aufteilung der Buttons — Zeile 1 schaltet den Lauf, Zeile 2 die
# Drosselung. Discord erlaubt fuenf Buttons je Zeile.
_BUTTONS_LAUF = (
    ("start", "Start", "▶️", discord.ButtonStyle.success),
    ("stop", "Stop", "⏹️", discord.ButtonStyle.danger),
    ("status", "Status", "🔄", discord.ButtonStyle.secondary),
)
_BUTTONS_MODUS = (
    ("mode", "Modus", "🔀", discord.ButtonStyle.primary),
    ("sweepmode", "Sweep", "🎞️", discord.ButtonStyle.primary),
    ("daylimit", "Tageslimit", "⏱️", discord.ButtonStyle.primary),
)

_LEGENDE = (
    "-# Modus: Burst = interne Throttles aus · Normal = Marco-Stunden und Caps aktiv\n"
    "-# Sweep: Text = günstig · Multimodal = genau (Vision-Keyframes)\n"
    "-# Tageslimit schaltet Marco-Stunden und Tages-/5h-Cap zusammen ab\n"
    "-# Nur Marco kann die Buttons nutzen."
)


class ControlPanelView(discord.ui.LayoutView):
    """
    Das Control-Panel als Components-V2-Layout (timeout=None, restart-fest).

    Umstieg 2026-08-13: vorher ein Embed mit Feld „Live-Status". Der Zustand steht
    jetzt im Kopf statt in einem Feld, und die Buttons gehoeren sichtbar zum Panel
    statt darunter zu haengen. Die ``custom_id``s sind unveraendert — Klicks auf ein
    altes, noch stehendes Panel landen weiterhin hier.
    """

    def __init__(self, live: dict | None = None, status_line: str = "") -> None:
        super().__init__(timeout=None)

        if live is not None:
            zustand, kennzahlen, zeilen = _panel_inhalt(live)
            stempel = datetime.now(BERLIN).strftime("%H:%M:%S")
            fuss = f"aktualisiert sich automatisch · Stand {stempel} Berlin"
        else:
            zustand, kennzahlen = "info", []
            zeilen = ([discord.utils.escape_markdown(status_line[:600])]
                      if status_line else ["Status noch nicht abgerufen."])
            fuss = "aktualisiert sich automatisch"

        self.add_item(hud_container(
            PANEL_TITLE,
            state=zustand,
            subtitle="Steuerung der Curation-Pipeline",
            meta=kennzahlen or None,
            rows=[discord.ui.TextDisplay("\n".join(zeilen)),
                  discord.ui.TextDisplay(_LEGENDE)],
            actions=[
                action_row(*(ControlButton(a, label, emoji, style)
                             for a, label, emoji, style in _BUTTONS_LAUF)),
                action_row(*(ControlButton(a, label, emoji, style)
                             for a, label, emoji, style in _BUTTONS_MODUS)),
            ],
            footer_note=fuss,
            with_dot=False,
        ))


class PipelineControlCog(commands.Cog):
    """Control-Panel fuer die Pipeline (Start/Stop + Burst/Normal-Modus + Status)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._panel_posted = False
        # Auto-Refresh-Zustand: die Panel-Nachricht selbst + Signatur des zuletzt
        # gerenderten Inhalts (Edit nur bei echter Aenderung).
        self._panel_msg: discord.Message | None = None
        self._last_sig: str = ""
        self._fehlschlaege: int = 0
        if not _marco_uid():
            logger.warning("MARCO_DISCORD_UID fehlt — Control-Buttons werden fuer ALLE abgewiesen.")

    async def cog_load(self) -> None:
        # on_ready ist die uebliche Startquelle, feuert nach einem
        # reload_extension im laufenden Betrieb aber nicht mehr.
        guard(self._refresh_loop, name="pipeline_panel_refresh")
        if not self._refresh_loop.is_running():
            self._refresh_loop.start()

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
        # Dedup: existiert schon ein Panel? Zwei Formen sind moeglich — das neue
        # Components-V2-Panel (uebernehmen) und das alte Embed-Panel. Ein Embed
        # laesst sich nicht auf V2 umstellen, Discord setzt das Flag beim Senden.
        # Also: altes Panel loeschen und neu posten, sonst stehen zwei Panels mit
        # denselben Buttons im Channel und nur eines aktualisiert sich.
        eigene_id = self.bot.user.id if self.bot.user else 0
        try:
            async for m in ch.history(limit=30):
                if m.author.id != eigene_id:
                    continue
                if m.flags.components_v2 and _ist_control_panel(m):
                    # Nachricht merken: der Auto-Refresh-Loop editiert genau diese.
                    # Ohne das faende er nach einem Bot-Restart kein Panel mehr.
                    self._panel_msg = m
                    logger.info("Control-Panel existiert bereits (msg_id=%s) — kein Re-Post, "
                                "uebernehme es fuer den Auto-Refresh.", m.id)
                    return
                # Titel-Praefix allein reicht als Loeschgrund nicht — die
                # Nachricht muss auch unsere Buttons tragen, sonst koennte eine
                # fremde Bot-Quittung mit aehnlichem Titel mitgeloescht werden.
                if (m.embeds
                        and (m.embeds[0].title or "").startswith(LEGACY_PANEL_PREFIX)
                        and _ist_control_panel(m)):
                    try:
                        await m.delete()
                        logger.info("Altes Embed-Panel (msg_id=%s) entfernt — Umstieg auf "
                                    "Components V2 braucht eine neue Nachricht.", m.id)
                    except discord.HTTPException as e:
                        logger.warning("Altes Embed-Panel nicht loeschbar (%s) — es bleibt "
                                       "stehen und aktualisiert sich nicht mehr.", e)
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
            self._panel_msg = await ch.send(view=ControlPanelView(status_line=status_line))
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
        try:
            st = await _live_status()
        except Exception as e:  # noqa: BLE001
            # tasks.loop beendet die Schleife bei einer unbehandelten Ausnahme
            # endgueltig. Ein Anzeige-Loop darf daran nicht sterben.
            logger.warning("Status-Abfrage fuer das Panel fehlgeschlagen: %s", e)
            return
        if st is None:
            return
        sig = _panel_signature(st)
        # Das Dirty-Flag erst JETZT lesen: ein Klick waehrend der Statusabfrage
        # gehoert zum naechsten Bild, nicht zu dem gerade geholten.
        if _PANEL_DIRTY:
            # Button-Aktion: sofort neu zeichnen, auch wenn der Status (noch) gleich
            # aussieht — Marco soll unmittelbar sehen, dass sein Klick angekommen ist.
            _PANEL_DIRTY = False
        elif sig == self._last_sig:
            return
        try:
            await msg.edit(view=ControlPanelView(live=st))
            self._last_sig = sig
            self._fehlschlaege = 0
        except discord.NotFound:
            # Panel wurde geloescht — Referenz fallen lassen, on_ready/!pipeline_panel
            # legt bei Bedarf ein neues an. Weiter-Editieren waere sinnlos.
            logger.warning("Panel-Nachricht nicht mehr vorhanden — Auto-Refresh pausiert.")
            self._panel_msg = None
        except discord.HTTPException as e:
            # Einzelne Fehlschlaege sind normal (Rate-Limit, kurzer Ausfall).
            # Ein dauerhaft totes Panel sah bisher genauso aus wie "nichts
            # geaendert" — ab der dritten Runde deshalb sichtbar melden.
            self._fehlschlaege += 1
            if self._fehlschlaege >= 3:
                logger.warning(
                    "Panel-Auto-Refresh scheitert seit %d Runden: %s",
                    self._fehlschlaege, e
                )
            else:
                logger.debug("Panel-Auto-Refresh fehlgeschlagen (retry naechster Tick): %s", e)

    @_refresh_loop.before_loop
    async def _before_refresh(self) -> None:
        await self.bot.wait_until_ready()

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
            # Die neue Nachricht wird das gepflegte Panel — sonst stuenden zwei
            # mit denselben Buttons da und nur das alte aktualisierte sich.
            self._panel_msg = await channel.send(
                view=ControlPanelView(status_line=status_line)
            )
            self._last_sig = ""
            if channel.id != ctx.channel.id:
                await ctx.reply(f"Panel gepostet in <#{channel.id}>.", mention_author=False)
        except discord.HTTPException as e:
            await ctx.reply(f"Panel konnte nicht gepostet werden: {e}", mention_author=False)


async def setup(bot: commands.Bot) -> None:
    bot.add_view(ControlPanelView())   # persistente Buttons (restart-fest)
    await bot.add_cog(PipelineControlCog(bot))
    logger.info("PipelineControlCog geladen — MARCO_DISCORD_UID=%s, channel=%s",
                "gesetzt" if os.getenv("MARCO_DISCORD_UID") else "FEHLT", CONTROL_CHANNEL_ID)

"""
MCCountdownTimer — Erweitert RestartTimer um MC-spezifische RCON /title Banner.

Der bestehende RestartTimer nutzt die Satisfactory API für In-Game-Nachrichten.
MCCountdownTimer ersetzt diese Logik durch RCON-Befehle für Minecraft-Server:
- /title @a title {...}   — Großes Banner mittig auf dem Bildschirm
- /title @a subtitle {...} — Untertitel unter dem Banner
- /tellraw @a [...]        — Formatierte Chat-Nachricht

Nutzung:
    timer = MCCountdownTimer(mc_server, channel)
    result = await timer.countdown(
        duration_minutes=10,
        action_name="Update",
        extra_info="v48.5",
    )
"""

import asyncio
from typing import Optional, List

import discord

from modules.restart_timer import RestartTimer, TimerResult
from utils.logger import get_logger
from utils.ui_kit import subtext

logger = get_logger("minecraft.mc_countdown")


class MCCountdownTimer(RestartTimer):
    """
    Countdown-Timer mit MC-spezifischen RCON /title Bannern.

    Erweitert RestartTimer und überschreibt die In-Game-Nachrichten-Logik.
    Discord-Nachrichten werden vom RestartTimer geerbt.
    """

    cancel_hint = "`/modpack cancel` oder In-Game `!cancel` bricht ab"

    def __init__(
        self,
        mc_server=None,
        channel: Optional[discord.TextChannel] = None,
        extra_info: str = "",
    ) -> None:
        """
        Args:
            mc_server: MinecraftServer-Instanz (für RCON-Zugriff)
            channel: Discord-Textkanal für Nachrichten
            extra_info: Zusatzinfo für Banner (z.B. "Update auf v48.5")
        """
        # RestartTimer OHNE api initialisieren — wir nutzen RCON statt SAT API
        super().__init__(api=None, channel=channel)
        self.mc_server = mc_server
        self.extra_info = extra_info
        self._30s_task: Optional[asyncio.Task] = None

    async def countdown(
        self,
        duration_minutes: int = 10,
        action_name: str = "Update",
        warnings: Optional[List[int]] = None,
        on_complete=None,
        extra_info: str = "",
    ) -> TimerResult:
        """
        Startet Countdown mit MC-spezifischen Warnungen.

        Erweitert die Standard-Warnings um 2-Minuten und 30-Sekunden-Banner.
        """
        if extra_info:
            self.extra_info = extra_info

        # MC-spezifische Warning-Intervalle (inkl. 2 Min)
        if warnings is None:
            if duration_minutes >= 10:
                warnings = [10, 5, 2, 1]
            elif duration_minutes >= 5:
                warnings = [5, 2, 1]
            else:
                warnings = [duration_minutes, 1]

        return await super().countdown(
            duration_minutes=duration_minutes,
            action_name=action_name,
            warnings=warnings,
            on_complete=on_complete,
        )

    # ------------------------------------------------------------------
    # Überschriebene Methoden für MC-spezifische In-Game-Nachrichten
    # ------------------------------------------------------------------

    async def _send_warning(
        self,
        message: str,
        is_initial: bool = False,
        is_final: bool = False,
    ) -> None:
        """
        Sendet Warnung an Discord (MC-spezifisch) + In-Game via RCON.

        Ueberschreibt Discord-Text fuer MC-spezifische Cancel-Hinweise.
        In-Game-Logik wird ueber _send_ingame_warning() dispatched (I6).
        """
        # Discord: dasselbe Panel wie beim Basis-Timer, nur mit MC-Hinweisen
        if self.channel:
            try:
                await self._update_panel(
                    message, is_initial=is_initial, is_final=is_final
                )
            except discord.DiscordException as e:
                logger.debug(f"Discord-Nachricht fehlgeschlagen: {e}")

        # In-Game via RCON (I6: ueberschriebene Methode)
        await self._send_ingame_warning(message, is_initial, is_final)

    async def _send_ingame_warning(
        self,
        message: str,
        is_initial: bool = False,
        is_final: bool = False,
    ) -> None:
        """
        RCON /title Banner statt SAT API (I6: Override von RestartTimer).

        Banner-Stil:
        - > 2 Min: Gold (Server Update)
        - <= 2 Min: Rot (Server Neustart)
        - Final: Dunkelrot
        """
        if not self.mc_server:
            return

        try:
            minutes = self._extract_minutes(message)

            if is_final:
                await self._rcon_title(
                    title_text="SERVER NEUSTART",
                    title_color="dark_red",
                    subtitle_text="Jetzt!",
                    subtitle_color="red",
                )
            elif is_initial:
                subtitle = f"in {minutes} Minuten"
                if self.extra_info:
                    subtitle += f" \u2014 {self.extra_info}"
                await self._rcon_title(
                    title_text="SERVER UPDATE",
                    title_color="gold",
                    subtitle_text=subtitle,
                    subtitle_color="yellow",
                )
            elif minutes is not None and minutes <= 2:
                await self._rcon_title(
                    title_text="SERVER NEUSTART",
                    title_color="red",
                    subtitle_text=f"in {minutes} Minute{'n' if minutes != 1 else ''}!",
                    subtitle_color="red",
                )
            else:
                await self._rcon_title(
                    title_text="SERVER UPDATE",
                    title_color="gold",
                    subtitle_text=f"in {minutes} Minuten" if minutes else "",
                    subtitle_color="yellow",
                )

            # 30-Sekunden-Warnung am Ende (BUG-1: Task statt call_later)
            if minutes == 1:
                self._30s_task = asyncio.create_task(self._delayed_30s_warning())

        except Exception as e:
            logger.debug(f"MC-Banner fehlgeschlagen: {e}")

    async def _delayed_30s_warning(self) -> None:
        """30-Sekunden-Warnung als cancelbarer Task (BUG-1)."""
        try:
            await asyncio.sleep(30)
            await self._rcon_title(
                "SERVER NEUSTART", "dark_red",
                "in 30 Sekunden!", "red",
            )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"30s-Warnung fehlgeschlagen: {e}")

    def _countdown_embed(self, message: str, **kwargs):
        """Panel des Basis-Timers, ergaenzt um die Zusatzinfo (z.B. Zielversion).

        Der Abbruch-Weg unterscheidet sich ebenfalls; der steckt in
        :attr:`cancel_hint` und wird vom Basis-Panel gesetzt.
        """
        embed = super()._countdown_embed(message, **kwargs)
        if kwargs.get("is_final") or kwargs.get("cancelled") or not self.extra_info:
            return embed

        embed.description = "\n".join(
            [t for t in (embed.description, subtext(self.extra_info)) if t]
        )
        return embed

    async def _send_cancel_message(self) -> None:
        """Sendet Abbruch-Nachricht (BUG-1: 30s-Task abbrechen, I6: super nutzen)."""
        if self._30s_task and not self._30s_task.done():
            self._30s_task.cancel()
        await super()._send_cancel_message()

    async def _send_ingame_cancel(self) -> None:
        """RCON Cancel-Banner statt SAT API (I6: Override von RestartTimer)."""
        if self.mc_server:
            try:
                await self._rcon_title(
                    title_text="UPDATE ABGEBROCHEN",
                    title_color="green",
                    subtitle_text="Entwarnung \u2014 Server l\u00e4uft weiter",
                    subtitle_color="green",
                )
            except Exception as e:
                logger.debug(f"MC-Abbruch-Banner fehlgeschlagen: {e}")

    async def send_success_banner(self, new_version: str) -> None:
        """Sendet Erfolgs-Banner nach erfolgreichem Update."""
        if self.mc_server:
            try:
                await self._rcon_title(
                    title_text="Server aktualisiert!",
                    title_color="green",
                    subtitle_text=f"Neue Version: {new_version}",
                    subtitle_color="green",
                )
            except Exception as e:
                logger.debug(f"Erfolgs-Banner fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # RCON-Hilfsmethoden
    # ------------------------------------------------------------------

    async def _rcon_title(
        self,
        title_text: str,
        title_color: str = "gold",
        subtitle_text: str = "",
        subtitle_color: str = "yellow",
    ) -> None:
        """
        Sendet /title + /subtitle Befehle via RCON.

        Args:
            title_text: Haupttext (groß, mittig)
            title_color: MC-Farbname (gold, red, dark_red, green, etc.)
            subtitle_text: Untertitel
            subtitle_color: MC-Farbname für Untertitel
        """
        if not self.mc_server:
            return

        title_cmd = (
            f'/title @a title '
            f'{{"text":"{title_text}","color":"{title_color}"}}'
        )
        await self._safe_rcon(title_cmd)

        if subtitle_text:
            sub_cmd = (
                f'/title @a subtitle '
                f'{{"text":"{subtitle_text}","color":"{subtitle_color}"}}'
            )
            await self._safe_rcon(sub_cmd)

    async def _safe_rcon(self, command: str) -> Optional[str]:
        """Sendet RCON-Befehl mit Fehlerbehandlung."""
        try:
            return await self.mc_server.rcon_command(command)
        except Exception as e:
            logger.debug(f"RCON-Befehl fehlgeschlagen: {command[:50]}... — {e}")
            return None

    @staticmethod
    def _extract_minutes(message: str) -> Optional[int]:
        """Extrahiert Minutenzahl aus einer Warnnachricht."""
        import re
        match = re.search(r'(\d+)\s*Minute', message)
        if match:
            return int(match.group(1))
        return None

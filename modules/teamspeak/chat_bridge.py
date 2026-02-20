"""
TeamSpeak <-> Discord Chat-Bridge — Bidirektionale Nachrichtenuebertragung.

Leitet Nachrichten aus einem TeamSpeak-Channel an einen Discord-Channel weiter
und umgekehrt. Unterstuetzt BBCode-zu-Markdown- und Markdown-zu-BBCode-Konvertierung.

Die Bridge pollt den TS-Server regelmaessig nach neuen Nachrichten,
da die ts3-Bibliothek kein Event-basiertes Listening unterstuetzt.
"""

import asyncio
import re
from typing import Optional

import discord

from utils.logger import get_logger

logger = get_logger("modules.teamspeak.chat_bridge")


class TSChatBridge:
    """Bidirektionale Chat-Bridge zwischen TeamSpeak und Discord.

    Pollt den TeamSpeak-Server auf neue Textnachrichten und leitet
    sie an den konfigurierten Discord-Channel weiter. Nachrichten
    aus Discord werden ueber send_to_ts() an den TS-Channel gesendet.
    """

    # Polling-Intervall in Sekunden
    POLL_INTERVAL = 2.0
    # Maximale Nachrichtenlaenge fuer die Weiterleitung
    MAX_MESSAGE_LENGTH = 500
    # Nachrichten-Cache-Groesse (Duplikat-Erkennung)
    MESSAGE_CACHE_SIZE = 50

    def __init__(
        self,
        ts_client,
        bot: discord.Client,
        discord_channel_id: int,
        ts_channel_id: int = 0,
    ):
        """Initialisiert die Chat-Bridge.

        Args:
            ts_client: TSClient-Instanz fuer die TS-Kommunikation.
            bot: Discord-Bot-Instanz.
            discord_channel_id: ID des Discord-Channels fuer die Bridge.
            ts_channel_id: TS-Channel-ID (0 = Server-Chat).
        """
        self.ts_client = ts_client
        self.bot = bot
        self.discord_channel_id = discord_channel_id
        self.ts_channel_id = ts_channel_id

        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._seen_messages: list = []  # Cache fuer bereits weitergeleitete Nachrichten

    @property
    def is_running(self) -> bool:
        """Gibt zurueck ob die Bridge aktiv ist."""
        return self._running

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """Startet die Chat-Bridge.

        Returns:
            True wenn erfolgreich gestartet, False bei Fehler.
        """
        if self._running:
            logger.warning("Chat-Bridge laeuft bereits.")
            return False

        if not self.ts_client.is_connected():
            logger.error("Chat-Bridge kann nicht starten: Keine TS-Verbindung.")
            return False

        # TS-Server ueber Textmessage-Events informieren (registriere Notifications)
        try:
            await self.ts_client.register_notifications(self.ts_channel_id)
        except Exception as e:
            logger.warning(
                f"TS-Notification-Registrierung fehlgeschlagen: {e}. "
                f"Fallback auf Polling."
            )

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_ts_messages())
        logger.info(
            f"Chat-Bridge gestartet (Discord: {self.discord_channel_id}, "
            f"TS-Channel: {self.ts_channel_id})"
        )
        return True

    async def stop(self) -> None:
        """Stoppt die Chat-Bridge."""
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._poll_task = None
        self._seen_messages.clear()
        logger.info("Chat-Bridge gestoppt.")

    # ------------------------------------------------------------------
    # Discord -> TeamSpeak
    # ------------------------------------------------------------------

    async def send_to_ts(self, author: str, message: str) -> bool:
        """Sendet eine Nachricht von Discord an den TeamSpeak-Channel.

        Args:
            author: Name des Discord-Autors.
            message: Die Nachricht.

        Returns:
            True bei Erfolg, False bei Fehler.
        """
        if not self._running:
            return False

        # Nachricht kuerzen falls noetig
        if len(message) > self.MAX_MESSAGE_LENGTH:
            message = message[:self.MAX_MESSAGE_LENGTH] + "..."

        # Markdown in BBCode umwandeln, BBCode in Autor-Name escapen
        ts_message = self._markdown_to_bbcode(message)
        safe_author = author.replace("[", "(").replace("]", ")")

        # Formatierte Nachricht mit Autor
        formatted = f"[b][Discord][/b] {safe_author}: {ts_message}"

        try:
            # target_mode: 2 = Channel-Nachricht, 3 = Server-Nachricht
            if self.ts_channel_id > 0:
                target_mode = 2
                target_id = self.ts_channel_id
            else:
                target_mode = 3
                target_id = self.ts_client.server_id
            success = await self.ts_client.send_text_message(
                target_mode=target_mode,
                target_id=target_id,
                message=formatted,
            )
            if success:
                logger.debug(f"Discord->TS: [{author}] {message[:80]}")
            return success
        except Exception as e:
            logger.error(f"Fehler beim Senden an TS: {e}")
            return False

    # ------------------------------------------------------------------
    # TeamSpeak -> Discord (Polling)
    # ------------------------------------------------------------------

    async def _poll_ts_messages(self) -> None:
        """Hintergrund-Task: Pollt TS regelmaessig nach neuen Nachrichten.

        Nutzt servernotifyregister fuer Channel-Text-Events.
        Faellt auf Client-Listen-Vergleich zurueck, falls Events
        nicht unterstuetzt werden.
        """
        logger.debug("TS-Polling-Task gestartet.")
        while self._running:
            try:
                await self._check_ts_events()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Fehler im TS-Polling: {e}")

            await asyncio.sleep(self.POLL_INTERVAL)

        logger.debug("TS-Polling-Task beendet.")

    async def _check_ts_events(self) -> None:
        """Prueft auf neue TS-Events und leitet Nachrichten weiter."""
        try:
            events = await self._fetch_ts_events()
            if not events:
                return

            discord_channel = self.bot.get_channel(self.discord_channel_id)
            if not discord_channel:
                logger.warning(
                    f"Discord-Channel {self.discord_channel_id} nicht gefunden."
                )
                return

            for event in events:
                msg_text = event.get("msg", "")
                invoker = event.get("invokername", "Unbekannt")

                # Duplikat-Check
                msg_hash = f"{invoker}:{msg_text}:{event.get('timestamp', '')}"
                if msg_hash in self._seen_messages:
                    continue
                self._seen_messages.append(msg_hash)
                # Cache-Groesse begrenzen
                if len(self._seen_messages) > self.MESSAGE_CACHE_SIZE:
                    self._seen_messages = self._seen_messages[-self.MESSAGE_CACHE_SIZE:]

                # BBCode in Markdown umwandeln + Mentions escapen
                md_message = self._bbcode_to_markdown(msg_text)
                md_message = discord.utils.escape_mentions(md_message)
                # Auf Discord-Embed-Limit beschraenken
                if len(md_message) > 4000:
                    md_message = md_message[:4000] + "..."

                # An Discord weiterleiten
                embed = discord.Embed(
                    description=md_message,
                    color=0x2980b9,
                )
                embed.set_author(name=f"[TS] {invoker}")
                embed.set_footer(text="TeamSpeak Chat-Bridge")

                try:
                    await discord_channel.send(embed=embed)
                    logger.debug(f"TS->Discord: [{invoker}] {msg_text[:80]}")
                except discord.HTTPException as e:
                    logger.error(f"Discord-Sende-Fehler: {e}")

        except Exception as e:
            logger.debug(f"TS-Event-Check-Fehler: {e}")

    async def _fetch_ts_events(self) -> list:
        """Holt neue Events vom TS-Server ueber die oeffentliche TSClient-API.

        Nutzt ts_client.fetch_events() statt direktem _connection-Zugriff
        fuer Thread-Sicherheit.

        Returns:
            Liste von Event-Dicts oder leere Liste.
        """
        if not self.ts_client.is_connected():
            return []

        try:
            return await self.ts_client.fetch_events()
        except Exception as e:
            logger.debug(f"TS-Event-Abruf fehlgeschlagen: {e}")
            return []

    # ------------------------------------------------------------------
    # BBCode <-> Markdown Konvertierung
    # ------------------------------------------------------------------

    @staticmethod
    def _bbcode_to_markdown(text: str) -> str:
        """Konvertiert BBCode-Tags in Markdown-Formatierung.

        Unterstuetzte Tags:
            [b]...[/b]        -> **...**
            [i]...[/i]        -> *...*
            [u]...[/u]        -> __...__
            [s]...[/s]        -> ~~...~~
            [url=X]Y[/url]    -> [Y](X)
            [url]X[/url]      -> X
            [code]...[/code]  -> `...`
            [color=X]Y[/color] -> Y (Farbe wird entfernt)
            [size=X]Y[/size]  -> Y (Groesse wird entfernt)

        Args:
            text: BBCode-formatierter Text.

        Returns:
            Markdown-formatierter Text.
        """
        if not text:
            return ""

        result = text

        # Bold
        result = re.sub(r'\[b\](.*?)\[/b\]', r'**\1**', result, flags=re.IGNORECASE | re.DOTALL)
        # Italic
        result = re.sub(r'\[i\](.*?)\[/i\]', r'*\1*', result, flags=re.IGNORECASE | re.DOTALL)
        # Underline
        result = re.sub(r'\[u\](.*?)\[/u\]', r'__\1__', result, flags=re.IGNORECASE | re.DOTALL)
        # Strikethrough
        result = re.sub(r'\[s\](.*?)\[/s\]', r'~~\1~~', result, flags=re.IGNORECASE | re.DOTALL)
        # URL mit Text
        result = re.sub(
            r'\[url=(.*?)\](.*?)\[/url\]', r'[\2](\1)',
            result, flags=re.IGNORECASE | re.DOTALL,
        )
        # URL ohne Text
        result = re.sub(r'\[url\](.*?)\[/url\]', r'\1', result, flags=re.IGNORECASE | re.DOTALL)
        # Code
        result = re.sub(r'\[code\](.*?)\[/code\]', r'`\1`', result, flags=re.IGNORECASE | re.DOTALL)
        # Farbe entfernen (nur Text beibehalten)
        result = re.sub(r'\[color=[^\]]*\](.*?)\[/color\]', r'\1', result, flags=re.IGNORECASE | re.DOTALL)
        # Groesse entfernen (nur Text beibehalten)
        result = re.sub(r'\[size=[^\]]*\](.*?)\[/size\]', r'\1', result, flags=re.IGNORECASE | re.DOTALL)

        return result.strip()

    @staticmethod
    def _markdown_to_bbcode(text: str) -> str:
        """Konvertiert Markdown-Formatierung in BBCode-Tags.

        Unterstuetzte Formatierungen:
            **...**   -> [b]...[/b]
            *...*     -> [i]...[/i]
            __...__   -> [u]...[/u]
            ~~...~~   -> [s]...[/s]
            [Y](X)    -> [url=X]Y[/url]
            `...`     -> [code]...[/code]

        Args:
            text: Markdown-formatierter Text.

        Returns:
            BBCode-formatierter Text.
        """
        if not text:
            return ""

        result = text

        # Code (vor Bold/Italic, da ` Sonderbehandlung braucht)
        result = re.sub(r'`([^`]+)`', r'[code]\1[/code]', result)
        # Bold (** vor * verarbeiten)
        result = re.sub(r'\*\*(.+?)\*\*', r'[b]\1[/b]', result, flags=re.DOTALL)
        # Underline (__ vor _ verarbeiten)
        result = re.sub(r'__(.+?)__', r'[u]\1[/u]', result, flags=re.DOTALL)
        # Italic (einzelnes *)
        result = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'[i]\1[/i]', result, flags=re.DOTALL)
        # Strikethrough
        result = re.sub(r'~~(.+?)~~', r'[s]\1[/s]', result, flags=re.DOTALL)
        # Links [Text](URL)
        result = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'[url=\2]\1[/url]', result)

        return result.strip()

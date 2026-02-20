"""
TeamSpeak Cog fuer den Admin Bot (Phase 12b-12d, Feature F16).

Stellt alle /ts Commands bereit:
  /ts status        — Server-Info + Online-Spieler
  /ts players       — Detaillierte Spielerliste
  /ts kick          — Client vom Server kicken
  /ts ban           — Client bannen
  /ts unban         — Ban aufheben
  /ts banlist       — Aktive Bans anzeigen
  /ts poke          — Client anpoken
  /ts move          — Client in anderen Channel verschieben
  /ts bridge start  — Chat-Bridge starten
  /ts bridge stop   — Chat-Bridge stoppen
  /ts bridge status — Chat-Bridge-Status anzeigen

WICHTIG: TeamSpeak ist optional. Wenn TS_ENABLED=false oder TS_HOST
nicht gesetzt ist, werden alle Commands mit einer entsprechenden
Meldung beantwortet.
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from utils.logger import get_logger
from utils.config import get_env
from utils.permissions import admin_only

logger = get_logger("cogs.teamspeak")

# TS-Konfiguration aus Umgebungsvariablen
TS_ENABLED = get_env("TS_ENABLED", "false", cast=bool)
TS_HOST = get_env("TS_HOST", "")
TS_PORT = get_env("TS_PORT", 10011, cast=int)
TS_USER = get_env("TS_USER", "serveradmin")
TS_PASSWORD = get_env("TS_PASSWORD", "")
TS_SERVER_ID = get_env("TS_SERVER_ID", 1, cast=int)

# Pruefen ob TeamSpeak konfiguriert und aktiviert ist
TS_CONFIGURED = bool(TS_ENABLED and TS_HOST)

# Hinweistext wenn TS nicht konfiguriert
TS_NOT_CONFIGURED_MSG = (
    "TeamSpeak ist nicht konfiguriert.\n"
    "Setze `TS_ENABLED=true` und `TS_HOST` in der `.env`-Datei."
)


def _format_idle_time(ms: int) -> str:
    """Formatiert Idle-Zeit von Millisekunden in lesbares Format.

    Args:
        ms: Idle-Zeit in Millisekunden.

    Returns:
        Formatierter String (z.B. '5m 30s' oder '2h 15m').
    """
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m {seconds % 60}s"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m"


def _format_uptime(seconds: int) -> str:
    """Formatiert Uptime-Sekunden in lesbares Format.

    Args:
        seconds: Uptime in Sekunden.

    Returns:
        Formatierter String (z.B. '3d 5h 20m').
    """
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h {minutes % 60}m"
    days = hours // 24
    return f"{days}d {hours % 24}h {minutes % 60}m"


class TeamSpeakCog(commands.Cog):
    """TeamSpeak-Integration fuer den Admin Bot.

    Ermoeglicht Server-Verwaltung und Chat-Bridge ueber Discord-Commands.
    Alle Commands sind admin-only. Falls TeamSpeak nicht konfiguriert ist,
    wird eine entsprechende Fehlermeldung angezeigt.
    """

    # ==================================================================
    # Command-Gruppen
    # ==================================================================

    ts = app_commands.Group(
        name="ts", description="TeamSpeak Server-Verwaltung"
    )
    bridge_grp = app_commands.Group(
        name="bridge", parent=ts, description="TeamSpeak Chat-Bridge"
    )

    # ==================================================================
    # Init
    # ==================================================================

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ts_client = None
        self.channel_manager = None
        self.chat_bridge = None
        self._ts_available = False

        if TS_CONFIGURED:
            self._init_ts_modules()

    def _init_ts_modules(self) -> None:
        """Initialisiert die TeamSpeak-Module (TSClient, ChannelManager).

        Wird nur aufgerufen wenn TS konfiguriert ist. Importfehler werden
        abgefangen falls die ts3-Bibliothek nicht installiert ist.
        """
        try:
            from modules.teamspeak.ts_client import TSClient, TS3_AVAILABLE

            if not TS3_AVAILABLE:
                logger.warning(
                    "ts3-Paket nicht installiert. TeamSpeak-Commands deaktiviert."
                )
                return

            self.ts_client = TSClient(
                host=TS_HOST,
                port=TS_PORT,
                user=TS_USER,
                password=TS_PASSWORD,
                server_id=TS_SERVER_ID,
            )
            self._ts_available = True
            logger.info(
                f"TeamSpeak-Client initialisiert: {TS_HOST}:{TS_PORT} "
                f"(VServer {TS_SERVER_ID})"
            )

            # Channel-Manager initialisieren
            from modules.teamspeak.channel_manager import TSChannelManager
            self.channel_manager = TSChannelManager(self.ts_client)
            logger.info("TeamSpeak Channel-Manager initialisiert.")

        except ImportError as e:
            logger.error(f"TeamSpeak-Module konnten nicht geladen werden: {e}")
        except Exception as e:
            logger.error(f"TeamSpeak-Initialisierung fehlgeschlagen: {e}")

    async def cog_load(self) -> None:
        """Wird beim Laden des Cogs aufgerufen. Stellt TS-Verbindung her."""
        if self._ts_available and self.ts_client:
            success = await self.ts_client.connect()
            if success:
                logger.info("TeamSpeak-Verbindung beim Cog-Load hergestellt.")
            else:
                logger.warning(
                    "TeamSpeak-Verbindung beim Cog-Load fehlgeschlagen. "
                    "Reconnect wird bei Bedarf versucht."
                )

    async def cog_unload(self) -> None:
        """Wird beim Entladen des Cogs aufgerufen. Raeumt auf."""
        # Chat-Bridge stoppen
        if self.chat_bridge:
            await self.chat_bridge.stop()

        # TS-Verbindung trennen
        if self.ts_client:
            await self.ts_client.disconnect()

        logger.info("TeamSpeak-Cog entladen.")

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    async def _check_ts(self, interaction: discord.Interaction) -> bool:
        """Prueft ob TeamSpeak verfuegbar ist. Sendet Fehlermeldung wenn nicht.

        Args:
            interaction: Die Discord-Interaction.

        Returns:
            True wenn TS verfuegbar, False wenn nicht.
        """
        if not TS_CONFIGURED:
            await interaction.response.send_message(
                TS_NOT_CONFIGURED_MSG, ephemeral=True
            )
            return False

        if not self._ts_available or not self.ts_client:
            await interaction.response.send_message(
                "TeamSpeak-Client nicht verfuegbar. "
                "Ist das `ts3`-Paket installiert?",
                ephemeral=True,
            )
            return False

        return True

    async def _check_ts_deferred(self, interaction: discord.Interaction) -> bool:
        """Prueft ob TeamSpeak verfuegbar ist (fuer bereits deferred Interactions).

        Args:
            interaction: Die Discord-Interaction (bereits deferred).

        Returns:
            True wenn TS verfuegbar, False wenn nicht.
        """
        if not TS_CONFIGURED:
            await interaction.followup.send(TS_NOT_CONFIGURED_MSG, ephemeral=True)
            return False

        if not self._ts_available or not self.ts_client:
            await interaction.followup.send(
                "TeamSpeak-Client nicht verfuegbar. "
                "Ist das `ts3`-Paket installiert?",
                ephemeral=True,
            )
            return False

        return True

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  /ts status — Server-Info + Online-Spieler                   ║
    # ╚════════════════════════════════════════════════════════════════╝

    @ts.command(name="status", description="TeamSpeak Server-Status anzeigen")
    @admin_only()
    async def ts_status(self, interaction: discord.Interaction):
        """Zeigt Server-Info und die Anzahl der Online-Spieler."""
        if not await self._check_ts(interaction):
            return

        await interaction.response.defer()

        server_info = await self.ts_client.get_server_info()
        if not server_info:
            await interaction.followup.send(
                "TeamSpeak-Server nicht erreichbar.", ephemeral=True
            )
            return

        clients = await self.ts_client.get_client_list()

        embed = discord.Embed(
            title=f"TeamSpeak: {server_info.get('name', 'Unbekannt')}",
            color=0x2980b9,
        )

        # Server-Informationen
        embed.add_field(
            name="Version",
            value=server_info.get("version", "Unbekannt"),
            inline=True,
        )
        embed.add_field(
            name="Plattform",
            value=server_info.get("platform", "Unbekannt"),
            inline=True,
        )
        embed.add_field(
            name="Uptime",
            value=_format_uptime(server_info.get("uptime", 0)),
            inline=True,
        )

        # Client-Zahlen
        online = server_info.get("clients_online", 0)
        # ServerQuery-Clients abziehen (sie werden in clients_online mitgezaehlt)
        real_clients = len(clients) if clients else max(0, online - 1)
        max_clients = server_info.get("max_clients", 0)

        embed.add_field(
            name="Spieler",
            value=f"{real_clients}/{max_clients}",
            inline=True,
        )
        embed.add_field(
            name="Channels",
            value=str(server_info.get("channels_online", 0)),
            inline=True,
        )

        # Verbindungsstatus
        embed.add_field(
            name="Verbindung",
            value="Verbunden" if self.ts_client.is_connected() else "Getrennt",
            inline=True,
        )

        # Spieler-Liste (Kurzform)
        if clients:
            player_names = [c.get("client_nickname", "?") for c in clients[:15]]
            player_text = ", ".join(player_names)
            if len(clients) > 15:
                player_text += f" ... (+{len(clients) - 15})"
            embed.add_field(
                name="Online",
                value=player_text,
                inline=False,
            )

        # Bridge-Status
        if self.chat_bridge and self.chat_bridge.is_running:
            embed.set_footer(text="Chat-Bridge: Aktiv")
        else:
            embed.set_footer(text="Chat-Bridge: Inaktiv")

        await interaction.followup.send(embed=embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  /ts players — Detaillierte Spielerliste                     ║
    # ╚════════════════════════════════════════════════════════════════╝

    @ts.command(name="players", description="Detaillierte Spielerliste anzeigen")
    @admin_only()
    async def ts_players(self, interaction: discord.Interaction):
        """Zeigt alle verbundenen Clients mit Channel und Idle-Zeit."""
        if not await self._check_ts(interaction):
            return

        await interaction.response.defer()

        clients = await self.ts_client.get_client_list()
        channels = await self.ts_client.get_channel_list()

        if not clients:
            embed = discord.Embed(
                title="TeamSpeak Spieler",
                description="Keine Spieler online.",
                color=0x2980b9,
            )
            await interaction.followup.send(embed=embed)
            return

        # Channel-ID -> Name Mapping
        channel_map = {}
        if channels:
            channel_map = {ch["cid"]: ch["channel_name"] for ch in channels}

        embed = discord.Embed(
            title=f"TeamSpeak Spieler ({len(clients)})",
            color=0x2980b9,
        )

        entries = []
        for client in clients[:25]:
            name = client.get("client_nickname", "Unbekannt")
            clid = client.get("clid", 0)
            channel_name = channel_map.get(client.get("cid", 0), "Unbekannt")
            idle = _format_idle_time(client.get("client_idle_time", 0))

            entries.append(
                f"`{clid:>4}` **{name}**\n"
                f"       Channel: {channel_name} | Idle: {idle}"
            )

        embed.description = "\n".join(entries)

        if len(clients) > 25:
            embed.set_footer(
                text=f"Zeige 25 von {len(clients)} Spielern."
            )

        await interaction.followup.send(embed=embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  /ts kick — Client kicken                                    ║
    # ╚════════════════════════════════════════════════════════════════╝

    @ts.command(name="kick", description="Client vom TeamSpeak kicken")
    @app_commands.describe(
        client_id="Client-ID des Spielers",
        reason="Grund fuer den Kick",
    )
    @admin_only()
    async def ts_kick(
        self,
        interaction: discord.Interaction,
        client_id: int,
        reason: str = "",
    ):
        """Kickt einen Client vom TeamSpeak-Server."""
        if not await self._check_ts(interaction):
            return

        await interaction.response.defer()

        success = await self.ts_client.kick_client(client_id, reason)

        if success:
            embed = discord.Embed(
                title="Client gekickt",
                description=(
                    f"Client-ID: `{client_id}`\n"
                    f"Grund: {reason or 'Kein Grund angegeben'}"
                ),
                color=0xe67e22,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(
                f"TS-Kick: Client {client_id} von {interaction.user} "
                f"(Grund: {reason or 'keiner'})"
            )
        else:
            await interaction.followup.send(
                f"Kick fehlgeschlagen. Client-ID `{client_id}` existiert "
                f"moeglicherweise nicht.",
                ephemeral=True,
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  /ts ban — Client bannen                                     ║
    # ╚════════════════════════════════════════════════════════════════╝

    @ts.command(name="ban", description="Client auf dem TeamSpeak bannen")
    @app_commands.describe(
        client_id="Client-ID des Spielers",
        dauer_sekunden="Ban-Dauer in Sekunden (0 = permanent)",
        reason="Grund fuer den Ban",
    )
    @admin_only()
    async def ts_ban(
        self,
        interaction: discord.Interaction,
        client_id: int,
        dauer_sekunden: int = 0,
        reason: str = "",
    ):
        """Bannt einen Client vom TeamSpeak-Server."""
        if not await self._check_ts(interaction):
            return

        await interaction.response.defer()

        success = await self.ts_client.ban_client(
            client_id, dauer_sekunden, reason
        )

        if success:
            dauer_text = (
                f"{dauer_sekunden} Sekunden"
                if dauer_sekunden > 0
                else "Permanent"
            )
            embed = discord.Embed(
                title="Client gebannt",
                description=(
                    f"Client-ID: `{client_id}`\n"
                    f"Dauer: {dauer_text}\n"
                    f"Grund: {reason or 'Kein Grund angegeben'}"
                ),
                color=0xe74c3c,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(
                f"TS-Ban: Client {client_id} von {interaction.user} "
                f"(Dauer: {dauer_text}, Grund: {reason or 'keiner'})"
            )
        else:
            await interaction.followup.send(
                f"Ban fehlgeschlagen. Client-ID `{client_id}` existiert "
                f"moeglicherweise nicht.",
                ephemeral=True,
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  /ts unban — Ban aufheben                                    ║
    # ╚════════════════════════════════════════════════════════════════╝

    @ts.command(name="unban", description="TeamSpeak-Ban aufheben")
    @app_commands.describe(ban_id="Ban-ID (siehe /ts banlist)")
    @admin_only()
    async def ts_unban(self, interaction: discord.Interaction, ban_id: int):
        """Hebt einen Ban anhand der Ban-ID auf."""
        if not await self._check_ts(interaction):
            return

        await interaction.response.defer()

        success = await self.ts_client.unban(ban_id)

        if success:
            embed = discord.Embed(
                title="Ban aufgehoben",
                description=f"Ban-ID: `{ban_id}`",
                color=0x2ecc71,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            logger.info(f"TS-Unban: Ban {ban_id} von {interaction.user}")
        else:
            await interaction.followup.send(
                f"Unban fehlgeschlagen. Ban-ID `{ban_id}` existiert "
                f"moeglicherweise nicht.",
                ephemeral=True,
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  /ts banlist — Aktive Bans anzeigen                          ║
    # ╚════════════════════════════════════════════════════════════════╝

    @ts.command(name="banlist", description="TeamSpeak-Banliste anzeigen")
    @admin_only()
    async def ts_banlist(self, interaction: discord.Interaction):
        """Zeigt alle aktiven Bans auf dem TeamSpeak-Server."""
        if not await self._check_ts(interaction):
            return

        await interaction.response.defer()

        bans = await self.ts_client.get_ban_list()

        embed = discord.Embed(
            title=f"TeamSpeak Bans ({len(bans)})",
            color=0xe74c3c,
        )

        if not bans:
            embed.description = "Keine aktiven Bans."
        else:
            entries = []
            for ban in bans[:20]:
                ban_id = ban.get("banid", "?")
                name = ban.get("name", "") or ban.get("uid", "") or ban.get("ip", "Unbekannt")
                reason = ban.get("reason", "Kein Grund")
                invoker = ban.get("invoker_name", "?")
                duration = ban.get("duration", 0)

                dauer_text = "Permanent" if duration == 0 else f"{duration}s"

                entries.append(
                    f"`{ban_id:>4}` **{name}**\n"
                    f"       Grund: {reason}\n"
                    f"       Dauer: {dauer_text} | von: {invoker}"
                )

            embed.description = "\n".join(entries)

            if len(bans) > 20:
                embed.set_footer(
                    text=f"Zeige 20 von {len(bans)} Bans."
                )

        await interaction.followup.send(embed=embed)

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  /ts poke — Client anpoken                                   ║
    # ╚════════════════════════════════════════════════════════════════╝

    @ts.command(name="poke", description="Client im TeamSpeak anpoken")
    @app_commands.describe(
        client_id="Client-ID des Spielers",
        nachricht="Poke-Nachricht (max. 100 Zeichen)",
    )
    @admin_only()
    async def ts_poke(
        self,
        interaction: discord.Interaction,
        client_id: int,
        nachricht: str,
    ):
        """Sendet eine Poke-Nachricht an einen Client."""
        if not await self._check_ts(interaction):
            return

        await interaction.response.defer()

        success = await self.ts_client.poke_client(client_id, nachricht)

        if success:
            embed = discord.Embed(
                title="Client gepoked",
                description=(
                    f"Client-ID: `{client_id}`\n"
                    f"Nachricht: {nachricht[:100]}"
                ),
                color=0x3498db,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(
                f"Poke fehlgeschlagen. Client-ID `{client_id}` existiert "
                f"moeglicherweise nicht.",
                ephemeral=True,
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  /ts move — Client verschieben                               ║
    # ╚════════════════════════════════════════════════════════════════╝

    @ts.command(name="move", description="Client in anderen Channel verschieben")
    @app_commands.describe(
        client_id="Client-ID des Spielers",
        channel_id="Ziel-Channel-ID",
    )
    @admin_only()
    async def ts_move(
        self,
        interaction: discord.Interaction,
        client_id: int,
        channel_id: int,
    ):
        """Verschiebt einen Client in einen anderen TeamSpeak-Channel."""
        if not await self._check_ts(interaction):
            return

        await interaction.response.defer()

        success = await self.ts_client.move_client(client_id, channel_id)

        if success:
            embed = discord.Embed(
                title="Client verschoben",
                description=(
                    f"Client-ID: `{client_id}`\n"
                    f"Neuer Channel: `{channel_id}`"
                ),
                color=0x3498db,
            )
            embed.set_footer(text=f"von {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(
                f"Verschieben fehlgeschlagen. Prüfe ob Client-ID `{client_id}` "
                f"und Channel-ID `{channel_id}` existieren.",
                ephemeral=True,
            )

    # ╔════════════════════════════════════════════════════════════════╗
    # ║  /ts bridge start|stop|status — Chat-Bridge                  ║
    # ╚════════════════════════════════════════════════════════════════╝

    @bridge_grp.command(name="start", description="Chat-Bridge starten")
    @app_commands.describe(
        ts_channel_id="TeamSpeak Channel-ID (0 = Server-Chat)",
    )
    @admin_only()
    async def bridge_start(
        self,
        interaction: discord.Interaction,
        ts_channel_id: int = 0,
    ):
        """Startet die bidirektionale Chat-Bridge zwischen TS und Discord."""
        if not await self._check_ts(interaction):
            return

        await interaction.response.defer()

        # Pruefen ob Bridge bereits laeuft
        if self.chat_bridge and self.chat_bridge.is_running:
            await interaction.followup.send(
                "Chat-Bridge laeuft bereits. Stoppe sie zuerst mit `/ts bridge stop`.",
                ephemeral=True,
            )
            return

        try:
            from modules.teamspeak.chat_bridge import TSChatBridge

            self.chat_bridge = TSChatBridge(
                ts_client=self.ts_client,
                bot=self.bot,
                discord_channel_id=interaction.channel_id,
                ts_channel_id=ts_channel_id,
            )

            success = await self.chat_bridge.start()

            if success:
                embed = discord.Embed(
                    title="Chat-Bridge gestartet",
                    description=(
                        f"Discord-Channel: <#{interaction.channel_id}>\n"
                        f"TS-Channel-ID: `{ts_channel_id}` "
                        f"({'Server-Chat' if ts_channel_id == 0 else 'Channel'})"
                    ),
                    color=0x2ecc71,
                )
                embed.set_footer(text=f"Gestartet von {interaction.user.display_name}")
                await interaction.followup.send(embed=embed)
                logger.info(
                    f"Chat-Bridge gestartet von {interaction.user} "
                    f"(Discord: {interaction.channel_id}, TS: {ts_channel_id})"
                )
            else:
                await interaction.followup.send(
                    "Chat-Bridge konnte nicht gestartet werden. "
                    "Prüfe die TS-Verbindung.",
                    ephemeral=True,
                )

        except ImportError as e:
            await interaction.followup.send(
                f"Chat-Bridge-Modul konnte nicht geladen werden: {e}",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(f"Chat-Bridge-Start fehlgeschlagen: {e}")
            await interaction.followup.send(
                f"Fehler beim Starten der Chat-Bridge: {e}",
                ephemeral=True,
            )

    @bridge_grp.command(name="stop", description="Chat-Bridge stoppen")
    @admin_only()
    async def bridge_stop(self, interaction: discord.Interaction):
        """Stoppt die Chat-Bridge."""
        if not await self._check_ts(interaction):
            return

        if not self.chat_bridge or not self.chat_bridge.is_running:
            await interaction.response.send_message(
                "Chat-Bridge laeuft nicht.", ephemeral=True
            )
            return

        await self.chat_bridge.stop()
        embed = discord.Embed(
            title="Chat-Bridge gestoppt",
            color=0xe74c3c,
        )
        embed.set_footer(text=f"Gestoppt von {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
        logger.info(f"Chat-Bridge gestoppt von {interaction.user}")

    @bridge_grp.command(name="status", description="Chat-Bridge-Status anzeigen")
    @admin_only()
    async def bridge_status(self, interaction: discord.Interaction):
        """Zeigt den aktuellen Status der Chat-Bridge."""
        if not await self._check_ts(interaction):
            return

        is_active = (
            self.chat_bridge is not None and self.chat_bridge.is_running
        )

        embed = discord.Embed(
            title="Chat-Bridge Status",
            color=0x2ecc71 if is_active else 0x95a5a6,
        )

        if is_active:
            embed.add_field(
                name="Status", value="Aktiv", inline=True
            )
            embed.add_field(
                name="Discord-Channel",
                value=f"<#{self.chat_bridge.discord_channel_id}>",
                inline=True,
            )
            ts_ch = self.chat_bridge.ts_channel_id
            embed.add_field(
                name="TS-Channel",
                value=f"`{ts_ch}`" if ts_ch > 0 else "Server-Chat",
                inline=True,
            )
        else:
            embed.add_field(
                name="Status", value="Inaktiv", inline=True
            )
            embed.description = (
                "Starte die Bridge mit `/ts bridge start`."
            )

        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # Discord-Listener fuer Chat-Bridge (Discord -> TS)
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Leitet Discord-Nachrichten an TeamSpeak weiter (wenn Bridge aktiv).

        Ignoriert Bot-Nachrichten und Nachrichten aus anderen Channels.
        """
        # Nur weiterleiten wenn Bridge aktiv
        if not self.chat_bridge or not self.chat_bridge.is_running:
            return

        # Bot-Nachrichten ignorieren
        if message.author.bot:
            return

        # Nur Nachrichten aus dem Bridge-Channel
        if message.channel.id != self.chat_bridge.discord_channel_id:
            return

        # Slash-Commands ignorieren (beginnen oft mit /)
        if message.content.startswith("/"):
            return

        # An TS weiterleiten
        content = message.content
        if not content and message.attachments:
            content = f"[Anhang: {message.attachments[0].filename}]"
        if not content:
            return

        await self.chat_bridge.send_to_ts(
            author=message.author.display_name,
            message=content,
        )

    # ------------------------------------------------------------------
    # Error Handler
    # ------------------------------------------------------------------

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Behandelt Fehler fuer alle Commands in diesem Cog."""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung fuer diesen Befehl.", ephemeral=True
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
        except Exception:
            pass


async def setup(bot):
    """Registriert den TeamSpeak-Cog beim Bot."""
    await bot.add_cog(TeamSpeakCog(bot))

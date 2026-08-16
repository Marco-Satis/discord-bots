"""
Timeout Cog — Serverübergreifendes temporaeres Ban-System

Command-Struktur:
  /timeout <spieler> <dauer> [grund] [server]   (Timeout setzen - Admin)
  /timeout status [spieler]                      (Restzeit abfragen - Alle)
  /timeout aufheben <spieler>                    (Timeout aufheben - Admin)
  /timeout list                                  (Alle aktiven Timeouts - Admin)
  /timeout history <spieler>                     (Timeout-Historie - Admin)

Neues Verhalten gegenüber v3.1.0:
- Multi-Server Ban: SAT + alle konfigurierten MC-Server gleichzeitig
- IP-Ban via UFW + RCON-Ban (MC)
- Persistenz in data/timeouts.json
- Background-Task prueft alle 60s auf abgelaufene Timeouts
- DM-Benachrichtigung an Spieler
- Spieler können eigene Restzeit abfragen
"""

import re as _re
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.timeout_manager import TimeoutManager
from utils import get_logger, admin_only, DATA_DIR, is_admin
from modules.server_registry import alle as alle_server
from utils.embeds import (
    COLOR_ERROR,
    COLOR_SUCCESS,
    COLOR_WARNING,
    error_embed,
    info_embed,
    success_embed,
    warning_embed,
)
from utils.loop_guard import guard


def _sanitize_input(text: str, max_length: int = 100) -> str:
    """Sanitisiert User-Input für Server-Befehle. Erlaubt nur sichere Zeichen."""
    sanitized = _re.sub(r'[^\w\s\-]', '', text, flags=_re.UNICODE)
    return sanitized[:max_length].strip()


logger = get_logger("cogs.timeout")

# Server-Optionen für den Autocomplete — aus der ENV (modules.server_registry),
# damit ein stillgelegter Server nicht in der Auswahl stehen bleibt.
_SERVER_CHOICES = [app_commands.Choice(name="Alle Server", value="alle")] + [
    app_commands.Choice(name=srv.label, value=srv.kurz_id) for srv in alle_server()
]


class TimeoutCog(commands.Cog):
    """Serverübergreifendes Timeout-System mit Multi-Server-Ban"""

    timeout_grp = app_commands.Group(
        name="timeout",
        description="Temporaeres Ban-System (Multi-Server)",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sat_server = bot.sat_server
        self.sat_api = bot.sat_api
        self.timeout_mgr = TimeoutManager(DATA_DIR / "timeouts.json")

    async def cog_load(self) -> None:
        """Background-Task starten wenn Cog geladen wird"""
        guard(self.check_expired_timeouts, name="check_expired_timeouts")
        self.check_expired_timeouts.start()
        logger.info("Timeout-Background-Task gestartet")

    async def cog_unload(self) -> None:
        """Background-Task stoppen wenn Cog entladen wird"""
        self.check_expired_timeouts.cancel()

    # ==================================================================
    # Background-Task: Abgelaufene Timeouts aufheben
    # ==================================================================

    @tasks.loop(seconds=60)
    async def check_expired_timeouts(self) -> None:
        """Prueft alle 60 Sekunden ob Timeouts abgelaufen sind"""
        expired = self.timeout_mgr.get_expired()
        if not expired:
            return

        for entry in expired:
            discord_id = entry.get("discord_id")
            player_name = entry.get("player_name", "?")
            servers = entry.get("servers", [])

            if not discord_id:
                continue

            # Je Eintrag kapseln: ein Nutzer mit geschlossenen DMs wirft
            # discord.Forbidden, und eine unbehandelte Ausnahme beendet die
            # Schleife dauerhaft — danach laufen ALLE Timeouts nie mehr ab,
            # ohne dass jemand etwas merkt.
            try:
                logger.info(
                    f"Timeout abgelaufen: {player_name} (Discord: {discord_id})"
                )

                # Timeout in Manager als abgelaufen markieren
                was_active, _ = await self.timeout_mgr.lift_timeout(discord_id)
                if not was_active:
                    continue  # Bereits von anderem Pfad aufgehoben

                # Server-Bans aufheben
                safe_name = _sanitize_input(player_name)
                await self._unban_from_servers(safe_name, servers)

                # Discord-Timeout wird automatisch von Discord aufgehoben

                # Spieler per DM benachrichtigen
                await self._notify_player_dm(
                    discord_id,
                    "Dein Timeout ist abgelaufen",
                    "Du kannst wieder auf allen Servern spielen.",
                    color=COLOR_SUCCESS,
                )
            except Exception as e:  # noqa: BLE001 — Schleife muss weiterlaufen
                logger.error(
                    f"Timeout-Ablauf fuer {player_name} fehlgeschlagen: {e}",
                    exc_info=True,
                )

    @check_expired_timeouts.before_loop
    async def before_check_expired(self) -> None:
        """Warten bis Bot bereit ist"""
        await self.bot.wait_until_ready()

    # ==================================================================
    # /timeout <spieler> <dauer> [grund] [server]
    # ==================================================================

    @timeout_grp.command(
        name="setzen",
        description="Spieler temporaer von Servern sperren",
    )
    @app_commands.describe(
        spieler="Name des Spielers (Game + Discord)",
        dauer_min="Timeout-Dauer in Minuten",
        grund="Grund für den Timeout",
        server="Auf welchen Servern sperren (Standard: alle)",
    )
    @app_commands.choices(server=_SERVER_CHOICES)
    @admin_only()
    async def timeout_setzen(
        self,
        interaction: discord.Interaction,
        spieler: str,
        dauer_min: int,
        grund: str = "Kein Grund angegeben",
        server: app_commands.Choice[str] = None,
    ) -> None:
        await interaction.response.defer()

        # Validierung
        if dauer_min < 1:
            await interaction.followup.send(
                "Dauer muss mindestens 1 Minute sein.", ephemeral=True
            )
            return
        if dauer_min > 40320:  # Discord max: 28 Tage
            await interaction.followup.send(
                "Max. 28 Tage (40320 Minuten).", ephemeral=True
            )
            return

        safe_spieler = _sanitize_input(spieler)
        safe_grund = _sanitize_input(grund, max_length=200)
        target_servers = self._resolve_servers(server)

        # Discord-Member finden
        discord_member = self._find_member(interaction.guild, spieler)
        discord_id = discord_member.id if discord_member else 0

        # Timeout im Manager speichern
        success, message, action_results = await self.timeout_mgr.set_timeout(
            player_name=safe_spieler,
            discord_id=discord_id,
            duration_minutes=dauer_min,
            reason=grund,
            set_by=interaction.user.display_name,
            set_by_id=interaction.user.id,
            servers=target_servers,
            bot=self.bot,
        )

        if not success:
            await interaction.followup.send(message, ephemeral=True)
            return

        # Aktionen ausführen auf den Servern (safe_grund für RCON)
        ban_results = await self._ban_on_servers(
            safe_spieler, target_servers, dauer_min, safe_grund
        )
        action_results.extend(ban_results)

        # Discord-Timeout setzen
        discord_result = await self._set_discord_timeout(
            discord_member, dauer_min, grund, interaction.user
        )
        action_results.append(discord_result)

        # Spieler per DM benachrichtigen
        if discord_member:
            if dauer_min >= 60:
                display_dur = f"{dauer_min // 60}h {dauer_min % 60}m"
            else:
                display_dur = f"{dauer_min} Minuten"
            await self._notify_player_dm(
                discord_member.id,
                f"Du wurdest für {display_dur} von allen Servern gesperrt",
                f"**Grund:** {grund}\n\nDein Zugang wird automatisch wiederhergestellt.",
                color=COLOR_ERROR,
            )

        # Response-Embed erstellen
        embed = self._build_timeout_embed(
            safe_spieler, dauer_min, grund, action_results, interaction.user
        )
        await interaction.followup.send(embed=embed)

        logger.info(
            f"Timeout: {safe_spieler} ({dauer_min}min) by {interaction.user} "
            f"- Server: {target_servers} - Grund: {grund}"
        )

    # ==================================================================
    # /timeout status [spieler]
    # ==================================================================

    @timeout_grp.command(
        name="status",
        description="Timeout-Status abfragen (eigenen oder fremden)",
    )
    @app_commands.describe(
        spieler="Discord-User (leer = eigenen Status anzeigen)"
    )
    async def timeout_status(
        self,
        interaction: discord.Interaction,
        spieler: Optional[discord.Member] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        # Admins können andere abfragen, normale User nur sich selbst
        if spieler and spieler.id != interaction.user.id:
            if not is_admin(interaction):
                await interaction.followup.send(
                    "Du kannst nur deinen eigenen Timeout-Status abfragen.",
                    ephemeral=True,
                )
                return
            target = spieler
        else:
            target = interaction.user

        timeout_data = self.timeout_mgr.get_active_timeout(target.id)

        if not timeout_data:
            await interaction.followup.send(
                f"{'Du hast' if target == interaction.user else f'**{target.display_name}** hat'} "
                f"keinen aktiven Timeout.",
                ephemeral=True,
            )
            return

        # Timeout-Info Embed
        embed = error_embed(
            title="Aktiver Timeout",
        )

        embed.add_field(
            name="Spieler",
            value=target.display_name,
            inline=True,
        )

        # Restzeit berechnen
        expires_str = timeout_data.get("expires_at", "")
        expires_at = None
        try:
            expires_at = datetime.fromisoformat(expires_str)
            remaining = expires_at - datetime.now()
            if remaining.total_seconds() > 0:
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    restzeit = f"{hours}h {minutes}m"
                else:
                    restzeit = f"{minutes}m {seconds}s"
            else:
                restzeit = "Laeuft gleich ab..."
        except (ValueError, TypeError):
            restzeit = "?"

        embed.add_field(name="Restzeit", value=restzeit, inline=True)
        embed.add_field(
            name="Grund",
            value=timeout_data.get("reason", "Kein Grund"),
            inline=False,
        )

        set_at = timeout_data.get("set_at", "?")
        try:
            set_dt = datetime.fromisoformat(set_at)
            set_at = set_dt.strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            pass

        embed.add_field(name="Gesperrt seit", value=set_at, inline=True)

        if expires_at:
            expires_display = expires_at.strftime("%d.%m.%Y %H:%M")
        else:
            expires_display = expires_str
        embed.add_field(name="Gesperrt bis", value=expires_display, inline=True)

        servers = timeout_data.get("servers", [])
        if servers:
            # Alte Eintraege koennen Server nennen, die es nicht mehr gibt —
            # dann bleibt die Kurzform stehen, statt die Anzeige zu verlieren.
            server_labels = {srv.kurz_id: srv.label for srv in alle_server()}
            server_names = [server_labels.get(s, s) for s in servers]
            embed.add_field(
                name="Betroffene Server",
                value=", ".join(server_names),
                inline=False,
            )

        embed.set_footer(text=f"Gesetzt von {timeout_data.get('set_by', '?')}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # /timeout aufheben <spieler>
    # ==================================================================

    @timeout_grp.command(
        name="aufheben",
        description="Timeout eines Spielers vorzeitig aufheben",
    )
    @app_commands.describe(spieler="Discord-User dessen Timeout aufgehoben werden soll")
    @admin_only()
    async def timeout_aufheben(
        self,
        interaction: discord.Interaction,
        spieler: discord.Member,
    ) -> None:
        await interaction.response.defer()

        was_active, timeout_data = await self.timeout_mgr.lift_timeout(
            spieler.id,
            ended_by=interaction.user.display_name,
        )

        if not was_active:
            await interaction.followup.send(
                f"**{spieler.display_name}** hat keinen aktiven Timeout.",
                ephemeral=True,
            )
            return

        player_name = timeout_data.get("player_name", spieler.display_name)
        safe_name = _sanitize_input(player_name)
        servers = timeout_data.get("servers", [])

        # Server-Bans aufheben
        await self._unban_from_servers(safe_name, servers)

        # Discord-Timeout aufheben
        try:
            await spieler.timeout(None, reason="Timeout vorzeitig aufgehoben")
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Discord-Timeout aufheben fehlgeschlagen: {e}")

        # Spieler per DM benachrichtigen
        await self._notify_player_dm(
            spieler.id,
            "Dein Timeout wurde vorzeitig aufgehoben",
            "Du kannst wieder auf allen Servern spielen.",
            color=COLOR_SUCCESS,
        )

        embed = success_embed(
            title="Timeout aufgehoben",
            description=(
                f"Timeout für **{player_name}** wurde vorzeitig aufgehoben.\n"
                f"Alle Server-Bans wurden entfernt."
            ),
        )
        embed.set_footer(text=f"von {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

        logger.info(
            f"Timeout aufgehoben: {player_name} von {interaction.user.display_name}"
        )

    # ==================================================================
    # /timeout list
    # ==================================================================

    @timeout_grp.command(
        name="list",
        description="Alle aktiven Timeouts anzeigen",
    )
    @admin_only()
    async def timeout_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        active = self.timeout_mgr.get_all_active()

        if not active:
            await interaction.followup.send(
                "Keine aktiven Timeouts.", ephemeral=True
            )
            return

        embed = warning_embed(
            title=f"Aktive Timeouts ({len(active)})",
        )

        for entry in active:
            player_name = entry.get("player_name", "?")
            reason = entry.get("reason", "?")
            servers = ", ".join(entry.get("servers", []))

            # Restzeit berechnen
            expires_str = entry.get("expires_at", "")
            try:
                expires_at = datetime.fromisoformat(expires_str)
                remaining = expires_at - datetime.now()
                if remaining.total_seconds() > 0:
                    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                    mins, _ = divmod(remainder, 60)
                    restzeit = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
                else:
                    restzeit = "abgelaufen"
            except (ValueError, TypeError):
                restzeit = "?"

            embed.add_field(
                name=player_name,
                value=(
                    f"**Restzeit:** {restzeit}\n"
                    f"**Grund:** {reason}\n"
                    f"**Server:** {servers}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # /timeout history <spieler>
    # ==================================================================

    @timeout_grp.command(
        name="history",
        description="Timeout-Historie eines Spielers anzeigen",
    )
    @app_commands.describe(spieler="Discord-User (leer = alle)")
    @admin_only()
    async def timeout_history(
        self,
        interaction: discord.Interaction,
        spieler: Optional[discord.Member] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        discord_id = spieler.id if spieler else None
        history = self.timeout_mgr.get_history(discord_id)

        if not history:
            name = spieler.display_name if spieler else "alle"
            await interaction.followup.send(
                f"Keine Timeout-Historie für {name}.", ephemeral=True
            )
            return

        title = (
            f"Timeout-Historie — {spieler.display_name}"
            if spieler
            else "Timeout-Historie (letzte 20)"
        )
        embed = info_embed(title=title)

        for entry in history[:10]:  # Max 10 im Embed
            player_name = entry.get("player_name", "?")
            reason = entry.get("reason", "?")
            ended_reason = entry.get("ended_reason", "?")
            ended_by = entry.get("ended_by")

            # Datum formatieren
            set_at = entry.get("set_at", "?")
            try:
                set_dt = datetime.fromisoformat(set_at)
                set_at = set_dt.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                pass

            ended_str = ended_reason
            if ended_reason == "manual" and ended_by:
                ended_str = f"Aufgehoben von {ended_by}"
            elif ended_reason == "expired":
                ended_str = "Automatisch abgelaufen"

            embed.add_field(
                name=f"{player_name} — {set_at}",
                value=(
                    f"**Grund:** {reason}\n"
                    f"**Ende:** {ended_str}"
                ),
                inline=False,
            )

        if len(history) > 10:
            embed.set_footer(text=f"... und {len(history) - 10} weitere")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # Hilfsmethoden
    # ==================================================================

    def _find_member(
        self, guild: Optional[discord.Guild], name: str
    ) -> Optional[discord.Member]:
        """Discord-Member anhand von Display-Name oder Username finden"""
        if not guild:
            return None
        name_lower = name.lower()
        for member in guild.members:
            if (
                member.display_name.lower() == name_lower
                or member.name.lower() == name_lower
            ):
                return member
        return None

    def _resolve_servers(
        self, server_choice: Optional[app_commands.Choice[str]] = None
    ) -> list[str]:
        """Server-Auswahl in Liste von Server-IDs aufloesen"""
        if not server_choice or server_choice.value == "alle":
            # Alle verfügbaren Server.
            #
            # Audit-Befund C-9 (2026-08-16): hier stand fest `["sat"]`, und
            # "sat" loest immer auf die *erste* Instanz auf (`_sat_sid`). Ein
            # Timeout auf "alle Server" traf damit SAT-1 und jeden Minecraft —
            # den zweiten Satisfactory nie. Der Bann-Pfad selbst kann ihn
            # laengst (siehe `_ban_on_servers`), nur bot ihn niemand an: der
            # gebannte Spieler haette einfach dort weitergespielt.
            servers = list(self._sat_kennungen())
            mc_servers = getattr(self.bot, "mc_servers", {})
            for sid in mc_servers:
                servers.append(sid.lower())
            return servers
        return [server_choice.value]

    async def _ban_on_servers(
        self,
        player_name: str,
        servers: list[str],
        dauer_min: int,
        grund: str,
    ) -> list[str]:
        """Spieler auf allen angegebenen Servern bannen. Gibt Status-Zeilen zurück."""
        results: list[str] = []

        for srv in servers:
            # Die zweite Satisfactory-Instanz heisst "sat_second". Ohne den
            # Praefix-Vergleich landete sie im Minecraft-Zweig, der nach einem
            # MC-Server namens SAT_SECOND suchte, keinen fand — und still nichts
            # tat. Ein Timeout, der nicht wirkt, ohne dass es jemand merkt.
            if srv == "sat" or srv.startswith("sat_"):
                sid = self._sat_sid(srv)
                result = await self._ban_sat(player_name, grund, sid)
                results.append(f"{self._sat_label(sid)}: {result}")
            else:
                result = await self._ban_mc(player_name, srv, grund)
                results.append(f"MC-{srv.upper()}: {result}")

        return results

    def _sat_kennungen(self) -> list[str]:
        """Timeout-Kennungen aller Satisfactory-Instanzen.

        Die erste heisst historisch "sat" (so steht sie in Auswahllisten und
        in der Datenbank), jede weitere "sat_<id>". `_sat_sid` liest genau
        dieses Schema wieder ein.
        """
        servers = list(getattr(self.bot, "sat_servers", {}) or {})
        if not servers:
            return ["sat"]
        return ["sat"] + [f"sat_{sid.lower()}" for sid in servers[1:]]

    def _sat_sid(self, kurz_id: str) -> str:
        """Kennung des Timeout-Systems ("sat", "sat_second") zur Instanz-ID."""
        if kurz_id == "sat":
            servers = getattr(self.bot, "sat_servers", {}) or {}
            return next(iter(servers), "MAIN")
        return kurz_id[4:].upper()

    def _sat_label(self, sid: str) -> str:
        """Anzeigename der Instanz fuer die Rueckmeldung."""
        srv = (getattr(self.bot, "sat_servers", {}) or {}).get(sid)
        return getattr(srv, "display_name", None) or f"SAT-{sid}"

    def _sat_tracker(self, sid: str):
        """IP-Tracker der Instanz — sonst bannt man auf dem falschen Server."""
        alle = getattr(self.bot, "sat_ip_trackers", {}) or {}
        return alle.get(sid) or getattr(self.bot, "player_ip_tracker", None)

    def _sat_api_von(self, sid: str):
        """API-Client der Instanz."""
        alle = getattr(self.bot, "sat_apis", {}) or {}
        return alle.get(sid) or self.sat_api

    async def _ban_sat(self, player_name: str, grund: str,
                       sid: Optional[str] = None) -> str:
        """SAT-Server: IP-Ban via iptables REJECT (sofortige Trennung)"""
        sid = sid or self._sat_sid("sat")
        ip_banned = False
        sat_tracker = self._sat_tracker(sid)
        if sat_tracker:
            success, msg = await sat_tracker.ban_player(
                player_name, grund, "Timeout-System",
                api=self._sat_api_von(sid)  # SaveGame vor Ban
            )
            ip_banned = success

        if ip_banned:
            return "IP gesperrt + Verbindung getrennt (iptables REJECT)"
        else:
            return "Ban fehlgeschlagen (keine IP bekannt?)"

    async def _ban_mc(self, player_name: str, server_id: str, grund: str) -> str:
        """MC-Server: RCON ban + IP-Ban via UFW"""
        mc_servers = getattr(self.bot, "mc_servers", {})
        srv = mc_servers.get(server_id.upper()) or mc_servers.get(server_id)
        if not srv:
            return f"Server nicht gefunden"

        # RCON ban
        rcon_ok = False
        try:
            result = await srv.rcon_command(f"ban {player_name} {grund}")
            if result is not None:
                rcon_ok = True
        except Exception as e:
            logger.warning(f"MC RCON ban fehlgeschlagen ({server_id}): {e}")

        # IP-Ban via MC IP-Tracker
        ip_banned = False
        mc_trackers = getattr(self.bot, "mc_ip_trackers", {})
        tracker = mc_trackers.get(server_id.upper()) or mc_trackers.get(server_id)
        if tracker:
            success, msg = await tracker.ban_player(
                player_name, grund, "Timeout-System"
            )
            ip_banned = success

        if rcon_ok and ip_banned:
            return "RCON-Ban + IP gesperrt"
        elif rcon_ok:
            return "RCON-Ban (IP nicht bekannt)"
        elif ip_banned:
            return "IP gesperrt (RCON fehlgeschlagen)"
        else:
            return "Ban fehlgeschlagen"

    async def _unban_from_servers(
        self,
        player_name: str,
        servers: list[str],
    ) -> None:
        """Spieler auf allen angegebenen Servern entbannen"""
        for srv in servers:
            if srv == "sat" or srv.startswith("sat_"):
                await self._unban_sat(player_name, self._sat_sid(srv))
            else:
                await self._unban_mc(player_name, srv)

    async def _unban_sat(self, player_name: str,
                         sid: Optional[str] = None) -> None:
        """SAT IP-Ban aufheben"""
        sid = sid or self._sat_sid("sat")
        sat_tracker = self._sat_tracker(sid)
        if sat_tracker:
            try:
                await sat_tracker.unban_player(player_name)
            except Exception as e:
                logger.warning(f"SAT-Unban fehlgeschlagen: {e}")

    async def _unban_mc(self, player_name: str, server_id: str) -> None:
        """MC-Server: RCON pardon + IP-Unban"""
        mc_servers = getattr(self.bot, "mc_servers", {})
        srv = mc_servers.get(server_id.upper()) or mc_servers.get(server_id)
        if srv:
            try:
                await srv.rcon_command(f"pardon {player_name}")
            except Exception as e:
                logger.warning(f"MC RCON pardon fehlgeschlagen ({server_id}): {e}")

        mc_trackers = getattr(self.bot, "mc_ip_trackers", {})
        tracker = mc_trackers.get(server_id.upper()) or mc_trackers.get(server_id)
        if tracker:
            try:
                await tracker.unban_player(player_name)
            except Exception as e:
                logger.warning(f"MC IP-Unban fehlgeschlagen ({server_id}): {e}")

    async def _set_discord_timeout(
        self,
        member: Optional[discord.Member],
        dauer_min: int,
        grund: str,
        admin: discord.Member,
    ) -> str:
        """Discord-Timeout setzen. Gibt Status-String zurück."""
        if not member:
            return "Discord-Timeout: User nicht gefunden"

        try:
            duration = timedelta(minutes=dauer_min)
            await member.timeout(
                duration,
                reason=f"Timeout von {admin.display_name}: {grund}",
            )
            return f"Discord-Timeout: {member.display_name} gesperrt"
        except discord.Forbidden:
            return "Discord-Timeout: Keine Berechtigung"
        except discord.HTTPException as e:
            return f"Discord-Timeout: {e}"

    async def _notify_player_dm(
        self,
        discord_id: int,
        title: str,
        description: str,
        color: int = 0xe74c3c,
    ) -> None:
        """Spieler per DM benachrichtigen (fehlertolerant)"""
        if not discord_id:
            return

        try:
            user = self.bot.get_user(discord_id) or await self.bot.fetch_user(
                discord_id
            )
            embed = discord.Embed(
                title=title,
                description=description,
                color=color,
                timestamp=datetime.now(),
            )
            await user.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            logger.debug(f"DM an {discord_id} konnte nicht gesendet werden")
        except Exception as e:
            logger.warning(f"DM-Benachrichtigung fehlgeschlagen: {e}")

    def _build_timeout_embed(
        self,
        player_name: str,
        dauer_min: int,
        grund: str,
        action_results: list[str],
        admin: discord.Member,
    ) -> discord.Embed:
        """Response-Embed für /timeout setzen erstellen"""
        embed = warning_embed(
            title="Timeout gesetzt",
        )
        embed.add_field(name="Spieler", value=player_name, inline=True)

        if dauer_min >= 60:
            display_dur = f"{dauer_min // 60}h {dauer_min % 60}m"
        else:
            display_dur = f"{dauer_min} Min"
        embed.add_field(name="Dauer", value=display_dur, inline=True)
        embed.add_field(name="Grund", value=grund, inline=False)

        # Aktionen anzeigen
        if action_results:
            embed.add_field(
                name="Aktionen",
                value="\n".join(f"• {r}" for r in action_results),
                inline=False,
            )

        # Farbe je nach Erfolg
        success_count = sum(
            1 for r in action_results
            if any(w in r for w in ["Gekickt", "gesperrt", "Ban", "gesetzt"])
        )
        if success_count == len(action_results):
            embed.color = COLOR_SUCCESS  # Gruen
        elif success_count > 0:
            embed.color = COLOR_WARNING  # Orange
        else:
            embed.color = COLOR_ERROR  # Rot

        embed.set_footer(text=f"von {admin.display_name}")
        return embed

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung für alle Commands in dieser Cog"""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung für diesen Befehl.", ephemeral=True
                )
            return
        cmd_name = interaction.command.name if interaction.command else "unknown"
        logger.error(f"Command error in {cmd_name}: {error}", exc_info=True)
        try:
            msg = f"Ein Fehler ist aufgetreten: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")


async def setup(bot):
    await bot.add_cog(TimeoutCog(bot))

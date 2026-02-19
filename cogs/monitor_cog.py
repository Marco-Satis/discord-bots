"""
Monitor Cog - Monitoring slash commands
Commands: /performance, /dashboard, /stats, /report
"""

import time
import json
import psutil
import discord
from pathlib import Path
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
from typing import Optional

from utils.logger import get_logger
from utils.formatting import format_uptime, format_bytes, progress_bar
from utils.permissions import is_admin, is_owner
from utils.config import PROJECT_ROOT

logger = get_logger("monitor_cog")


class MonitorCog(commands.Cog):
    """Monitoring commands for the Monitor Bot"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def health_checker(self):
        return getattr(self.bot, "health_checker", None)

    @property
    def perf_monitor(self):
        return getattr(self.bot, "perf_monitor", None)

    @property
    def player_tracker(self):
        return getattr(self.bot, "player_tracker", None)

    @property
    def update_checker(self):
        return getattr(self.bot, "update_checker", None)

    @property
    def sat_server(self):
        return getattr(self.bot, "sat_server", None)

    # ------------------------------------------------------------------
    # /performance
    # ------------------------------------------------------------------

    @app_commands.command(name="performance",
                          description="System-Performance anzeigen")
    async def performance_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()

        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        boot = psutil.boot_time()
        sys_uptime = int(time.time() - boot)

        embed = discord.Embed(
            title="📊 System-Performance",
            description=f"Netcup RS 4000 G12 | Uptime: {format_uptime(sys_uptime)}",
            color=0x5865F2,
            timestamp=datetime.now(),
        )

        embed.add_field(
            name="CPU",
            value=f"{progress_bar(cpu, 100)}\n{cpu}% ({psutil.cpu_count()} Kerne)",
            inline=False,
        )
        embed.add_field(
            name="RAM",
            value=(
                f"{progress_bar(mem.percent, 100)}\n"
                f"{format_bytes(mem.used)} / {format_bytes(mem.total)}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Disk",
            value=(
                f"{progress_bar(disk.percent, 100)}\n"
                f"{format_bytes(disk.used)} / {format_bytes(disk.total)}"
            ),
            inline=False,
        )

        # Per-process info
        if self.sat_server:
            sat_status = await self.sat_server.get_status()
            if sat_status.get("running"):
                embed.add_field(
                    name="Satisfactory Process",
                    value=(
                        f"CPU: {sat_status['cpu_percent']:.1f}% | "
                        f"RAM: {sat_status['memory_mb']} MB | "
                        f"PID: {sat_status['pid']}"
                    ),
                    inline=False,
                )

        # Averages from perf monitor
        if self.perf_monitor:
            avg = self.perf_monitor.get_averages(minutes=60)
            if avg["samples"] > 0:
                embed.add_field(
                    name="Durchschnitt (1h)",
                    value=(
                        f"CPU: {avg['cpu']:.1f}% | "
                        f"RAM: {avg['ram']:.1f}% | "
                        f"Disk: {avg['disk']:.1f}% "
                        f"({avg['samples']} Samples)"
                    ),
                    inline=False,
                )

        # Health checker info
        if self.health_checker:
            summary = self.health_checker.get_summary()
            health_text = (
                f"Status: {summary['state']} | "
                f"Spieler: {summary['players']} | "
                f"Tick: {summary['tick_rate']:.1f} | "
                f"Crashes: {summary['crashes_total']} "
                f"({summary['crashes_last_hour']} letzte Stunde)"
            )
            embed.add_field(name="Server Health", value=health_text, inline=False)

        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # /dashboard
    # ------------------------------------------------------------------

    @app_commands.command(name="dashboard",
                          description="Dashboard-Embed manuell aktualisieren")
    @app_commands.check(is_admin)
    async def dashboard_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Trigger the status embed update task
        update_func = getattr(self.bot, "update_status_embed_now", None)
        if update_func:
            await update_func()
            await interaction.followup.send("✅ Dashboard aktualisiert!", ephemeral=True)
        else:
            await interaction.followup.send(
                "⚠️ Dashboard-Update Funktion nicht verfuegbar.", ephemeral=True
            )

    # ------------------------------------------------------------------
    # /stats [spieler]
    # ------------------------------------------------------------------

    @app_commands.command(name="stats",
                          description="Spieler-Statistiken anzeigen")
    @app_commands.describe(spieler="Name des Spielers (leer = alle)")
    async def stats_cmd(self, interaction: discord.Interaction,
                        spieler: Optional[str] = None):
        await interaction.response.defer()

        if not self.player_tracker:
            await interaction.followup.send("❌ Player Tracker nicht verfuegbar.")
            return

        if spieler:
            # Single player stats
            stats = self.player_tracker.get_player_stats(spieler)
            if not stats:
                # Try case-insensitive search
                for name in self.player_tracker.players:
                    if name.lower() == spieler.lower():
                        stats = self.player_tracker.get_player_stats(name)
                        break

            if not stats:
                await interaction.followup.send(
                    f"❌ Keine Daten fuer Spieler **{spieler}** gefunden."
                )
                return

            online_str = "🟢 Online" if stats["is_online"] else "🔴 Offline"
            if stats["is_online"] and stats["current_session_minutes"] > 0:
                online_str += f" (seit {stats['current_session_minutes']} Min)"

            embed = discord.Embed(
                title=f"📊 Statistiken: {stats['name']}",
                color=0x00ff00 if stats["is_online"] else 0x888888,
                timestamp=datetime.now(),
            )
            embed.add_field(name="Status", value=online_str, inline=True)
            embed.add_field(
                name="Gesamt-Spielzeit",
                value=f"{stats['total_playtime_hours']}h",
                inline=True,
            )
            embed.add_field(
                name="Sessions",
                value=str(stats["session_count"]),
                inline=True,
            )
            embed.add_field(
                name="Durchschnittl. Session",
                value=f"{stats['avg_session_minutes']} Min",
                inline=True,
            )
            embed.add_field(
                name="Erste Session",
                value=stats["first_seen"][:10],
                inline=True,
            )
            embed.add_field(
                name="Zuletzt gesehen",
                value=stats["last_seen"][:10],
                inline=True,
            )

            # Recent sessions
            if stats["recent_sessions"]:
                recent = []
                for s in reversed(stats["recent_sessions"][-5:]):
                    join_dt = s.get("join", "")[:16].replace("T", " ")
                    dur = s.get("duration_minutes", 0)
                    recent.append(f"`{join_dt}` — {dur} Min")
                embed.add_field(
                    name="Letzte Sessions",
                    value="\n".join(recent),
                    inline=False,
                )

            await interaction.followup.send(embed=embed)
        else:
            # All players overview
            all_stats = self.player_tracker.get_all_stats()
            if not all_stats:
                await interaction.followup.send("📊 Noch keine Spielerdaten vorhanden.")
                return

            embed = discord.Embed(
                title="📊 Spieler-Uebersicht",
                description=f"{len(all_stats)} Spieler erfasst",
                color=0x5865F2,
                timestamp=datetime.now(),
            )

            for i, stats in enumerate(all_stats[:15]):
                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "▫️"
                status = "🟢" if stats["is_online"] else ""
                embed.add_field(
                    name=f"{medal} {stats['name']} {status}",
                    value=(
                        f"Spielzeit: {stats['total_playtime_hours']}h | "
                        f"Sessions: {stats['session_count']}"
                    ),
                    inline=False,
                )

            if len(all_stats) > 15:
                embed.set_footer(text=f"... und {len(all_stats) - 15} weitere Spieler")

            await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # /report
    # ------------------------------------------------------------------

    @app_commands.command(name="report",
                          description="Wochenbericht anzeigen")
    @app_commands.describe(zeitraum="Zeitraum in Tagen (Standard: 7)")
    async def report_cmd(self, interaction: discord.Interaction,
                         zeitraum: Optional[int] = 7):
        await interaction.response.defer()

        days = min(max(zeitraum or 7, 1), 90)
        title_prefix = "Wochen" if days <= 7 else "Monats" if days <= 31 else ""
        now = datetime.now()
        start_date = (now - timedelta(days=days)).strftime("%d.%m.%Y")
        end_date = now.strftime("%d.%m.%Y")

        embed = discord.Embed(
            title=f"📋 {title_prefix}bericht",
            description=f"{start_date} — {end_date} ({days} Tage)",
            color=0x5865F2,
            timestamp=now,
        )

        # --- Uptime ---
        stats_tracker = getattr(self.bot, "stats_tracker", None)
        if stats_tracker:
            uptime_pct = stats_tracker.get_uptime_percent(days)
            total_checks = stats_tracker.get_total_checks(days)
            uptime_bar = self._make_bar(uptime_pct)
            embed.add_field(
                name="🟢 Server Uptime",
                value=(
                    f"{uptime_bar} **{uptime_pct}%**\n"
                    f"{total_checks} Checks im Zeitraum"
                ),
                inline=True,
            )

            # Peak players
            peak_players = stats_tracker.get_peak_players(days)
            embed.add_field(
                name="👥 Peak Spieler",
                value=f"**{peak_players}** gleichzeitig",
                inline=True,
            )
        else:
            embed.add_field(
                name="🟢 Uptime",
                value="Stats Tracker nicht verfuegbar",
                inline=True,
            )

        # --- Crashes ---
        if stats_tracker:
            crashes = stats_tracker.get_crashes(days)
            if crashes:
                crash_lines = []
                for c in crashes[-5:]:
                    ts = c["ts"][:16].replace("T", " ")
                    crash_lines.append(f"`{ts}` — Crash #{c['number']}")
                if len(crashes) > 5:
                    crash_lines.append(f"... und {len(crashes) - 5} weitere")
                embed.add_field(
                    name=f"🚨 Crashes ({len(crashes)})",
                    value="\n".join(crash_lines),
                    inline=False,
                )
            else:
                embed.add_field(
                    name="🚨 Crashes",
                    value="✅ Keine Crashes im Zeitraum",
                    inline=True,
                )

        # --- Spieler-Aktivitaet ---
        if self.player_tracker:
            cutoff_iso = (now - timedelta(days=days)).isoformat()
            active = {}
            total_sessions = 0
            total_time = 0
            for name, record in self.player_tracker.players.items():
                wt = ws = 0
                for s in record.sessions:
                    if s.get("join", "") >= cutoff_iso:
                        ws += 1
                        wt += s.get("duration_minutes", 0)
                if ws > 0:
                    active[name] = {"sessions": ws, "playtime_hours": round(wt / 60, 1)}
                    total_sessions += ws
                    total_time += wt

            # Vorwoche zum Vergleich
            prev_cutoff = (now - timedelta(days=days * 2)).isoformat()
            curr_cutoff = (now - timedelta(days=days)).isoformat()
            prev_time = 0
            prev_sessions = 0
            for name, record in self.player_tracker.players.items():
                for s in record.sessions:
                    join = s.get("join", "")
                    if prev_cutoff <= join < curr_cutoff:
                        prev_sessions += 1
                        prev_time += s.get("duration_minutes", 0)

            # Trend-Vergleich
            curr_hours = round(total_time / 60, 1)
            prev_hours = round(prev_time / 60, 1)
            if prev_hours > 0:
                change = round(((curr_hours - prev_hours) / prev_hours) * 100, 0)
                trend = f"📈 +{change:.0f}%" if change > 0 else f"📉 {change:.0f}%" if change < 0 else "➡️ gleich"
                trend_text = f"\nVorperiode: {prev_hours}h ({trend})"
            else:
                trend_text = ""

            embed.add_field(
                name="🎮 Spieler-Aktivitaet",
                value=(
                    f"Aktive Spieler: **{len(active)}**\n"
                    f"Sessions: **{total_sessions}**\n"
                    f"Spielzeit: **{curr_hours}h**{trend_text}"
                ),
                inline=True,
            )

            # Top players
            if active:
                sorted_players = sorted(active.items(), key=lambda x: x[1]["playtime_hours"], reverse=True)
                top = []
                for i, (name, data) in enumerate(sorted_players[:5], 1):
                    medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                    top.append(f"{medal} **{name}** — {data['playtime_hours']}h ({data['sessions']} Sess.)")
                embed.add_field(
                    name="🏆 Top Spieler",
                    value="\n".join(top),
                    inline=True,
                )
        else:
            embed.add_field(name="Spieler", value="Tracker nicht verfuegbar", inline=True)

        # --- Savegame-Wachstum ---
        if stats_tracker:
            growth = stats_tracker.get_savegame_growth(days)
            if growth:
                growth_icon = "📈" if growth["growth_mb"] > 0 else "📉" if growth["growth_mb"] < 0 else "➡️"
                embed.add_field(
                    name="💾 Savegame-Wachstum",
                    value=(
                        f"{growth['start_mb']} MB → {growth['end_mb']} MB\n"
                        f"{growth_icon} {growth['growth_mb']:+.1f} MB ({growth['growth_percent']:+.1f}%)"
                    ),
                    inline=True,
                )

        # --- Performance ---
        if self.perf_monitor:
            avg = self.perf_monitor.get_averages(minutes=days * 1440)
            peak = self.perf_monitor.get_peak(minutes=days * 1440)
            if avg.get("samples", 0) > 0:
                embed.add_field(
                    name="📊 Performance (Avg/Peak)",
                    value=(
                        f"CPU: {avg['cpu']:.1f}% / {peak['cpu']:.1f}%\n"
                        f"RAM: {avg['ram']:.1f}% / {peak['ram']:.1f}%\n"
                        f"Disk: {avg['disk']:.1f}% / {peak['disk']:.1f}%\n"
                        f"SAT: {avg['process_cpu']:.1f}% CPU, {avg['process_ram']} MB RAM"
                    ),
                    inline=True,
                )

        # --- Health ---
        if self.health_checker:
            summary = self.health_checker.get_summary()
            embed.add_field(
                name="🩺 Server Health",
                value=(
                    f"Status: {summary.get('state', '?')}\n"
                    f"Tick Rate: {summary.get('tick_rate', 0):.1f}\n"
                    f"Crashes gesamt: {summary.get('crashes_total', 0)}"
                ),
                inline=True,
            )

        # --- System & Backup ---
        extra_parts = []
        if self.update_checker:
            if self.update_checker.update_available:
                extra_parts.append("⚠️ Update verfuegbar!")
            elif self.update_checker.installed_buildid:
                extra_parts.append(f"Build: {self.update_checker.installed_buildid}")

        bm = getattr(self.bot, "backup_manager", None)
        if bm:
            backups = bm.list_backups()
            extra_parts.append(f"Backups: {len(backups)} lokal")

        od = getattr(self.bot, "onedrive_backup", None)
        if od and od.enabled:
            extra_parts.append(f"☁️ OneDrive: Aktiv")

        cb = getattr(self.bot, "config_backup", None)
        if cb and cb.last_backup:
            extra_parts.append(f"🔧 Config-Backup: {cb.last_backup.strftime('%d.%m. %H:%M')}")

        if extra_parts:
            embed.add_field(
                name="🔧 System",
                value="\n".join(extra_parts),
                inline=True,
            )

        embed.set_footer(text=f"Zeitraum: {days} Tage | /report [tage] fuer anderen Zeitraum")
        await interaction.followup.send(embed=embed)

    @staticmethod
    def _make_bar(percent: float, length: int = 10) -> str:
        """Create a simple text progress bar"""
        filled = int(percent / 100 * length)
        return "█" * filled + "░" * (length - filled)

    # ------------------------------------------------------------------
    # /mon world - World statistics from savegame
    # ------------------------------------------------------------------

    mon_grp = app_commands.Group(
        name="mon", description="Detaillierte Monitoring-Befehle"
    )

    @mon_grp.command(name="world", description="Detaillierte Welt-Statistiken anzeigen")
    async def mon_world_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()

        analyzer = getattr(self.bot, "savegame_analyzer", None)
        if not analyzer:
            await interaction.followup.send("❌ Savegame-Analyzer nicht verfügbar.")
            return

        try:
            world_stats = await analyzer.get_stats()
            if not world_stats or not world_stats.available:
                await interaction.followup.send(
                    "⚠️ Keine Savegame-Daten verfügbar. Server könnte offline sein."
                )
                return

            # Basic info
            embed = discord.Embed(
                title="🌍 Welt-Statistiken",
                description=f"**{world_stats.session_name}**",
                color=0x5865F2,
                timestamp=datetime.now(),
            )

            # Session info
            embed.add_field(
                name="📋 Session-Info",
                value=(
                    f"Spielzeit: {world_stats.play_hours}h\n"
                    f"Speicherung: {world_stats.save_date}\n"
                    f"Dateigröße: {world_stats.save_size}"
                ),
                inline=False,
            )

            # Buildings overview
            if world_stats.total_buildings > 0:
                embed.add_field(
                    name="🏭 Gebäude Übersicht",
                    value=(
                        f"Gesamt: {world_stats.total_buildings:,}\n"
                        f"Fundamente: {world_stats.foundations:,} | "
                        f"Wände: {world_stats.walls:,}\n"
                        f"Speicher: {world_stats.storage:,} | "
                        f"Strompole: {world_stats.power_poles:,}"
                    ),
                    inline=False,
                )

            # Power
            if world_stats.generators > 0 or world_stats.total_power_mw > 0:
                embed.add_field(
                    name="⚡ Stromversorgung",
                    value=(
                        f"Generatoren: {world_stats.generators}\n"
                        f"Kapazität: {world_stats.total_power_mw:,.0f} MW"
                    ),
                    inline=True,
                )

            # Production
            if world_stats.production_machines > 0:
                prod_lines = []
                if world_stats.smelters > 0:
                    prod_lines.append(f"Schmelzer: {world_stats.smelters}")
                if world_stats.foundries > 0:
                    prod_lines.append(f"Gießereien: {world_stats.foundries}")
                if world_stats.constructors > 0:
                    prod_lines.append(f"Konstruktoren: {world_stats.constructors}")
                if world_stats.assemblers > 0:
                    prod_lines.append(f"Montagewerke: {world_stats.assemblers}")
                if world_stats.manufacturers > 0:
                    prod_lines.append(f"Fabrikmaschinen: {world_stats.manufacturers}")
                if world_stats.refineries > 0:
                    prod_lines.append(f"Raffinieren: {world_stats.refineries}")
                if world_stats.blenders > 0:
                    prod_lines.append(f"Mixer: {world_stats.blenders}")

                if prod_lines:
                    embed.add_field(
                        name="🏗️ Produktion",
                        value="\n".join(prod_lines),
                        inline=True,
                    )

            # Conveyor Belts
            if world_stats.conveyor_belts > 0:
                belt_lines = []
                belt_lines.append(f"Gesamt: {world_stats.conveyor_belts:,}")
                if world_stats.belts_mk1 > 0:
                    belt_lines.append(f"MK1: {world_stats.belts_mk1}")
                if world_stats.belts_mk2 > 0:
                    belt_lines.append(f"MK2: {world_stats.belts_mk2}")
                if world_stats.belts_mk3 > 0:
                    belt_lines.append(f"MK3: {world_stats.belts_mk3}")
                if world_stats.belts_mk4 > 0:
                    belt_lines.append(f"MK4: {world_stats.belts_mk4}")
                if world_stats.belts_mk5 > 0:
                    belt_lines.append(f"MK5: {world_stats.belts_mk5}")
                if world_stats.belts_mk6 > 0:
                    belt_lines.append(f"MK6: {world_stats.belts_mk6}")
                if world_stats.lifts_total > 0:
                    belt_lines.append(f"Aufzüge: {world_stats.lifts_total}")

                embed.add_field(
                    name="📦 Förderbänder",
                    value=" | ".join(belt_lines),
                    inline=False,
                )

            # Pipes
            if world_stats.pipes > 0:
                pipe_lines = [f"Gesamt: {world_stats.pipes:,}"]
                if world_stats.pipes_mk1 > 0:
                    pipe_lines.append(f"MK1: {world_stats.pipes_mk1}")
                if world_stats.pipes_mk2 > 0:
                    pipe_lines.append(f"MK2: {world_stats.pipes_mk2}")
                if world_stats.pipeline_pumps > 0:
                    pipe_lines.append(f"Pumpen: {world_stats.pipeline_pumps}")
                if world_stats.valves > 0:
                    pipe_lines.append(f"Ventile: {world_stats.valves}")

                embed.add_field(
                    name="🔧 Rohrleitungen",
                    value=" | ".join(pipe_lines),
                    inline=False,
                )

            # Transport
            if world_stats.trains > 0 or world_stats.vehicles > 0:
                transport_lines = []
                if world_stats.trains > 0:
                    transport_lines.append(f"🚂 Züge: {world_stats.trains}")
                    if world_stats.locomotives > 0:
                        transport_lines.append(f"   Loks: {world_stats.locomotives}")
                    if world_stats.freight_cars > 0:
                        transport_lines.append(f"   Waggons: {world_stats.freight_cars}")
                if world_stats.stations > 0:
                    transport_lines.append(f"🚄 Stationen: {world_stats.stations}")
                if world_stats.vehicles > 0:
                    transport_lines.append(f"🚗 Fahrzeuge: {world_stats.vehicles}")
                    if world_stats.trucks > 0:
                        transport_lines.append(f"   Lastwagen: {world_stats.trucks}")
                    if world_stats.explorers > 0:
                        transport_lines.append(f"   Explorer: {world_stats.explorers}")
                if world_stats.drone_ports > 0:
                    transport_lines.append(f"🚁 Drohnen-Ports: {world_stats.drone_ports}")

                if transport_lines:
                    embed.add_field(
                        name="🚚 Transport & Logistik",
                        value="\n".join(transport_lines),
                        inline=False,
                    )

            # Splitter/Merger
            if world_stats.splitters > 0 or world_stats.mergers > 0:
                sm_lines = []
                if world_stats.splitters > 0:
                    sm_lines.append(f"Splitter: {world_stats.splitters}")
                if world_stats.smart_splitters > 0:
                    sm_lines.append(f"Smart Splitter: {world_stats.smart_splitters}")
                if world_stats.programmable_splitters > 0:
                    sm_lines.append(f"Programierbar: {world_stats.programmable_splitters}")
                if world_stats.mergers > 0:
                    sm_lines.append(f"Merger: {world_stats.mergers}")
                if world_stats.priority_mergers > 0:
                    sm_lines.append(f"Priorität: {world_stats.priority_mergers}")

                if sm_lines:
                    embed.add_field(
                        name="🔀 Splitter & Merger",
                        value=" | ".join(sm_lines),
                        inline=False,
                    )

            # Extractors & Mining
            if world_stats.miners > 0 or world_stats.oil_extractors > 0:
                ext_lines = []
                if world_stats.miners > 0:
                    ext_lines.append(f"Bergleute: {world_stats.miners}")
                if world_stats.oil_extractors > 0:
                    ext_lines.append(f"Öl-Förderer: {world_stats.oil_extractors}")
                if world_stats.water_extractors > 0:
                    ext_lines.append(f"Wasser: {world_stats.water_extractors}")
                if world_stats.resource_well_pressurizers > 0:
                    ext_lines.append(
                        f"Bohrlöcher: {world_stats.resource_well_pressurizers}"
                    )

                if ext_lines:
                    embed.add_field(
                        name="⛏️ Rohstoffgewinnung",
                        value=" | ".join(ext_lines),
                        inline=False,
                    )

            # Generator details
            gen_lines = []
            if world_stats.biomass_burners > 0:
                gen_lines.append(f"Biomasse: {world_stats.biomass_burners}")
            if world_stats.coal_generators > 0:
                gen_lines.append(f"Kohle: {world_stats.coal_generators}")
            if world_stats.fuel_generators > 0:
                gen_lines.append(f"Brennstoff: {world_stats.fuel_generators}")
            if world_stats.geothermal_generators > 0:
                gen_lines.append(f"Geotherm: {world_stats.geothermal_generators}")
            if world_stats.nuclear_plants > 0:
                gen_lines.append(f"Nuklear: {world_stats.nuclear_plants}")
            if world_stats.alien_augmenters > 0:
                gen_lines.append(f"Alien: {world_stats.alien_augmenters}")
            if world_stats.power_storage > 0:
                gen_lines.append(f"Speicher: {world_stats.power_storage}")

            if gen_lines:
                embed.add_field(
                    name="🔋 Generator-Details",
                    value=" | ".join(gen_lines),
                    inline=False,
                )

            # Meta
            if world_stats.last_analyzed:
                embed.set_footer(text=f"Analysiert: {world_stats.last_analyzed}")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"World stats error: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Fehler beim Abrufen der Weltstatistiken: {str(e)[:100]}"
            )

    # ------------------------------------------------------------------
    # /selftest command
    # ------------------------------------------------------------------

    @app_commands.command(name="selftest", description="Alle Bot-Systeme pruefen")
    @app_commands.check(is_admin)
    async def selftest_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()

        # Import SelfTest module
        from modules.monitoring.selftest import SelfTest

        # Create or get SelfTest instance
        selftest = getattr(self.bot, "selftest", None)
        if selftest is None:
            selftest = SelfTest(self.bot)
            self.bot.selftest = selftest

        try:
            # Run all tests
            results = await selftest.run_all()

            # Group results by category
            by_category = {}
            for result in results:
                cat = result.get("category", "Sonstiges")
                if cat not in by_category:
                    by_category[cat] = []
                by_category[cat].append(result)

            # Count status
            total = len(results)
            passed = sum(1 for r in results if r.get("ok", False))
            failed = total - passed

            # Build embed
            embed = discord.Embed(
                title="🔍 System-Selbsttest",
                description=(
                    f"**Ergebnis:** {passed}/{total} bestanden\n"
                    f"✅ {passed} | ❌ {failed}"
                ),
                color=0x00ff00 if failed == 0 else 0xff9900 if failed <= 2 else 0xff0000,
                timestamp=datetime.now(),
            )

            # Add results by category
            for category in sorted(by_category.keys()):
                items = by_category[category]
                lines = []
                for item in items:
                    status = "✅" if item.get("ok", False) else "❌"
                    name = item.get("name", "Unknown")
                    detail = item.get("detail", "")
                    lines.append(f"{status} **{name}**\n   {detail}")

                embed.add_field(
                    name=category,
                    value="\n".join(lines),
                    inline=False,
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Selftest error: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Fehler beim Selbsttest: {str(e)[:100]}"
            )

    # ------------------------------------------------------------------
    # /commandlog - Recent command log
    # ------------------------------------------------------------------

    @app_commands.command(
        name="commandlog", description="Letzte Bot-Commands anzeigen"
    )
    @app_commands.describe(anzahl="Anzahl der Eintraege (Standard: 10)")
    @app_commands.check(is_admin)
    async def commandlog_cmd(
        self, interaction: discord.Interaction, anzahl: int = 10
    ):
        await interaction.response.defer(ephemeral=True)

        # Ensure data directory exists
        data_dir = Path(PROJECT_ROOT / "data")
        data_dir.mkdir(parents=True, exist_ok=True)

        log_file = data_dir / "command_log.json"

        # Check if log file exists
        if not log_file.exists():
            await interaction.followup.send(
                "ℹ️ Noch keine Command-Logs vorhanden.", ephemeral=True
            )
            return

        try:
            # Read command log
            with open(log_file, "r", encoding="utf-8") as f:
                all_logs = json.load(f)

            if not all_logs:
                await interaction.followup.send(
                    "ℹ️ Noch keine Command-Logs vorhanden.", ephemeral=True
                )
                return

            # Get last N entries
            anzahl = min(max(anzahl, 1), 50)  # 1-50 range
            entries = all_logs[-anzahl:] if len(all_logs) > 0 else []

            if not entries:
                await interaction.followup.send(
                    "ℹ️ Noch keine Command-Logs vorhanden.", ephemeral=True
                )
                return

            # Build embed with latest entries
            embed = discord.Embed(
                title=f"📋 Letzte Commands ({len(entries)})",
                color=0x5865F2,
                timestamp=datetime.now(),
            )

            # Show entries in reverse order (newest first)
            lines = []
            for entry in reversed(entries):
                user = entry.get("user", "Unknown")
                command = entry.get("command", "unknown")
                timestamp = entry.get("timestamp", "")

                # Format timestamp
                try:
                    ts = timestamp[:16] if timestamp else "?"
                    ts = ts.replace("T", " ")
                except Exception:
                    ts = "?"

                lines.append(f"`{ts}` • **{user}** → `/{command}`")

            # Add to embed (Discord field limit is ~1024 chars)
            if lines:
                text = "\n".join(lines)
                if len(text) > 1024:
                    # Split into multiple fields if needed
                    chunks = []
                    current = []
                    for line in lines:
                        if len("\n".join(current + [line])) > 1000:
                            if current:
                                chunks.append("\n".join(current))
                            current = [line]
                        else:
                            current.append(line)
                    if current:
                        chunks.append("\n".join(current))

                    for i, chunk in enumerate(chunks):
                        embed.add_field(
                            name="Eintraege" if i == 0 else "­",
                            value=chunk,
                            inline=False,
                        )
                else:
                    embed.add_field(
                        name="Eintraege",
                        value=text,
                        inline=False,
                    )

            embed.set_footer(
                text=f"Gesamt: {len(all_logs)} eingeloggte Commands | Zeige letzte {len(entries)}"
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except json.JSONDecodeError:
            await interaction.followup.send(
                "❌ Command-Log Datei beschädigt.", ephemeral=True
            )
        except Exception as e:
            logger.error(f"Command log error: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Fehler beim Abrufen: {str(e)[:100]}", ephemeral=True
            )

    # ------------------------------------------------------------------
    # /mcstats [server] — MC Server-Statistiken
    # ------------------------------------------------------------------

    @app_commands.command(name="mcstats",
                          description="Minecraft Server-Statistiken anzeigen")
    @app_commands.describe(server="Server-ID (VANILLA/BMC, Standard: alle)")
    async def mcstats_cmd(self, interaction: discord.Interaction,
                          server: Optional[str] = None):
        await interaction.response.defer()

        mc_servers = getattr(self.bot, "mc_servers", {})
        if not mc_servers:
            await interaction.followup.send("❌ Keine Minecraft-Server konfiguriert.")
            return

        # Server filtern
        if server:
            sid = server.upper()
            if sid not in mc_servers:
                await interaction.followup.send(
                    f"❌ Server **{sid}** nicht gefunden. Verfuegbar: {', '.join(mc_servers.keys())}"
                )
                return
            targets = {sid: mc_servers[sid]}
        else:
            targets = mc_servers

        embed = discord.Embed(
            title="⛏️ Minecraft Server-Statistiken",
            color=0x5865F2,
            timestamp=datetime.now(),
        )

        for sid, srv in targets.items():
            try:
                running = await srv.is_running()
                dot = "🟢" if running else "🔴"
                lines = [f"{dot} {'Online' if running else 'Offline'}"]

                if running:
                    try:
                        online, max_p = await srv.get_player_count()
                        lines.append(f"Spieler: {online}/{max_p}")
                    except Exception:
                        pass

                # World-Groesse
                try:
                    world_bytes = await srv.get_world_size()
                    if world_bytes > 0:
                        lines.append(f"Welt: {world_bytes / (1024 * 1024):.1f} MB")
                except Exception:
                    pass

                # Stats-Tracker Daten
                mc_st = getattr(self.bot, "mc_stats_trackers", {}).get(sid)
                if mc_st:
                    uptime_pct = mc_st.get_uptime_percent(7)
                    peak = mc_st.get_peak_players(7)
                    lines.append(f"Uptime (7d): {uptime_pct}%")
                    lines.append(f"Peak Spieler (7d): {peak}")

                    growth = mc_st.get_savegame_growth(7)
                    if growth:
                        lines.append(
                            f"Welt-Wachstum: {growth['growth_mb']:+.1f} MB"
                        )

                # Update-Checker
                mc_uc = getattr(self.bot, "mc_update_checkers", {}).get(sid)
                if mc_uc and mc_uc.update_available:
                    lines.append(f"📦 Paper-Update: Build {mc_uc.current_build} → {mc_uc.latest_build}")

                embed.add_field(
                    name=f"⛏️ {srv.display_name}",
                    value="\n".join(lines),
                    inline=len(targets) > 1,
                )

            except Exception as e:
                logger.debug(f"[{sid}] MC Stats Fehler: {e}")
                embed.add_field(
                    name=f"⛏️ {srv.display_name}",
                    value=f"❌ Fehler: {str(e)[:100]}",
                    inline=True,
                )

        await interaction.followup.send(embed=embed)

    @mcstats_cmd.autocomplete("server")
    async def mcstats_server_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list:
        mc_servers = getattr(self.bot, "mc_servers", {})
        return [
            app_commands.Choice(name=f"{srv.display_name} ({sid})", value=sid)
            for sid, srv in mc_servers.items()
            if current.upper() in sid.upper() or current.lower() in srv.display_name.lower()
        ][:25]

    # ------------------------------------------------------------------
    # /mcreport [zeitraum] [server] — MC Wochenbericht
    # ------------------------------------------------------------------

    @app_commands.command(name="mcreport",
                          description="Minecraft Wochenbericht anzeigen")
    @app_commands.describe(
        zeitraum="Zeitraum in Tagen (Standard: 7)",
        server="Server-ID (VANILLA/BMC, Standard: alle)"
    )
    async def mcreport_cmd(self, interaction: discord.Interaction,
                           zeitraum: Optional[int] = 7,
                           server: Optional[str] = None):
        await interaction.response.defer()

        mc_servers = getattr(self.bot, "mc_servers", {})
        if not mc_servers:
            await interaction.followup.send("❌ Keine Minecraft-Server konfiguriert.")
            return

        days = min(max(zeitraum or 7, 1), 90)
        now = datetime.now()
        start_date = (now - timedelta(days=days)).strftime("%d.%m.%Y")
        end_date = now.strftime("%d.%m.%Y")

        # Server filtern
        if server:
            sid = server.upper()
            if sid not in mc_servers:
                await interaction.followup.send(
                    f"❌ Server **{sid}** nicht gefunden."
                )
                return
            targets = {sid: mc_servers[sid]}
        else:
            targets = mc_servers

        embed = discord.Embed(
            title="📋 Minecraft Bericht",
            description=f"{start_date} — {end_date} ({days} Tage)",
            color=0x5865F2,
            timestamp=now,
        )

        for sid, srv in targets.items():
            mc_st = getattr(self.bot, "mc_stats_trackers", {}).get(sid)
            if not mc_st:
                continue

            lines = []

            # Uptime
            uptime_pct = mc_st.get_uptime_percent(days)
            uptime_bar = self._make_bar(uptime_pct)
            lines.append(f"Uptime: {uptime_bar} **{uptime_pct}%**")

            # Peak Spieler
            peak = mc_st.get_peak_players(days)
            lines.append(f"Peak Spieler: **{peak}**")

            # Crashes
            crashes = mc_st.get_crashes(days)
            if crashes:
                lines.append(f"Crashes: **{len(crashes)}**")
            else:
                lines.append("Crashes: ✅ Keine")

            # World-Wachstum
            growth = mc_st.get_savegame_growth(days)
            if growth:
                icon = "📈" if growth["growth_mb"] > 0 else "📉" if growth["growth_mb"] < 0 else "➡️"
                lines.append(
                    f"Welt: {growth['start_mb']} → {growth['end_mb']} MB "
                    f"({icon} {growth['growth_mb']:+.1f} MB)"
                )

            # Spieler-Aktivitaet
            mc_pt = getattr(self.bot, "mc_player_trackers", {}).get(sid)
            if mc_pt:
                cutoff_iso = (now - timedelta(days=days)).isoformat()
                active_count = 0
                total_time = 0
                for name, record in mc_pt.players.items():
                    ws = 0
                    wt = 0
                    for s in record.sessions:
                        if s.get("join", "") >= cutoff_iso:
                            ws += 1
                            wt += s.get("duration_minutes", 0)
                    if ws > 0:
                        active_count += 1
                        total_time += wt
                lines.append(f"Aktive Spieler: **{active_count}** ({round(total_time / 60, 1)}h)")

            embed.add_field(
                name=f"⛏️ {srv.display_name}",
                value="\n".join(lines) if lines else "Keine Daten",
                inline=len(targets) > 1,
            )

        await interaction.followup.send(embed=embed)

    @mcreport_cmd.autocomplete("server")
    async def mcreport_server_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list:
        mc_servers = getattr(self.bot, "mc_servers", {})
        return [
            app_commands.Choice(name=f"{srv.display_name} ({sid})", value=sid)
            for sid, srv in mc_servers.items()
            if current.upper() in sid.upper() or current.lower() in srv.display_name.lower()
        ][:25]

    # ------------------------------------------------------------------
    # /mccrashlog [nummer] [server] — MC Crash-Replays
    # ------------------------------------------------------------------

    @app_commands.command(name="mccrashlog",
                          description="Minecraft Crash-Replays anzeigen oder herunterladen")
    @app_commands.describe(
        nummer="Crash-Nummer zum Herunterladen (leer = Liste)",
        server="Server-ID (VANILLA/BMC)"
    )
    @app_commands.check(is_admin)
    async def mccrashlog_cmd(self, interaction: discord.Interaction,
                              server: str = "VANILLA",
                              nummer: Optional[int] = None):
        await interaction.response.defer(ephemeral=True)

        mc_crs = getattr(self.bot, "mc_crash_replays", {})
        sid = server.upper()
        cr = mc_crs.get(sid)
        if not cr:
            await interaction.followup.send(
                f"❌ Kein Crash-Replay fuer Server **{sid}** verfuegbar.",
                ephemeral=True,
            )
            return

        replays = cr.list_replays()
        if not replays:
            await interaction.followup.send(
                f"✅ Keine Crash-Replays fuer **{sid}** vorhanden.",
                ephemeral=True,
            )
            return

        if nummer is not None:
            target = None
            for r in replays:
                if f"crash_{nummer:03d}_" in r["filename"]:
                    target = r
                    break

            if not target:
                await interaction.followup.send(
                    f"❌ Kein Replay fuer Crash #{nummer} gefunden.",
                    ephemeral=True,
                )
                return

            file = discord.File(target["path"], filename=target["filename"])
            await interaction.followup.send(
                f"🔍 MC Crash Replay #{nummer} ({sid}):",
                file=file, ephemeral=True,
            )
        else:
            lines = []
            for r in replays[:15]:
                lines.append(
                    f"`{r['filename']}` — {r['size_kb']} KB ({r['date']})"
                )

            embed = discord.Embed(
                title=f"🔍 MC Crash Replays — {sid} ({len(replays)})",
                description="\n".join(lines),
                color=0xff6600,
                timestamp=datetime.now(),
            )
            embed.set_footer(
                text="Verwende /mccrashlog server:X nummer:N zum Herunterladen"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @mccrashlog_cmd.autocomplete("server")
    async def mccrashlog_server_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list:
        mc_crs = getattr(self.bot, "mc_crash_replays", {})
        return [
            app_commands.Choice(name=sid, value=sid)
            for sid in mc_crs
            if current.upper() in sid.upper()
        ][:25]

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # /email test | status
    # ------------------------------------------------------------------

    email_grp = app_commands.Group(
        name="mail", description="Email-Benachrichtigungen verwalten"
    )

    @email_grp.command(name="test", description="Test-Email senden")
    @app_commands.check(is_admin)
    async def email_test(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        notifier = getattr(self.bot, "email_notifier", None)
        if not notifier:
            await interaction.followup.send("❌ Email Notifier nicht konfiguriert.", ephemeral=True)
            return

        if not notifier.enabled:
            await interaction.followup.send(
                "❌ Email deaktiviert. Setze `EMAIL_ENABLED=true` in `.env`.",
                ephemeral=True,
            )
            return

        success = await notifier.send_test()
        if success:
            await interaction.followup.send(
                f"✅ Test-Email gesendet an `{notifier.to_addr}`!",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "❌ Email konnte nicht gesendet werden. Pruefe SMTP-Konfiguration und Logs.",
                ephemeral=True,
            )

    @email_grp.command(name="status", description="Email-Konfiguration anzeigen")
    @app_commands.check(is_admin)
    async def email_status(self, interaction: discord.Interaction):
        notifier = getattr(self.bot, "email_notifier", None)
        if not notifier:
            await interaction.response.send_message(
                "❌ Email Notifier nicht konfiguriert.", ephemeral=True
            )
            return

        status_emoji = "✅" if notifier.enabled else "❌"
        smtp_configured = bool(notifier.smtp_host and notifier.from_addr and notifier.to_addr)
        config_emoji = "✅" if smtp_configured else "⚠️"

        # Recent sends
        recent = []
        for event_type, last_time in notifier._last_sent.items():
            recent.append(f"`{event_type}`: {last_time.strftime('%d.%m. %H:%M')}")

        embed = discord.Embed(
            title="📧 Email-Konfiguration",
            color=0x00ff00 if notifier.enabled else 0xff0000,
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="Status",
            value=f"{status_emoji} {'Aktiv' if notifier.enabled else 'Deaktiviert'}",
            inline=True,
        )
        embed.add_field(
            name="SMTP",
            value=(
                f"{config_emoji} {notifier.smtp_host}:{notifier.smtp_port}\n"
                f"TLS: {'Ja' if notifier.use_tls else 'Nein'}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Adressen",
            value=f"Von: `{notifier.from_addr or 'nicht gesetzt'}`\nAn: `{notifier.to_addr or 'nicht gesetzt'}`",
            inline=False,
        )
        embed.add_field(
            name="Passwort",
            value="✅ Gesetzt" if notifier.password else "❌ Nicht gesetzt",
            inline=True,
        )
        embed.add_field(
            name="Cooldown",
            value=f"{int(notifier._cooldown.total_seconds() / 60)} Min pro Event-Typ",
            inline=True,
        )

        if recent:
            embed.add_field(
                name="Letzte Emails",
                value="\n".join(recent) or "Keine",
                inline=False,
            )

        # What triggers emails
        embed.add_field(
            name="Triggers",
            value=(
                "🚨 Server Crash\n"
                "❌ Auto-Restart fehlgeschlagen\n"
                "⚠️ Performance-Warnung\n"
                "📦 Update verfuegbar"
            ),
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /onedrive status | upload | list
    # ------------------------------------------------------------------

    onedrive_grp = app_commands.Group(
        name="onedrive", description="OneDrive Cloud-Backup Verwaltung"
    )

    @onedrive_grp.command(name="status", description="OneDrive-Backup Status")
    @app_commands.check(is_admin)
    async def onedrive_status(self, interaction: discord.Interaction):
        od = getattr(self.bot, "onedrive_backup", None)
        if not od:
            await interaction.response.send_message(
                "❌ OneDrive Backup nicht konfiguriert.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        info = od.get_status()
        configured = await od.is_configured()

        embed = discord.Embed(
            title="☁️ OneDrive Backup",
            color=0x00ff00 if od.enabled and configured else 0xff0000,
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="Aktiviert",
            value="✅ Ja" if od.enabled else "❌ Nein",
            inline=True,
        )
        embed.add_field(
            name="rclone konfiguriert",
            value="✅ Ja" if configured else "❌ Nein (rclone config ausfuehren)",
            inline=True,
        )
        embed.add_field(
            name="Remote",
            value=f"`{info['remote']}`",
            inline=True,
        )
        embed.add_field(
            name="Max Backups",
            value=str(info["max_backups"]),
            inline=True,
        )
        embed.add_field(
            name="Letzter Upload",
            value=info["last_file"] or "Keiner",
            inline=True,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @onedrive_grp.command(name="upload", description="Letztes Backup zu OneDrive hochladen")
    @app_commands.check(is_admin)
    async def onedrive_upload(self, interaction: discord.Interaction):
        od = getattr(self.bot, "onedrive_backup", None)
        bm = getattr(self.bot, "backup_manager", None)
        if not od or not bm:
            await interaction.response.send_message(
                "❌ OneDrive oder BackupManager nicht konfiguriert.", ephemeral=True
            )
            return

        if not od.enabled:
            await interaction.response.send_message(
                "❌ OneDrive Backup ist deaktiviert.", ephemeral=True
            )
            return

        await interaction.response.defer()

        # Find latest backup
        backups = bm.list_backups()
        if not backups:
            await interaction.followup.send("❌ Keine lokalen Backups vorhanden.")
            return

        latest = backups[0]  # Most recent
        backup_path = latest.get("path", "")
        if not backup_path or not Path(backup_path).exists():
            await interaction.followup.send("❌ Backup-Datei nicht gefunden.")
            return

        await interaction.followup.send(
            f"☁️ Lade `{Path(backup_path).name}` zu OneDrive hoch..."
        )

        success, msg = await od.upload(backup_path)
        if success:
            await od.rotate()
            await interaction.channel.send(f"✅ {msg}")
        else:
            await interaction.channel.send(f"❌ {msg}")

    @onedrive_grp.command(name="list", description="Cloud-Backups auflisten")
    @app_commands.check(is_admin)
    async def onedrive_list(self, interaction: discord.Interaction):
        od = getattr(self.bot, "onedrive_backup", None)
        if not od or not od.enabled:
            await interaction.response.send_message(
                "❌ OneDrive Backup nicht aktiv.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        success, files = await od.list_backups()
        if not success or not files:
            await interaction.followup.send(
                "❌ Keine Cloud-Backups gefunden oder Verbindung fehlgeschlagen.",
                ephemeral=True,
            )
            return

        lines = []
        for f in files[:15]:
            date_str = f["modified"][:10] if f.get("modified") else "?"
            lines.append(f"`{f['name']}` — {f['size_mb']} MB ({date_str})")

        embed = discord.Embed(
            title=f"☁️ Cloud-Backups ({len(files)})",
            description="\n".join(lines),
            color=0x5865F2,
            timestamp=datetime.now(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /configbackup
    # ------------------------------------------------------------------

    @app_commands.command(name="configbackup",
                          description="Server-Config Backup manuell erstellen")
    @app_commands.check(is_admin)
    async def configbackup_cmd(self, interaction: discord.Interaction):
        config_bk = getattr(self.bot, "config_backup", None)
        if not config_bk:
            await interaction.response.send_message(
                "❌ Config Backup nicht konfiguriert.", ephemeral=True
            )
            return

        await interaction.response.defer()

        success, msg = await config_bk.create_and_upload()
        if success:
            await interaction.followup.send(f"✅ Config Backup erstellt: {msg}")
        else:
            await interaction.followup.send(f"❌ {msg}")

    # ------------------------------------------------------------------
    # /crashlog
    # ------------------------------------------------------------------

    @app_commands.command(name="crashlog",
                          description="Crash-Replays anzeigen oder herunterladen")
    @app_commands.describe(nummer="Crash-Nummer zum Herunterladen (leer = Liste)")
    @app_commands.check(is_admin)
    async def crashlog_cmd(self, interaction: discord.Interaction,
                           nummer: Optional[int] = None):
        await interaction.response.defer(ephemeral=True)

        cr = getattr(self.bot, "crash_replay", None)
        if not cr:
            await interaction.followup.send(
                "❌ Crash Replay nicht verfügbar.", ephemeral=True
            )
            return

        replays = cr.list_replays()
        if not replays:
            await interaction.followup.send(
                "✅ Keine Crash-Replays vorhanden.", ephemeral=True
            )
            return

        if nummer is not None:
            # Find specific crash replay
            target = None
            for r in replays:
                if f"crash_{nummer:03d}_" in r["filename"]:
                    target = r
                    break

            if not target:
                await interaction.followup.send(
                    f"❌ Kein Replay für Crash #{nummer} gefunden.",
                    ephemeral=True,
                )
                return

            file = discord.File(target["path"], filename=target["filename"])
            await interaction.followup.send(
                f"🔍 Crash Replay #{nummer}:", file=file, ephemeral=True
            )
        else:
            # List all replays
            lines = []
            for r in replays[:15]:
                lines.append(
                    f"`{r['filename']}` — {r['size_kb']} KB ({r['date']})"
                )

            embed = discord.Embed(
                title=f"🔍 Crash Replays ({len(replays)})",
                description="\n".join(lines),
                color=0xff6600,
                timestamp=datetime.now(),
            )
            embed.set_footer(
                text="Verwende /crashlog nummer:N zum Herunterladen"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /rollback - Rollback management and crash loop reset
    # ------------------------------------------------------------------

    @app_commands.command(name="rollback",
                          description="Rollback-Info anzeigen und optional wiederherstellen")
    @app_commands.check(is_owner)
    async def rollback_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        sg_prot = getattr(self.bot, 'savegame_protection', None)
        if not sg_prot:
            await interaction.followup.send("❌ Savegame-Schutz nicht verfuegbar.", ephemeral=True)
            return

        info = sg_prot.get_rollback_info()

        embed = discord.Embed(
            title="🔄 Rollback-Status",
            color=0xff6600 if info["crash_loop_active"] else 0x5865F2,
            timestamp=datetime.now(),
        )

        embed.add_field(
            name="Crash-Loop",
            value="🔴 AKTIV" if info["crash_loop_active"] else "🟢 Inaktiv",
            inline=True,
        )
        embed.add_field(
            name="Aktuelle Crashes",
            value=str(info["crashes_recent"]),
            inline=True,
        )

        if info["last_known_good"]:
            lkg = info["last_known_good"]
            embed.add_field(
                name="Letztes gutes Save",
                value=f"**{lkg['name']}**\n{lkg['size_mb']} MB | Alter: {lkg['age_minutes']} Min",
                inline=False,
            )
        else:
            embed.add_field(
                name="Letztes gutes Save",
                value="Nicht verfuegbar",
                inline=False,
            )

        # Add reset button if crash loop is active
        view = None
        if info["crash_loop_active"]:
            view = RollbackView(sg_prot, interaction.user.id)
            embed.add_field(
                name="Aktion",
                value="Crash-Loop kann zurueckgesetzt werden um Auto-Restart wieder zu aktivieren.",
                inline=False,
            )

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------

    async def cog_app_command_error(self, interaction: discord.Interaction,
                                     error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Keine Berechtigung!", ephemeral=True
                )
        else:
            logger.error(f"Command error: {error}", exc_info=True)
            msg = f"❌ Fehler: {str(error)[:200]}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


class RollbackView(discord.ui.View):
    """View with rollback action buttons"""

    def __init__(self, sg_prot, user_id: int):
        super().__init__(timeout=120)
        self.sg_prot = sg_prot
        self.user_id = user_id

    @discord.ui.button(label="Crash-Loop Reset", style=discord.ButtonStyle.danger, emoji="🔄")
    async def reset_crash_loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Befehlsgeber kann dies ausfuehren.", ephemeral=True)
            return

        self.sg_prot.reset_crash_loop()
        await interaction.response.edit_message(
            content="✅ Crash-Loop zurueckgesetzt. Auto-Restart ist wieder aktiv.",
            embed=None,
            view=None,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MonitorCog(bot))

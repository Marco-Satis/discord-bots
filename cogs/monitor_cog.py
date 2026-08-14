"""
Monitor Cog - Monitoring slash commands
Commands: /performance, /dashboard, /stats, /report, /backup stats
"""

import asyncio
import shutil
import time
import json
import tracemalloc
import psutil
import discord
from pathlib import Path
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from utils.logger import get_logger
from utils.formatting import format_uptime, format_bytes, progress_bar
from utils.ui_kit import progress_bar as ui_progress_bar, subtext, truncate, zahl
from utils.permissions import is_admin, is_owner
from utils.config import PROJECT_ROOT
from utils.embeds import (
    COLOR_ERROR,
    COLOR_SUCCESS,
    COLOR_WARNING,
    hud_embed,
    info_embed,
    warning_embed,
)

logger = get_logger("monitor_cog")

# Discord erlaubt 4096 Zeichen Beschreibung — Puffer fuer den Kopf.
MAX_BESCHREIBUNG = 4000


def build_world_embed(world_stats) -> discord.Embed:
    """
    Welt-Statistik als HUD-Panel: Kennzahlen-Kopf, Gruppen als Subtext.

    Getrennt vom Command, damit der gerenderte Text testbar ist
    (``tests/test_monitor_embeds.py``) — vorher lag alles im Interaction-Pfad.
    """
    # Kennzahlen-Kopf statt Feld-Wueste: erst die drei Zahlen, die die
    # Welt beschreiben, dann Gruppen als Subtext-Ueberschrift + eine Zeile.
    def _gruppe(titel: str, teile: List[str]) -> List[str]:
        """Ueberschrift + Werte-Zeile; leere Gruppen entfallen komplett."""
        if not teile:
            return []
        return ["", subtext(titel), " · ".join(teile)]

    kennzahlen = [(zahl(world_stats.total_buildings), "Gebäude")]
    if world_stats.production_machines > 0:
        kennzahlen.append((zahl(world_stats.production_machines), "Maschinen"))
    if world_stats.total_power_mw > 0:
        kennzahlen.append((zahl(world_stats.total_power_mw), "MW"))

    zeilen: List[str] = [
        subtext(
            f"Spielzeit {world_stats.play_hours}h · "
            f"gespeichert {world_stats.save_date} · {world_stats.save_size}"
        )
    ]

    bau: List[str] = []
    if world_stats.foundations > 0:
        bau.append(f"Fundamente {zahl(world_stats.foundations)}")
    if world_stats.walls > 0:
        bau.append(f"Wände {zahl(world_stats.walls)}")
    if world_stats.storage > 0:
        bau.append(f"Speicher {zahl(world_stats.storage)}")
    if world_stats.power_poles > 0:
        bau.append(f"Strompole {zahl(world_stats.power_poles)}")
    zeilen += _gruppe("BAU", bau)

    produktion: List[str] = []
    for wert, name in (
        (world_stats.smelters, "Schmelzer"),
        (world_stats.foundries, "Gießereien"),
        (world_stats.constructors, "Konstruktoren"),
        (world_stats.assemblers, "Montagewerke"),
        (world_stats.manufacturers, "Fabrikmaschinen"),
        (world_stats.refineries, "Raffinerien"),
        (world_stats.blenders, "Mixer"),
    ):
        if wert > 0:
            produktion.append(f"{name} {zahl(wert)}")
    zeilen += _gruppe("PRODUKTION", produktion)

    rohstoffe: List[str] = []
    for wert, name in (
        (world_stats.miners, "Bergbau"),
        (world_stats.oil_extractors, "Öl"),
        (world_stats.water_extractors, "Wasser"),
        (world_stats.resource_well_pressurizers, "Bohrlöcher"),
    ):
        if wert > 0:
            rohstoffe.append(f"{name} {zahl(wert)}")
    zeilen += _gruppe("ROHSTOFFE", rohstoffe)

    strom: List[str] = []
    if world_stats.generators > 0:
        strom.append(f"Generatoren {zahl(world_stats.generators)}")
    for wert, name in (
        (world_stats.biomass_burners, "Biomasse"),
        (world_stats.coal_generators, "Kohle"),
        (world_stats.fuel_generators, "Brennstoff"),
        (world_stats.geothermal_generators, "Geotherm"),
        (world_stats.nuclear_plants, "Nuklear"),
        (world_stats.alien_augmenters, "Alien"),
        (world_stats.power_storage, "Speicher"),
    ):
        if wert > 0:
            strom.append(f"{name} {zahl(wert)}")
    zeilen += _gruppe("STROM", strom)

    baender: List[str] = []
    if world_stats.conveyor_belts > 0:
        baender.append(f"Bänder {zahl(world_stats.conveyor_belts)}")
    for wert, name in (
        (world_stats.belts_mk1, "MK1"),
        (world_stats.belts_mk2, "MK2"),
        (world_stats.belts_mk3, "MK3"),
        (world_stats.belts_mk4, "MK4"),
        (world_stats.belts_mk5, "MK5"),
        (world_stats.belts_mk6, "MK6"),
        (world_stats.lifts_total, "Aufzüge"),
    ):
        if wert > 0:
            baender.append(f"{name} {zahl(wert)}")
    zeilen += _gruppe("FÖRDERBÄNDER", baender)

    rohre: List[str] = []
    if world_stats.pipes > 0:
        rohre.append(f"Rohre {zahl(world_stats.pipes)}")
    for wert, name in (
        (world_stats.pipes_mk1, "MK1"),
        (world_stats.pipes_mk2, "MK2"),
        (world_stats.pipeline_pumps, "Pumpen"),
        (world_stats.valves, "Ventile"),
    ):
        if wert > 0:
            rohre.append(f"{name} {zahl(wert)}")
    zeilen += _gruppe("ROHRLEITUNGEN", rohre)

    verteiler: List[str] = []
    for wert, name in (
        (world_stats.splitters, "Splitter"),
        (world_stats.smart_splitters, "Smart"),
        (world_stats.programmable_splitters, "Programmierbar"),
        (world_stats.mergers, "Merger"),
        (world_stats.priority_mergers, "Priorität"),
    ):
        if wert > 0:
            verteiler.append(f"{name} {zahl(wert)}")
    zeilen += _gruppe("VERTEILER", verteiler)

    transport: List[str] = []
    for wert, name in (
        (world_stats.trains, "Züge"),
        (world_stats.locomotives, "Loks"),
        (world_stats.freight_cars, "Waggons"),
        (world_stats.stations, "Stationen"),
        (world_stats.vehicles, "Fahrzeuge"),
        (world_stats.trucks, "Lastwagen"),
        (world_stats.explorers, "Explorer"),
        (world_stats.drone_ports, "Drohnen-Ports"),
    ):
        if wert > 0:
            transport.append(f"{name} {zahl(wert)}")
    zeilen += _gruppe("TRANSPORT", transport)

    embed = hud_embed(
        "WELTSTATISTIK",
        state="info",
        meta=kennzahlen,
        description=f"**{discord.utils.escape_markdown(str(world_stats.session_name))}**",
        lines=zeilen,
        footer=(
            f"Analysiert: {world_stats.last_analyzed}"
            if world_stats.last_analyzed
            else None
        ),
    )
    # Discord nimmt 4096 Zeichen Beschreibung. Eine sehr weit gebaute Fabrik
    # kaeme in die Naehe — sichtbar kuerzen ist besser als ein HTTPException
    # beim Senden. Gleicher Riegel wie in den Backup-Panels.
    if embed.description and len(embed.description) > MAX_BESCHREIBUNG:
        embed.description = truncate(embed.description, MAX_BESCHREIBUNG)

    return embed


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

        embed = info_embed(
            title="📊 System-Performance",
            description=f"Netcup RS 4000 G12 | Uptime: {format_uptime(sys_uptime)}",
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
                "⚠️ Dashboard-Update Funktion nicht verfügbar.", ephemeral=True
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
            await interaction.followup.send("❌ Player Tracker nicht verfügbar.")
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
                    f"❌ Keine Daten für Spieler **{spieler}** gefunden."
                )
                return

            online_str = "🟢 Online" if stats["is_online"] else "🔴 Offline"
            if stats["is_online"] and stats["current_session_minutes"] > 0:
                online_str += f" (seit {stats['current_session_minutes']} Min)"

            embed = discord.Embed(
                title=f"📊 Statistiken: {stats['name']}",
                color=COLOR_SUCCESS if stats["is_online"] else 0x888888,
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

            embed = info_embed(
                title="📊 Spieler-Übersicht",
                description=f"{len(all_stats)} Spieler erfasst",
            )

            for i, stats in enumerate(all_stats[:15]):
                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "▫️"
                status = "🟢" if stats["is_online"] else ""
                sicherer_name = discord.utils.escape_markdown(str(stats["name"]))
                embed.add_field(
                    name=f"{medal} {sicherer_name} {status}",
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

        # --- Kennzahlen sammeln (Rendern erst am Ende, HUD-Stil) ---
        stats_tracker = getattr(self.bot, "stats_tracker", None)
        kennzahlen: List[Tuple[str, str]] = []
        balken: Optional[Tuple[float, float]] = None
        zeilen: List[str] = []
        crash_liste: List[str] = []
        top_liste: List[str] = []

        # --- Uptime + Peak ---
        if stats_tracker:
            uptime_pct = stats_tracker.get_uptime_percent(days)
            kennzahlen.append((f"{uptime_pct}%", "Uptime"))
            balken = (uptime_pct, 100)
            zeilen.append(subtext(
                f"{start_date} – {end_date} · {days} Tage · "
                f"{stats_tracker.get_total_checks(days)} Checks · "
                f"Peak {stats_tracker.get_peak_players(days)} Spieler"
            ))

        # --- Crashes ---
        if stats_tracker:
            crashes = stats_tracker.get_crashes(days)
            kennzahlen.append((str(len(crashes)), "Crashes"))
            for c in crashes[-5:]:
                ts = c["ts"][:16].replace("T", " ")
                crash_liste.append(f"`{ts}` — Crash #{c['number']}")
            if len(crashes) > 5:
                crash_liste.append(f"… und {len(crashes) - 5} weitere")

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

            # Vorperiode zum Vergleich
            prev_cutoff = (now - timedelta(days=days * 2)).isoformat()
            prev_time = 0
            for record in self.player_tracker.players.values():
                for s in record.sessions:
                    if prev_cutoff <= s.get("join", "") < cutoff_iso:
                        prev_time += s.get("duration_minutes", 0)

            curr_hours = round(total_time / 60, 1)
            prev_hours = round(prev_time / 60, 1)
            trend = ""
            if prev_hours > 0:
                change = ((curr_hours - prev_hours) / prev_hours) * 100
                pfeil = "\U0001f4c8" if change > 0 else "\U0001f4c9" if change < 0 else "➡️"
                trend = f" · Vorperiode {prev_hours}h ({pfeil} {change:+.0f}%)"

            kennzahlen.insert(1, (str(len(active)), "Spieler"))
            zeilen.append(subtext(
                f"\U0001f3ae {total_sessions} Sessions · {curr_hours}h Spielzeit{trend}"
            ))

            for i, (name, data) in enumerate(
                sorted(active.items(), key=lambda x: x[1]["playtime_hours"], reverse=True)[:5], 1
            ):
                medal = "\U0001f947" if i == 1 else "\U0001f948" if i == 2 else "\U0001f949" if i == 3 else f"{i}."
                top_liste.append(
                    f"{medal} **{discord.utils.escape_markdown(name)}** — {data['playtime_hours']}h ({data['sessions']} Sess.)"
                )

        # --- Savegame-Wachstum ---
        if stats_tracker:
            growth = stats_tracker.get_savegame_growth(days)
            if growth:
                pfeil = "\U0001f4c8" if growth["growth_mb"] > 0 else "\U0001f4c9" if growth["growth_mb"] < 0 else "➡️"
                zeilen.append(subtext(
                    f"\U0001f4be {growth['start_mb']} → {growth['end_mb']} MB "
                    f"({pfeil} {growth['growth_mb']:+.1f} MB, {growth['growth_percent']:+.1f}%)"
                ))

        # --- Performance (Schnitt / Spitze) ---
        if self.perf_monitor:
            avg = self.perf_monitor.get_averages(minutes=days * 1440)
            peak = self.perf_monitor.get_peak(minutes=days * 1440)
            if avg.get("samples", 0) > 0:
                zeilen.append(subtext(
                    f"\U0001f4ca CPU {avg['cpu']:.0f}/{peak['cpu']:.0f}% · "
                    f"RAM {avg['ram']:.0f}/{peak['ram']:.0f}% · "
                    f"Disk {avg['disk']:.0f}/{peak['disk']:.0f}% · "
                    f"SAT {avg['process_cpu']:.0f}% CPU, {avg['process_ram']} MB"
                ))

        # --- Health ---
        if self.health_checker:
            summary = self.health_checker.get_summary()
            zeilen.append(subtext(
                f"\U0001fa7a {summary.get('state', '?')} · "
                f"Tick {summary.get('tick_rate', 0):.1f} · "
                f"{summary.get('crashes_total', 0)} Crashes gesamt"
            ))

        # --- System & Backup ---
        extra_parts = []
        if self.update_checker:
            if self.update_checker.update_available:
                extra_parts.append("⚠️ Update verfügbar")
            elif self.update_checker.installed_buildid:
                extra_parts.append(f"Build {self.update_checker.installed_buildid}")

        bm = getattr(self.bot, "backup_manager", None)
        if bm:
            extra_parts.append(f"{len(bm.list_backups())} Backups lokal")

        od = getattr(self.bot, "onedrive_backup", None)
        if od and od.enabled:
            extra_parts.append("☁️ OneDrive aktiv")

        cb = getattr(self.bot, "config_backup", None)
        if cb and cb.last_backup:
            extra_parts.append(f"\U0001f527 Config-Backup {cb.last_backup.strftime('%d.%m. %H:%M')}")

        if extra_parts:
            zeilen.append(subtext(" · ".join(extra_parts)))

        embed = hud_embed(
            f"{title_prefix.upper().strip()}BERICHT" if title_prefix else "BERICHT",
            state="ok",
            meta=kennzahlen or None,
            bar=balken,
            lines=zeilen or None,
            footer=f"Zeitraum: {days} Tage | /report [tage] für anderen Zeitraum",
        )

        # Zwei echte Listen bleiben Felder — dafuer sind Felder da.
        if crash_liste:
            embed.add_field(name="Crashes", value="\n".join(crash_liste), inline=False)
        if top_liste:
            embed.add_field(name="Top Spieler", value="\n".join(top_liste), inline=False)

        await interaction.followup.send(embed=embed)

    @staticmethod
    def _make_bar(percent: float, length: int = 10) -> str:
        """Fortschrittsbalken — nutzt den Hausstil statt einer eigenen Optik.

        Vorher rendered diese Methode ``███░░░`` waehrend dieselbe Datei an
        anderer Stelle ``[###---]`` benutzte: zwei Balken-Optiken in einem Bot.
        """
        return ui_progress_bar(percent, 100, length)

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

            embed = build_world_embed(world_stats)

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"World stats error: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Fehler beim Abrufen der Weltstatistiken: {str(e)[:100]}"
            )

    # ------------------------------------------------------------------
    # /selftest command
    # ------------------------------------------------------------------

    @app_commands.command(name="selftest", description="Alle Bot-Systeme prüfen")
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
                color=COLOR_SUCCESS if failed == 0 else 0xf39c12 if failed <= 2 else 0xe74c3c,
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
            # Read command log (async)
            def _read_log():
                with open(log_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            all_logs = await asyncio.to_thread(_read_log)

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
            embed = info_embed(
                title=f"📋 Letzte Commands ({len(entries)})",
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

        embed = info_embed(
            title="⛏️ Minecraft Server-Statistiken",
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
                    except Exception as e:
                        logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

                # World-Größe
                try:
                    world_bytes = await srv.get_world_size()
                    if world_bytes > 0:
                        lines.append(f"Welt: {world_bytes / (1024 * 1024):.1f} MB")
                except Exception as e:
                    logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

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

        embed = info_embed(
            title="📋 Minecraft Bericht",
            description=f"{start_date} — {end_date} ({days} Tage)",
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
                f"❌ Kein Crash-Replay für Server **{sid}** verfügbar.",
                ephemeral=True,
            )
            return

        replays = cr.list_replays()
        if not replays:
            await interaction.followup.send(
                f"✅ Keine Crash-Replays für **{sid}** vorhanden.",
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
                    f"❌ Kein Replay für Crash #{nummer} gefunden.",
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

            embed = warning_embed(
                title=f"🔍 MC Crash Replays — {sid} ({len(replays)})",
                description="\n".join(lines),
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
            color=COLOR_SUCCESS if notifier.enabled else 0xe74c3c,
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
                "📦 Update verfügbar"
            ),
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /backup stats (Phase 8c)
    # ------------------------------------------------------------------

    backup_grp = app_commands.Group(
        name="backup", description="Backup-Verwaltung & Statistiken"
    )

    @backup_grp.command(name="stats", description="Backup-Statistiken aller Server")
    @app_commands.check(lambda i: True)  # Spieler-Berechtigung (alle)
    async def backup_stats(self, interaction: discord.Interaction):
        """Zeigt Backup-Übersicht für alle Server mit Speicherplatz-Info"""
        await interaction.response.defer()

        loop = asyncio.get_running_loop()

        # Speicherplatz ermitteln (async via run_in_executor)
        disk = await loop.run_in_executor(None, shutil.disk_usage, "/")
        disk_total = disk.total
        disk_used = disk.used
        disk_free = disk.free
        disk_percent = (disk_used / disk_total * 100) if disk_total > 0 else 0

        # Embed-Farbe basierend auf Speicherplatz
        if disk_percent >= 95:
            color = COLOR_ERROR  # Rot: Kritisch
            disk_status = "KRITISCH"
        elif disk_percent >= 80:
            color = COLOR_WARNING  # Gelb: Warnung
            disk_status = "Warnung"
        else:
            color = COLOR_SUCCESS  # Gruen: OK
            disk_status = "OK"

        embed = discord.Embed(
            title="Backup-Statistiken",
            color=color,
        )

        # Satisfactory Backups
        sat_bm = getattr(self.bot, "backup_manager", None)
        if sat_bm:
            try:
                sat_backups = await loop.run_in_executor(None, sat_bm.list_backups, 999)
                sat_count = len(sat_backups)
                sat_total = await loop.run_in_executor(None, sat_bm.total_size)
                oldest = sat_backups[-1].get("created_at", "?")[:10] if sat_backups else "—"
                newest = sat_backups[0].get("created_at", "?")[:10] if sat_backups else "—"
                embed.add_field(
                    name="Satisfactory",
                    value=(
                        f"**Anzahl:** {sat_count}\n"
                        f"**Größe:** {format_bytes(sat_total)}\n"
                        f"**Aeltestes:** {oldest}\n"
                        f"**Neuestes:** {newest}"
                    ),
                    inline=True,
                )
            except Exception as e:
                embed.add_field(
                    name="Satisfactory", value=f"Fehler: {e}", inline=True
                )

        # Minecraft Backups (pro Server)
        mc_backup_mgrs = getattr(self.bot, "mc_backup_mgrs", {})
        mc_servers = getattr(self.bot, "mc_servers", {})
        for server_id, mgr in mc_backup_mgrs.items():
            srv = mc_servers.get(server_id)
            display_name = srv.display_name if srv else server_id
            try:
                mc_backups = await mgr.list_backups(max_results=999)
                mc_count = len(mc_backups)
                mc_total = sum(b.get("size_bytes", 0) for b in mc_backups)
                oldest = mc_backups[-1].get("created", "?")[:10] if mc_backups else "—"
                newest = mc_backups[0].get("created", "?")[:10] if mc_backups else "—"
                embed.add_field(
                    name=f"MC {display_name}",
                    value=(
                        f"**Anzahl:** {mc_count}\n"
                        f"**Größe:** {format_bytes(mc_total)}\n"
                        f"**Aeltestes:** {oldest}\n"
                        f"**Neuestes:** {newest}"
                    ),
                    inline=True,
                )
            except Exception as e:
                embed.add_field(
                    name=f"MC {display_name}", value=f"Fehler: {e}", inline=True
                )

        # Speicherplatz-Übersicht
        embed.add_field(
            name=f"Speicherplatz ({disk_status})",
            value=(
                f"**Gesamt:** {format_bytes(disk_total)}\n"
                f"**Belegt:** {format_bytes(disk_used)} ({disk_percent:.1f}%)\n"
                f"**Frei:** {format_bytes(disk_free)}\n"
                f"{progress_bar(disk_percent, 100, length=12)}"
            ),
            inline=False,
        )

        if disk_percent >= 95:
            embed.set_footer(text="KRITISCH: Backup-Speicher fast voll!")
        elif disk_percent >= 80:
            embed.set_footer(text="Warnung: Backup-Speicher wird knapp")

        await interaction.followup.send(embed=embed)

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
            color=COLOR_SUCCESS if od.enabled and configured else 0xe74c3c,
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="Aktiviert",
            value="✅ Ja" if od.enabled else "❌ Nein",
            inline=True,
        )
        embed.add_field(
            name="rclone konfiguriert",
            value="✅ Ja" if configured else "❌ Nein (rclone config ausführen)",
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

        embed = info_embed(
            title=f"☁️ Cloud-Backups ({len(files)})",
            description="\n".join(lines),
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

            embed = warning_embed(
                title=f"🔍 Crash Replays ({len(replays)})",
                description="\n".join(lines),
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
            await interaction.followup.send("❌ Savegame-Schutz nicht verfügbar.", ephemeral=True)
            return

        info = sg_prot.get_rollback_info()

        embed = discord.Embed(
            title="🔄 Rollback-Status",
            color=COLOR_WARNING if info["crash_loop_active"] else 0x5865F2,
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
                value="Nicht verfügbar",
                inline=False,
            )

        # Add reset button if crash loop is active
        view = None
        if info["crash_loop_active"]:
            view = RollbackView(sg_prot, interaction.user.id)
            embed.add_field(
                name="Aktion",
                value="Crash-Loop kann zurückgesetzt werden um Auto-Restart wieder zu aktivieren.",
                inline=False,
            )

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # /debug_memory -- tracemalloc Top-30 Allocators (Etappe 2.6)
    # ------------------------------------------------------------------

    @app_commands.command(
        name="debug_memory",
        description="Tracemalloc Top-30 Allocators (Admin-only, RSS-Diagnose)",
    )
    @app_commands.check(is_admin)
    async def debug_memory_cmd(self, interaction: discord.Interaction):
        """Memory-Diagnose via tracemalloc - zeigt die 30 groessten Allocators."""
        await interaction.response.defer(ephemeral=True)

        if not tracemalloc.is_tracing():
            await interaction.followup.send(
                "tracemalloc laeuft nicht. Setze `TRACEMALLOC_ENABLED=1` in .env "
                "und restart den Bot.",
                ephemeral=True,
            )
            return

        try:
            snapshot = tracemalloc.take_snapshot()
            stats = snapshot.statistics("lineno")[:30]
            total_kb = sum(s.size for s in stats) / 1024

            lines = [f"**Top {len(stats)} Allocators** (Sum: {total_kb:,.0f} KB)\n"]
            for i, stat in enumerate(stats, 1):
                tb = stat.traceback[0] if stat.traceback else None
                loc = f"{Path(tb.filename).name}:{tb.lineno}" if tb else "?"
                lines.append(
                    f"`{i:2d}.` `{loc}` -- {stat.size / 1024:,.1f} KB ({stat.count} blocks)"
                )

            text = "\n".join(lines)
            if len(text) > 1900:
                text = text[:1900] + "\n...(truncated)"

            await interaction.followup.send(text, ephemeral=True)
        except Exception as e:
            logger.exception("debug_memory failed")
            await interaction.followup.send(f"Fehler: {e}", ephemeral=True)

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
            await interaction.response.send_message("Nur der Befehlsgeber kann dies ausführen.", ephemeral=True)
            return

        self.sg_prot.reset_crash_loop()
        await interaction.response.edit_message(
            content="✅ Crash-Loop zurückgesetzt. Auto-Restart ist wieder aktiv.",
            embed=None,
            view=None,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MonitorCog(bot))

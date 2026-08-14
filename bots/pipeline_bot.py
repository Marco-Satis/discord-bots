"""
Pipeline Bot — Standalone 24/7-Bot fuer die n8n-TikTok-Curation-Pipeline.

Zweck:
  Ausgekoppelt aus dem Admin-Bot (2026-06-13). Haelt die Pipeline-Approval-
  Buttons (Approve/Dismiss/Recategorize) UND das Re-Analyse-Control-Panel
  (Start/Pause/Stop/Status) am Leben. Laeuft 24/7 auf dem Server, unabhaengig
  von Moderation/Community-Features des Admin-Bots.

Architektur:
  - Eigene Discord-Application/Token-Identitaet (PIPELINE_BOT_TOKEN).
  - Laedt `cogs.pipeline_approval_cog` (Findings-Buttons) + `cogs.pipeline_control_cog`
    (Control-Panel, auto-postet bei on_ready, persistente Buttons via bot.add_view).
  - Minimale Intents: KEINE privilegierten Intents noetig — Button-Component-
    Interactions kommen ueber den Gateway-INTERACTION_CREATE-Dispatch unabhaengig
    von message_content/members. `Intents.default()` reicht (privilegierte sind
    da bereits aus).

WICHTIG — Token-Routing-Abhaengigkeit (server-seitig, von Marco zu erledigen):
  Discord routet Button-Interactions an die Application, die die NACHRICHT mit den
  Buttons gesendet hat. Der Notification-Poster (n8n_stack/scripts/pipeline_runner.py,
  step_discord_notify) muss die Pipeline-Notifications mit DIESEM Bot-Token posten
  (vorher Admin-Bot-Token) — sonst landen die Clicks weiter beim Admin-Bot und der
  on_interaction-Listener hier feuert nie. Siehe docs/HANDOFF_pipeline_bot_*.md.
"""

import sys
import json
import time
import asyncio
import discord
from discord.ext import commands, tasks
from pathlib import Path
from datetime import datetime, timezone

# Projektroot zum Pfad hinzufuegen (Modul-Imports wie utils.*, cogs.*)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_env, get_env, get_config, DATA_DIR
from utils.logger import get_logger
from utils.shutdown import setup_signal_handlers
from utils.loop_guard import guard

# Umgebung laden
load_env()

# Konfiguration
TOKEN = get_env("PIPELINE_BOT_TOKEN")

logger = get_logger("pipeline_bot")

# ------------------------------------------------------------------
# Token-Pruefung — ohne Token wird der Bot nicht gestartet
# ------------------------------------------------------------------

if not TOKEN:
    logger.info("PIPELINE_BOT_TOKEN nicht gesetzt — Pipeline Bot deaktiviert")
    sys.exit(0)

# ------------------------------------------------------------------
# Bot Setup — minimale Intents (nur Gateway-Interactions noetig)
# ------------------------------------------------------------------

intents = discord.Intents.default()  # KEINE privilegierten Intents noetig

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    allowed_mentions=discord.AllowedMentions.none(),
)

# Konfiguration fuer den Cog bereitstellen (Parity mit Admin-Bot)
bot.config = get_config()

PIPELINE_DATA_DIR = DATA_DIR / "pipeline"
bot.pipeline_data_dir = PIPELINE_DATA_DIR

# Die beiden Pipeline-Cogs — Approval-Buttons (Findings) + Control-Panel (Sweep).
INITIAL_COGS: list[str] = [
    "cogs.pipeline_approval_cog",
    "cogs.pipeline_control_cog",
]


# ------------------------------------------------------------------
# Bot-Status-Writer (Ping + Uptime fuer Dashboard)
# ------------------------------------------------------------------

_pipeline_bot_start_time = time.time()


def _write_pipeline_bot_status():
    """Schreibt Pipeline Bot Status (Ping, Uptime) als JSON fuer Dashboard."""
    try:
        PIPELINE_DATA_DIR.mkdir(parents=True, exist_ok=True)
        uptime_secs = int(time.time() - _pipeline_bot_start_time)
        hours = uptime_secs // 3600
        mins = (uptime_secs % 3600) // 60
        uptime_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

        data = {
            "status": "online" if bot.is_ready() else "offline",
            "ping_ms": round(bot.latency * 1000) if bot.latency and bot.latency != float("inf") else 0,
            "uptime": uptime_str,
            "last_update": datetime.now(timezone.utc).isoformat(),
        }
        tmp = PIPELINE_DATA_DIR / "bot_status.json.tmp"
        target = PIPELINE_DATA_DIR / "bot_status.json"
        with open(tmp, "w") as f:
            json.dump(data, f)
        tmp.replace(target)  # atomic
    except Exception as e:
        logger.debug(f"Bot-Status schreiben fehlgeschlagen: {e}")


@tasks.loop(seconds=30)
async def pipeline_status_writer_task():
    await asyncio.to_thread(_write_pipeline_bot_status)


@pipeline_status_writer_task.before_loop
async def before_pipeline_status_writer():
    await bot.wait_until_ready()
    await asyncio.sleep(5)


# ------------------------------------------------------------------
# Events
# ------------------------------------------------------------------

@bot.event
async def on_ready():
    if not bot.user:
        return
    logger.info(f"Pipeline Bot online: {bot.user} (ID: {bot.user.id})")
    logger.info(f"Guilds: {[g.name for g in bot.guilds]}")
    if not pipeline_status_writer_task.is_running():
        # Ohne Fehler-Handler beendet die erste unerwartete Ausnahme diese Schleife
        # endgueltig — das Dashboard zeigte den Bot dann dauerhaft als veraltet.
        guard(pipeline_status_writer_task, name="pipeline_status_writer_task")
        pipeline_status_writer_task.start()


@bot.event
async def on_command_error(ctx, error):
    logger.error(f"Command error: {error}", exc_info=True)


# ------------------------------------------------------------------
# Setup / Main
# ------------------------------------------------------------------

async def load_cogs():
    for cog in INITIAL_COGS:
        try:
            await bot.load_extension(cog)
            logger.info(f"Cog loaded: {cog}")
        except Exception as e:
            logger.error(f"Failed to load cog {cog}: {e}", exc_info=True)


@bot.event
async def setup_hook():
    PIPELINE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Pipeline data dir: {PIPELINE_DATA_DIR}")

    await load_cogs()

    # Der Cog registriert KEINE Slash-Commands (rein Component-Interactions).
    # tree.sync daher bewusst weggelassen — nichts zu syncen.

    # Signal-Handler im laufenden Event-Loop registrieren (analog Admin-Bot-Fix
    # 2026-06-12): setup_hook laeuft im Loop, den bot.run() via asyncio.run()
    # erstellt — SIGTERM-Cleanups landen sonst auf einem nie laufenden Loop.
    setup_signal_handlers(bot, "Pipeline Bot")

    logger.info("Setup hook complete")


def main():
    logger.info("Starting Pipeline Bot...")
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

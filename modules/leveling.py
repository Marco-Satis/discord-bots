"""
Leveling Manager — XP- und Level-System fuer den Admin Bot (per-Guild)

Verwaltet Erfahrungspunkte (XP) und Level pro Server-Mitglied **je Guild**.
XP werden durch Nachrichten und Voice-Aktivitaet gesammelt. Bei Level-Ups
koennen automatisch Rollen vergeben werden.

Multi-Tenant (Phase C, Migration v6):
  - Userdaten sind guild-scoped (`UNIQUE(guild_id, user_id)`), kein Cross-Guild-Leak.
  - In-Memory-Cache `_data` ist nach Guild verschachtelt: {guild_id: {user_id: {...}}}.
  - Konfiguration kommt per-Guild aus `guild_config` (Migration v5) ueber
    `modules.guild_context.GuildConfig`, Keys mit Prefix `leveling.`.
    Die JSON-Datei bleibt als globaler Default/Fallback erhalten (Backward-Compat
    fuer bestehende Single-Guild-Deployments bevor ein guild_config-Eintrag existiert).

Persistenz:
  - Userdaten:     SQLite leveling-Tabelle (alleinige Datenquelle, guild-scoped)
  - Einstellungen: per-Guild `guild_config` (Override) ueber globalem JSON-Default

SQLite-Tabelle leveling (ab v6):
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  xp INTEGER DEFAULT 0,
  level INTEGER DEFAULT 0,
  messages INTEGER DEFAULT 0,
  voice_minutes INTEGER DEFAULT 0,
  last_xp_time TIMESTAMP,
  UNIQUE(guild_id, user_id)

Level-Formel: xp_needed = 5 * level^2 + 50 * level + 100
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime

# secrets.SystemRandom fuer XP-Variation (Defense-in-Depth, verhindert
# Pattern-Vorhersage durch Spieler die XP-Boost erfarmen wollen).
_rand = secrets.SystemRandom()
from pathlib import Path
from typing import Any

from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR
from utils.async_tasks import schedule_from_sync
from modules.database.db_manager import get_db
from modules.guild_context import GuildConfig

logger = get_logger("leveling_manager")

# Standard-Pfade
LEVELING_CONFIG_FILE = ADMIN_DATA_DIR / "leveling_config.json"

# Sentinel: unterscheidet "kein guild_config-Override" von gespeichertem None.
_UNSET = object()


def _default_config() -> dict[str, Any]:
    """Standard-Konfiguration fuer das Leveling-System zurueckgeben."""
    return {
        "xp_per_message_min": 15,
        "xp_per_message_max": 25,
        "xp_cooldown_seconds": 60,
        "voice_xp_per_minute": 5,
        "level_formula": "5 * level^2 + 50 * level + 100",
        "channel_multipliers": {},
        "role_rewards": {},
        "no_xp_channels": [],
        # Level-Up-Karte mit Bild-Hintergrund (statt Standard-Embed)
        "levelup_card_enabled": False,
        "levelup_card_bg": None,
        "levelup_card_accent": "#f1c40f",
    }


class LevelingManager:
    """
    Verwaltet XP, Level und Belohnungen fuer Server-Mitglieder — pro Guild.

    Persistenz:
    - Liest aus SQLite (leveling-Tabelle, guild-scoped)
    - Schreibt User-Aenderungen in SQLite (fire-and-forget)
    - Konfiguration kommt per-Guild aus `guild_config` (DB), gecacht als
      vollstaendig gemergter Dict je Guild ueber globalem JSON-Default.

    Alle XP-/Daten-Methoden erwarten `guild_id` als ersten Parameter.
    Config-Lese-Methoden sind sync (Hot-Path), Config-Setter sind async
    (schreiben nach `guild_config`).

    Der Manager kuemmert sich um die Datenverwaltung. Rollen-Vergabe und
    Nachrichten-Versand uebernimmt das Cog.
    """

    def __init__(
        self,
        config_file: Path | None = None,
        **kwargs,
    ) -> None:
        """
        Args:
            config_file: Pfad zur Config-JSON (globaler Default/Fallback;
                         Standard: data/admin/leveling_config.json)
        """
        self.config_file = config_file or LEVELING_CONFIG_FILE
        # Verschachtelt: guild_id -> user_id -> user_data
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        # Globaler JSON-Default (Fallback bevor guild_config geladen ist)
        self._config: dict[str, Any] = _default_config()
        # Per-Guild gemergte Config (Default ueberlagert mit guild_config)
        self._guild_config: dict[str, dict[str, Any]] = {}
        # Guards gegen mehrfaches Lazy-Load im selben Loop-Tick
        self._config_loading: set[str] = set()
        self._load_config()

    # ------------------------------------------------------------------
    # Persistence — SQLite (alleinige Datenquelle, guild-scoped)
    # ------------------------------------------------------------------

    async def load_from_db(self) -> bool:
        """
        Leveling-Daten aller Guilds aus SQLite laden und in _data uebernehmen.

        Returns:
            True wenn erfolgreich geladen, False bei Fehler
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT guild_id, user_id, xp, level, messages, voice_minutes, last_xp_time "
                "FROM leveling"
            )
            rows = await cursor.fetchall()

            loaded: dict[str, dict[str, dict[str, Any]]] = {}
            for row in rows:
                gid = str(row["guild_id"])
                uid = str(row["user_id"])
                loaded.setdefault(gid, {})[uid] = {
                    "xp": row["xp"] or 0,
                    "level": row["level"] or 0,
                    "total_messages": row["messages"] or 0,
                    "voice_minutes": row["voice_minutes"] or 0,
                    "last_xp_at": row["last_xp_time"],
                }

            self._data = loaded
            total = sum(len(users) for users in loaded.values())
            logger.info(
                f"Leveling-Daten aus SQLite geladen: {total} User in "
                f"{len(loaded)} Guild(s)"
            )
            return True

        except Exception as e:
            logger.error(f"Leveling-Daten aus SQLite laden fehlgeschlagen: {e}")
            return False

    # ------------------------------------------------------------------
    # Fire-and-Forget — sync -> async Bruecke
    # ------------------------------------------------------------------

    def _fire_and_forget(self, coro) -> None:
        """
        Plant eine async Coroutine als getrackten Background-Task.
        Wird aus synchronem Code aufgerufen (add_message_xp, etc.).

        Reference-Tracking via utils.async_tasks gegen GC-Verlust
        (audit-fix 2026-05-17, async.md H2).
        """
        schedule_from_sync(coro, name="leveling.fire_and_forget")

    async def _db_upsert_user(
        self, guild_id: int | str, user_id: int | str, user_data: dict[str, Any]
    ) -> None:
        """
        User-Daten einer Guild in SQLite einfuegen/aktualisieren (Upsert).

        Args:
            guild_id: Discord-Guild-ID
            user_id: Discord-User-ID
            user_data: Dict mit xp, level, total_messages, voice_minutes, last_xp_at
        """
        try:
            db = await get_db()
            await db.execute(
                """
                INSERT INTO leveling
                    (guild_id, user_id, xp, level, messages, voice_minutes, last_xp_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    xp = excluded.xp,
                    level = excluded.level,
                    messages = excluded.messages,
                    voice_minutes = excluded.voice_minutes,
                    last_xp_time = excluded.last_xp_time
                """,
                (
                    str(guild_id),
                    str(user_id),
                    user_data.get("xp", 0),
                    user_data.get("level", 0),
                    user_data.get("total_messages", 0),
                    user_data.get("voice_minutes", 0),
                    user_data.get("last_xp_at"),
                ),
            )
            await db.commit()
        except Exception as e:
            logger.error(
                f"SQLite-Upsert fuer Guild {guild_id} User {user_id} fehlgeschlagen: {e}"
            )

    # ------------------------------------------------------------------
    # Persistence — globaler JSON-Default (Fallback)
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        """Globalen JSON-Default laden (Fallback bevor guild_config greift)."""
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # Defaults fuer fehlende Schluessel einsetzen
                defaults = _default_config()
                for key, value in defaults.items():
                    loaded.setdefault(key, value)
                self._config = loaded
                logger.info("Leveling-Default-Konfiguration (JSON) geladen")
            else:
                logger.info(
                    "Keine Leveling-Config-JSON vorhanden, verwende Standardwerte"
                )
                self._save_config()
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Leveling-Config laden fehlgeschlagen: {e}")

    def _save_config(self) -> None:
        """Globalen JSON-Default auf Disk speichern."""
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Leveling-Config speichern fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    # Per-Guild-Config (guild_config-Override ueber JSON-Default)
    # ------------------------------------------------------------------

    async def load_guild_config(self, guild_id: int | str) -> dict[str, Any]:
        """
        Per-Guild-Config laden: globaler JSON-Default ueberlagert mit
        `guild_config`-Eintraegen (Keys `leveling.<key>`). Ergebnis wird als
        vollstaendig gemergter Dict gecacht (Hot-Path-Lesen ist dann sync).

        Args:
            guild_id: Discord-Guild-ID

        Returns:
            Gemergte Config der Guild
        """
        gid = str(guild_id)
        merged = dict(self._config)
        try:
            for key in _default_config().keys():
                val = await GuildConfig.get(gid, f"leveling.{key}", _UNSET)
                if val is not _UNSET:
                    merged[key] = val
        except Exception as e:
            logger.error(f"Guild-Config laden fuer {gid} fehlgeschlagen: {e}")
        finally:
            self._guild_config[gid] = merged
            self._config_loading.discard(gid)
        return merged

    async def _ensure_guild_config(self, guild_id: int | str) -> dict[str, Any]:
        """Stellt sicher dass die gemergte Config der Guild im Cache liegt."""
        gid = str(guild_id)
        if gid not in self._guild_config:
            await self.load_guild_config(gid)
        return self._guild_config[gid]

    def _gcfg(self, guild_id: int | str) -> dict[str, Any]:
        """
        Sync-Zugriff auf die gemergte Guild-Config (Hot-Path).

        Liegt die Guild-Config noch nicht im Cache: globaler JSON-Default
        zurueckgeben UND einmalig ein Lazy-Load anstossen (greift ab naechster
        Nachricht). Verhindert Async im per-Message-Hot-Path.
        """
        gid = str(guild_id)
        cfg = self._guild_config.get(gid)
        if cfg is not None:
            return cfg
        if gid not in self._config_loading:
            self._config_loading.add(gid)
            self._fire_and_forget(self.load_guild_config(gid))
        return self._config

    # ------------------------------------------------------------------
    # Level-Formel
    # ------------------------------------------------------------------

    @staticmethod
    def xp_for_level(level: int) -> int:
        """
        Berechnet die benoetigten XP fuer ein bestimmtes Level.

        Formel: 5 * level^2 + 50 * level + 100

        Args:
            level: Das Ziel-Level

        Returns:
            Benoetigte XP um dieses Level zu erreichen
        """
        return 5 * level * level + 50 * level + 100

    # ------------------------------------------------------------------
    # User-Daten (guild-scoped)
    # ------------------------------------------------------------------

    def _ensure_user(self, guild_id: int | str, user_id: int | str) -> dict[str, Any]:
        """
        Sicherstellen dass ein User-Eintrag in der Guild existiert.

        Returns:
            User-Daten-Dictionary (Referenz auf den internen Eintrag)
        """
        gid = str(guild_id)
        uid = str(user_id)
        guild_users = self._data.setdefault(gid, {})
        if uid not in guild_users:
            guild_users[uid] = {
                "xp": 0,
                "level": 0,
                "total_messages": 0,
                "voice_minutes": 0,
                "last_xp_at": None,
            }
        return guild_users[uid]

    def get_user(self, guild_id: int | str, user_id: int | str) -> dict[str, Any]:
        """
        User-Daten einer Guild zurueckgeben (Kopie).

        Returns:
            Dict mit xp, level, total_messages, voice_minutes, last_xp_at
        """
        gid = str(guild_id)
        uid = str(user_id)
        guild_users = self._data.get(gid, {})
        if uid in guild_users:
            return dict(guild_users[uid])
        # Leerer Standard-User
        return {
            "xp": 0,
            "level": 0,
            "total_messages": 0,
            "voice_minutes": 0,
            "last_xp_at": None,
        }

    # ------------------------------------------------------------------
    # XP vergeben — Nachrichten
    # ------------------------------------------------------------------

    def add_message_xp(
        self,
        guild_id: int | str,
        user_id: int | str,
        channel_id: int | None = None,
    ) -> tuple[int, bool]:
        """
        XP fuer eine Nachricht vergeben (mit Cooldown-Pruefung), guild-scoped.

        Vergibt zufaellig xp_per_message_min bis xp_per_message_max XP der Guild.
        Beachtet den Cooldown (xp_cooldown_seconds) — innerhalb des Cooldowns
        werden 0 XP vergeben. Optional wird ein Channel-Multiplikator angewendet.

        Schreibt Aenderungen via fire-and-forget in SQLite.

        Returns:
            (xp_gained, leveled_up)
        """
        cfg = self._gcfg(guild_id)

        # No-XP-Channel: weder XP noch Message-Count (Spam-Kanaele ausschliessen)
        if channel_id is not None:
            no_xp = cfg.get("no_xp_channels", [])
            if no_xp and str(channel_id) in {str(c) for c in no_xp}:
                return 0, False

        user = self._ensure_user(guild_id, user_id)
        user["total_messages"] += 1

        # Cooldown pruefen
        now = datetime.now()
        cooldown = cfg.get("xp_cooldown_seconds", 60)
        last_xp_str = user.get("last_xp_at")

        if last_xp_str:
            try:
                last_xp = datetime.fromisoformat(last_xp_str)
                elapsed = (now - last_xp).total_seconds()
                if elapsed < cooldown:
                    # Nur messages zaehlt hoch, kein XP — trotzdem DB-Update
                    self._fire_and_forget(
                        self._db_upsert_user(guild_id, user_id, user)
                    )
                    return 0, False
            except (ValueError, TypeError):
                pass  # Ungueltiges Datum ignorieren, XP trotzdem geben

        # XP berechnen
        xp_min = cfg.get("xp_per_message_min", 15)
        xp_max = cfg.get("xp_per_message_max", 25)
        xp = _rand.randint(xp_min, xp_max)

        # Channel-Multiplikator anwenden
        if channel_id:
            multipliers = cfg.get("channel_multipliers", {})
            multiplier = multipliers.get(str(channel_id), 1.0)
            xp = int(xp * multiplier)

        # XP hinzufuegen und Level pruefen
        user["xp"] += xp
        user["last_xp_at"] = now.isoformat()

        leveled_up = self._check_level_up(user)

        # Fire-and-forget: SQLite-Update
        self._fire_and_forget(self._db_upsert_user(guild_id, user_id, user))

        return xp, leveled_up

    # ------------------------------------------------------------------
    # XP vergeben — Voice
    # ------------------------------------------------------------------

    def add_voice_xp(
        self,
        guild_id: int | str,
        user_id: int | str,
        minutes: int,
    ) -> tuple[int, bool]:
        """
        XP fuer Voice-Aktivitaet vergeben, guild-scoped.

        Schreibt Aenderungen via fire-and-forget in SQLite.

        Returns:
            (xp_gained, leveled_up)
        """
        if minutes <= 0:
            return 0, False

        cfg = self._gcfg(guild_id)
        user = self._ensure_user(guild_id, user_id)
        xp_per_min = cfg.get("voice_xp_per_minute", 5)
        xp = xp_per_min * minutes

        user["xp"] += xp
        user["voice_minutes"] += minutes

        leveled_up = self._check_level_up(user)

        # Fire-and-forget: SQLite-Update
        self._fire_and_forget(self._db_upsert_user(guild_id, user_id, user))

        return xp, leveled_up

    # ------------------------------------------------------------------
    # XP direkt setzen (Admin-Funktion)
    # ------------------------------------------------------------------

    def set_xp(self, guild_id: int | str, user_id: int | str, amount: int) -> None:
        """
        XP eines Users direkt setzen (Admin-Funktion), guild-scoped.

        Berechnet das Level automatisch neu.
        Schreibt Aenderungen via fire-and-forget in SQLite.

        Args:
            guild_id: Discord-Guild-ID
            user_id: Discord-User-ID
            amount: Neue XP-Anzahl (muss >= 0 sein)
        """
        if amount < 0:
            amount = 0

        user = self._ensure_user(guild_id, user_id)
        user["xp"] = amount

        # Level komplett neu berechnen
        level = 0
        while user["xp"] >= self.xp_for_level(level):
            level += 1
        # Level ist jetzt eins zu hoch (erste Stufe die NICHT erreicht wird)
        user["level"] = max(0, level - 1) if level > 0 else 0

        # Nochmal pruefen: Wenn genug XP fuer naechstes Level
        while user["xp"] >= self.xp_for_level(user["level"]):
            user["level"] += 1

        # Fire-and-forget: SQLite-Update
        self._fire_and_forget(self._db_upsert_user(guild_id, user_id, user))

        logger.info(
            f"XP manuell gesetzt: Guild {guild_id} User {user_id} -> "
            f"{amount} XP (Level {user['level']})"
        )

    # ------------------------------------------------------------------
    # Level-Berechnung
    # ------------------------------------------------------------------

    def _check_level_up(self, user: dict[str, Any]) -> bool:
        """
        Pruefen ob der User ein Level-Up verdient hat.

        Erhoet das Level so lange, bis die XP nicht mehr ausreichen.

        Returns:
            True wenn mindestens ein Level-Up stattgefunden hat
        """
        leveled_up = False
        current_level = user.get("level", 0)
        xp = user.get("xp", 0)

        while xp >= self.xp_for_level(current_level):
            current_level += 1
            leveled_up = True

        if leveled_up:
            user["level"] = current_level

        return leveled_up

    # ------------------------------------------------------------------
    # Rollen-Belohnungen (per-Guild)
    # ------------------------------------------------------------------

    def get_role_reward(self, guild_id: int | str, level: int) -> int | None:
        """
        Rollen-ID fuer ein Level in einer Guild zurueckgeben.

        Returns:
            Discord-Rollen-ID oder None wenn keine Belohnung
        """
        rewards = self._gcfg(guild_id).get("role_rewards", {})
        role_id = rewards.get(str(level))
        if role_id is not None:
            return int(role_id)
        return None

    async def set_role_reward(
        self, guild_id: int | str, level: int, role_id: int
    ) -> None:
        """Rollen-Belohnung fuer ein Level setzen (nach guild_config)."""
        cfg = await self._ensure_guild_config(guild_id)
        rewards = dict(cfg.get("role_rewards", {}))
        rewards[str(level)] = int(role_id)
        cfg["role_rewards"] = rewards
        await GuildConfig.set(
            guild_id, "leveling.role_rewards", rewards, updated_by="command"
        )
        logger.info(
            f"Rollen-Belohnung gesetzt: Guild {guild_id} Level {level} -> Rolle {role_id}"
        )

    # ------------------------------------------------------------------
    # Channel-Multiplikatoren (per-Guild)
    # ------------------------------------------------------------------

    async def set_channel_multiplier(
        self, guild_id: int | str, channel_id: int, factor: float
    ) -> None:
        """
        XP-Multiplikator fuer einen Channel setzen (nach guild_config).

        factor == 1.0 entfernt den Eintrag (Standard).
        """
        cfg = await self._ensure_guild_config(guild_id)
        multipliers = dict(cfg.get("channel_multipliers", {}))

        if factor == 1.0:
            multipliers.pop(str(channel_id), None)
        else:
            multipliers[str(channel_id)] = factor

        cfg["channel_multipliers"] = multipliers
        await GuildConfig.set(
            guild_id, "leveling.channel_multipliers", multipliers, updated_by="command"
        )
        logger.info(
            f"Channel-Multiplikator gesetzt: Guild {guild_id} {channel_id} -> {factor}x"
        )

    def get_channel_multiplier(self, guild_id: int | str, channel_id: int) -> float:
        """XP-Multiplikator fuer einen Channel abfragen (Standard: 1.0)."""
        multipliers = self._gcfg(guild_id).get("channel_multipliers", {})
        return float(multipliers.get(str(channel_id), 1.0))

    # ------------------------------------------------------------------
    # No-XP-Channels (per-Guild)
    # ------------------------------------------------------------------

    def get_no_xp_channels(self, guild_id: int | str) -> list[str]:
        """Liste der Channel-IDs (als str) in denen keine XP vergeben wird."""
        return [str(c) for c in self._gcfg(guild_id).get("no_xp_channels", [])]

    def is_no_xp_channel(self, guild_id: int | str, channel_id: int | str) -> bool:
        """True wenn der Channel in der No-XP-Liste der Guild steht."""
        return str(channel_id) in set(self.get_no_xp_channels(guild_id))

    async def set_no_xp_channel(
        self, guild_id: int | str, channel_id: int, enabled: bool
    ) -> None:
        """
        Channel zur No-XP-Liste hinzufuegen (enabled=True) oder entfernen
        (enabled=False), nach guild_config + Live-Cache.
        """
        cfg = await self._ensure_guild_config(guild_id)
        current = [str(c) for c in cfg.get("no_xp_channels", [])]
        cid = str(channel_id)
        if enabled:
            if cid not in current:
                current.append(cid)
        else:
            current = [c for c in current if c != cid]
        cfg["no_xp_channels"] = current
        await GuildConfig.set(
            guild_id, "leveling.no_xp_channels", current, updated_by="command"
        )
        logger.info(
            f"No-XP-Channel {'+' if enabled else '-'} Guild {guild_id} Channel {channel_id}"
        )

    # ------------------------------------------------------------------
    # Level-Up-Karte (Bild-Hintergrund, per-Guild)
    # ------------------------------------------------------------------

    def is_card_enabled(self, guild_id: int | str) -> bool:
        """True wenn die Level-Up-Karte mit Bild-Hintergrund aktiv ist."""
        return bool(self._gcfg(guild_id).get("levelup_card_enabled", False))

    def get_card_bg(self, guild_id: int | str) -> str | None:
        """Dateiname des aktiven Karten-Hintergrunds (oder None)."""
        return self._gcfg(guild_id).get("levelup_card_bg")

    def get_card_accent(self, guild_id: int | str) -> str:
        """Hex-Akzentfarbe der Karte zurueckgeben (Standard: Gold)."""
        return self._gcfg(guild_id).get("levelup_card_accent", "#f1c40f")

    async def set_card_enabled(self, guild_id: int | str, enabled: bool) -> None:
        """Level-Up-Karte an- oder ausschalten (nach guild_config)."""
        cfg = await self._ensure_guild_config(guild_id)
        cfg["levelup_card_enabled"] = bool(enabled)
        await GuildConfig.set(
            guild_id, "leveling.levelup_card_enabled", bool(enabled), updated_by="command"
        )
        logger.info(f"Level-Up-Karte Guild {guild_id} enabled={enabled}")

    async def set_card_bg(self, guild_id: int | str, filename: str | None) -> None:
        """Dateinamen des Karten-Hintergrunds setzen (None = kein BG)."""
        cfg = await self._ensure_guild_config(guild_id)
        cfg["levelup_card_bg"] = filename
        await GuildConfig.set(
            guild_id, "leveling.levelup_card_bg", filename, updated_by="command"
        )
        logger.info(f"Level-Up-Karten-Hintergrund Guild {guild_id}: {filename}")

    async def set_card_accent(self, guild_id: int | str, hex_color: str) -> None:
        """Hex-Akzentfarbe der Karte setzen (nach guild_config)."""
        cfg = await self._ensure_guild_config(guild_id)
        cfg["levelup_card_accent"] = hex_color
        await GuildConfig.set(
            guild_id, "leveling.levelup_card_accent", hex_color, updated_by="command"
        )
        logger.info(f"Level-Up-Karten-Akzent Guild {guild_id}: {hex_color}")

    # ------------------------------------------------------------------
    # Leaderboard (guild-scoped)
    # ------------------------------------------------------------------

    def get_leaderboard(
        self, guild_id: int | str, limit: int | None = 10
    ) -> list[dict[str, Any]]:
        """
        Top-Spieler einer Guild nach XP sortiert (In-Memory).

        Args:
            guild_id: Discord-Guild-ID
            limit: Maximale Anzahl Eintraege (Standard: 10; None = alle,
                   z.B. fuer Pagination)

        Returns:
            Liste von Dicts (user_id, xp, level, total_messages, voice_minutes),
            nach XP absteigend
        """
        entries: list[dict[str, Any]] = []
        guild_users = self._data.get(str(guild_id), {})

        for uid, data in guild_users.items():
            entries.append({
                "user_id": int(uid),
                "xp": data.get("xp", 0),
                "level": data.get("level", 0),
                "total_messages": data.get("total_messages", 0),
                "voice_minutes": data.get("voice_minutes", 0),
            })

        entries.sort(key=lambda e: e["xp"], reverse=True)
        return entries if limit is None else entries[:limit]

    async def get_leaderboard_db(
        self, guild_id: int | str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """
        Top-Spieler einer Guild direkt aus SQLite (async Version).

        Faellt auf In-Memory-Daten zurueck bei DB-Fehler.
        """
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT user_id, xp, level, messages, voice_minutes "
                "FROM leveling "
                "WHERE guild_id = ? "
                "ORDER BY xp DESC "
                "LIMIT ?",
                (str(guild_id), limit),
            )
            rows = await cursor.fetchall()

            if not rows:
                # DB leer fuer diese Guild — Fallback auf In-Memory
                return self.get_leaderboard(guild_id, limit)

            entries: list[dict[str, Any]] = []
            for row in rows:
                entries.append({
                    "user_id": int(row["user_id"]),
                    "xp": row["xp"] or 0,
                    "level": row["level"] or 0,
                    "total_messages": row["messages"] or 0,
                    "voice_minutes": row["voice_minutes"] or 0,
                })
            return entries

        except Exception as e:
            logger.error(f"Leaderboard aus SQLite laden fehlgeschlagen: {e}")
            return self.get_leaderboard(guild_id, limit)

    # ------------------------------------------------------------------
    # Rank (guild-scoped)
    # ------------------------------------------------------------------

    def get_rank(self, guild_id: int | str, user_id: int | str) -> int:
        """
        Position eines Users im Guild-Leaderboard (In-Memory).

        Returns:
            1-basierte Position. len(guild_users)+1 wenn User nicht gefunden.
        """
        guild_users = self._data.get(str(guild_id), {})
        sorted_users = sorted(
            guild_users.items(),
            key=lambda item: item[1].get("xp", 0),
            reverse=True,
        )

        uid = str(user_id)
        for index, (key, _) in enumerate(sorted_users, start=1):
            if key == uid:
                return index

        return len(sorted_users) + 1

    async def get_rank_db(self, guild_id: int | str, user_id: int | str) -> int:
        """
        Position eines Users in der Guild direkt aus SQLite (async Version).

        Verwendet COUNT WHERE xp > user_xp (guild-scoped) fuer effiziente
        Berechnung. Faellt auf In-Memory-Daten zurueck bei DB-Fehler.
        """
        try:
            db = await get_db()
            gid = str(guild_id)
            uid = str(user_id)

            # Zuerst XP des Users in dieser Guild holen
            cursor = await db.execute(
                "SELECT xp FROM leveling WHERE guild_id = ? AND user_id = ?",
                (gid, uid),
            )
            row = await cursor.fetchone()

            if not row:
                # User nicht in der Guild — Fallback
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM leveling WHERE guild_id = ?",
                    (gid,),
                )
                count_row = await cursor.fetchone()
                total = count_row[0] if count_row else 0
                return total + 1

            user_xp = row["xp"] or 0

            # Anzahl der User in der Guild mit mehr XP zaehlen
            cursor = await db.execute(
                "SELECT COUNT(*) FROM leveling WHERE guild_id = ? AND xp > ?",
                (gid, user_xp),
            )
            count_row = await cursor.fetchone()
            rank = (count_row[0] if count_row else 0) + 1

            return rank

        except Exception as e:
            logger.error(f"Rang aus SQLite laden fehlgeschlagen: {e}")
            return self.get_rank(guild_id, user_id)

    @property
    def config(self) -> dict[str, Any]:
        """Globalen JSON-Default zurueckgeben (Kopie)."""
        return dict(self._config)

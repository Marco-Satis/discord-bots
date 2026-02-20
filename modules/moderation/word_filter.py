"""
Discord Word Filter — Wortfilter fuer Discord-Nachrichten (Admin Bot)

Filtert unangemessene Woerter in Discord-Channels.
Unterstuetzt einfache Woerter und regex:-Muster.

NICHT fuer RCON/In-Game — dafuer existiert modules/word_filter.py
"""

import re
import json
import aiofiles
from pathlib import Path
from typing import Optional

from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR

logger = get_logger("moderation.word_filter")

# Standard-Persistenzdatei fuer den Discord-Wortfilter
DEFAULT_FILTER_FILE = ADMIN_DATA_DIR / "discord_word_filter.json"


class DiscordWordFilter:
    """
    Wortfilter fuer Discord-Nachrichten im Admin Bot.

    Unterstuetzt zwei Arten von Eintraegen:
    - Einfache Woerter: Case-insensitive Substring-Match
    - Regex-Muster: Prefix ``regex:`` gefolgt vom regulaeren Ausdruck

    Beispiel-Config::

        {
            "word_filter": {
                "enabled": true,
                "words": ["badword", "regex:\\\\bd[i1]ck\\\\b"]
            }
        }
    """

    def __init__(self, config: dict) -> None:
        """
        Filter aus Config initialisieren.

        Args:
            config: Bot-Konfiguration (wird nach ``word_filter.words`` durchsucht)
        """
        wf_config = config.get("word_filter", {})
        self.enabled: bool = wf_config.get("enabled", True)
        self._raw_words: list[str] = list(wf_config.get("words", []))
        self._compiled: list[tuple[re.Pattern, str]] = []
        self._filepath: Path = DEFAULT_FILTER_FILE

        # Patterns kompilieren
        self._compile_patterns()

    # ------------------------------------------------------------------
    # Pattern-Kompilierung
    # ------------------------------------------------------------------

    def _compile_patterns(self) -> None:
        """Wortliste in kompilierte Regex-Patterns umwandeln"""
        self._compiled = []
        for word in self._raw_words:
            if not word:
                continue

            try:
                if word.startswith("regex:"):
                    # Regex-Muster: alles nach "regex:" als Pattern verwenden
                    pattern_str = word[len("regex:"):]
                    pattern = re.compile(pattern_str, re.IGNORECASE)
                else:
                    # Einfaches Wort: Case-insensitive Substring-Match
                    pattern = re.compile(re.escape(word), re.IGNORECASE)

                self._compiled.append((pattern, word))
            except re.error as e:
                logger.warning(f"Ungueltiges Filter-Pattern '{word}': {e}")

    # ------------------------------------------------------------------
    # Nachrichtenpruefung
    # ------------------------------------------------------------------

    def check_message(self, content: str) -> tuple[bool, str | None]:
        """
        Prueft ob eine Nachricht gefilterte Woerter enthaelt.

        Args:
            content: Nachrichteninhalt

        Returns:
            (is_filtered, matched_word) — True wenn die Nachricht blockiert werden soll,
            zusammen mit dem gematchten Wort/Pattern
        """
        if not self.enabled:
            return False, None

        if not content:
            return False, None

        for pattern, original_word in self._compiled:
            if pattern.search(content):
                logger.info(
                    f"Wortfilter ausgeloest: '{original_word}' in Nachricht gefunden"
                )
                return True, original_word

        return False, None

    # ------------------------------------------------------------------
    # Wortliste verwalten
    # ------------------------------------------------------------------

    async def add_word(self, word: str) -> bool:
        """
        Wort zur Filterliste hinzufuegen.

        Args:
            word: Wort oder ``regex:<pattern>`` zum Hinzufuegen

        Returns:
            True wenn erfolgreich, False wenn bereits vorhanden
        """
        # Duplikat-Pruefung (case-insensitive fuer einfache Woerter)
        for existing in self._raw_words:
            if existing.lower() == word.lower():
                return False

        self._raw_words.append(word)
        self._compile_patterns()
        await self._save()
        logger.info(f"Filterwort hinzugefuegt: {word}")
        return True

    async def remove_word(self, word: str) -> bool:
        """
        Wort aus der Filterliste entfernen.

        Args:
            word: Wort das entfernt werden soll

        Returns:
            True wenn entfernt, False wenn nicht gefunden
        """
        original_len = len(self._raw_words)
        self._raw_words = [
            w for w in self._raw_words
            if w.lower() != word.lower()
        ]

        if len(self._raw_words) < original_len:
            self._compile_patterns()
            await self._save()
            logger.info(f"Filterwort entfernt: {word}")
            return True

        return False

    def get_words(self) -> list[str]:
        """Aktuelle Wortliste zurueckgeben"""
        return self._raw_words.copy()

    def toggle(self) -> bool:
        """
        Filter ein-/ausschalten.

        Returns:
            Neuer Zustand (True = aktiv)
        """
        self.enabled = not self.enabled
        logger.info(f"Wortfilter {'aktiviert' if self.enabled else 'deaktiviert'}")
        return self.enabled

    @property
    def count(self) -> int:
        """Anzahl der Filterwoerter"""
        return len(self._raw_words)

    # ------------------------------------------------------------------
    # Persistenz
    # ------------------------------------------------------------------

    async def _save(self) -> None:
        """Aktuelle Filterliste in JSON-Datei speichern"""
        data = {
            "enabled": self.enabled,
            "words": self._raw_words,
        }
        try:
            self._filepath.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self._filepath, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Filterliste speichern fehlgeschlagen: {e}")

    async def load(self) -> None:
        """
        Filterliste aus JSON-Datei laden (ueberschreibt Config-Werte).

        Wird beim Cog-Start aufgerufen um persistente Aenderungen
        (via /filter add/remove) wiederherzustellen.
        """
        try:
            if self._filepath.exists():
                async with aiofiles.open(self._filepath, "r", encoding="utf-8") as f:
                    content = await f.read()
                    data = json.loads(content)
                    self._raw_words = data.get("words", self._raw_words)
                    self.enabled = data.get("enabled", self.enabled)
                    self._compile_patterns()
                    logger.info(
                        f"Filterliste geladen: {len(self._raw_words)} Woerter "
                        f"(aktiv: {self.enabled})"
                    )
        except Exception as e:
            logger.error(f"Filterliste laden fehlgeschlagen: {e}")

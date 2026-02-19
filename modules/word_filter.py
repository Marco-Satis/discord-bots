"""
Word Filter for Chat Bridge + Broadcast
Filters inappropriate words using configurable word list.
Supports exact match, partial match, and regex patterns.
"""

import re
import json
import aiofiles
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from utils.logger import get_logger
from utils.config import DATA_DIR

logger = get_logger("word_filter")

DEFAULT_FILTER_FILE = DATA_DIR / "word_filter_list.json"

# Default word list (German + English slurs, insults, offensive terms)
# This is a starting point - admins can customize via the JSON file
DEFAULT_WORDS = [
    # German offensive
    "hurensohn", "wichser", "fotze", "schlampe", "missgeburt",
    "spast", "mongo", "behindert", "schwuchtel", "kanake",
    "neger", "zigeuner", "vollidiot", "drecksau", "arschloch",
    "fick dich", "halt die fresse", "verpiss dich",
    # English offensive
    "nigger", "nigga", "faggot", "retard", "kys",
    "kill yourself", "nazi", "hitler did nothing",
    # Spam/Scam patterns
    "free nitro", "discord.gift", "steamcommunity.ru",
]


class WordFilter:
    """
    Configurable word filter with multiple match modes.
    
    Match modes:
    - exact: Full word match (default)
    - partial: Substring match  
    - regex: Regular expression
    """

    def __init__(self, filepath: Optional[Path] = None) -> None:
        self.filepath = filepath or DEFAULT_FILTER_FILE
        self._words: List[Dict[str, Any]] = []
        self._compiled: List[Tuple[re.Pattern, Dict[str, Any]]] = []
        self.enabled = True

    async def load(self) -> None:  # type: ignore
        """Load word filter list from JSON file"""
        try:
            if self.filepath.exists():
                async with aiofiles.open(self.filepath, "r", encoding="utf-8") as f:
                    content = await f.read()
                    data = json.loads(content)
                    self._words = data.get("words", [])
                    self.enabled = data.get("enabled", True)
            else:
                # Create default file
                await self._create_default()

            self._compile_patterns()
            logger.info(f"Word filter loaded: {len(self._words)} patterns")

        except Exception as e:
            logger.error(f"Failed to load word filter: {e}")
            # Use defaults
            self._words = [{"word": w, "mode": "partial"} for w in DEFAULT_WORDS]
            self._compile_patterns()

    async def _create_default(self) -> None:  # type: ignore
        """Create default word filter file"""
        data = {
            "enabled": True,
            "words": [
                {"word": w, "mode": "partial"} for w in DEFAULT_WORDS
            ]
        }
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(self.filepath, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        self._words = data["words"]

    async def save(self) -> None:  # type: ignore
        """Save current word list to file"""
        data = {
            "enabled": self.enabled,
            "words": self._words
        }
        try:
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self.filepath, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Failed to save word filter: {e}")

    def _compile_patterns(self) -> None:  # type: ignore
        """Compile word list into regex patterns for efficient matching"""
        self._compiled = []
        for entry in self._words:
            word = entry.get("word", "")
            mode = entry.get("mode", "partial")

            if not word:
                continue

            try:
                if mode == "regex":
                    pattern = re.compile(word, re.IGNORECASE)
                elif mode == "exact":
                    pattern = re.compile(
                        r'\b' + re.escape(word) + r'\b', re.IGNORECASE
                    )
                else:  # partial (default)
                    pattern = re.compile(re.escape(word), re.IGNORECASE)

                self._compiled.append((pattern, entry))
            except re.error as e:
                logger.warning(f"Invalid filter pattern '{word}': {e}")

    def check(self, message: str) -> Tuple[bool, Optional[str]]:
        """
        Check if message contains filtered words.

        Returns:
            (is_filtered, matched_word) - True if message should be blocked
        """
        if not self.enabled:
            return False, None

        for pattern, entry in self._compiled:
            match = pattern.search(message)
            if match:
                return True, entry.get("word", "unknown")

        return False, None

    def filter_message(self, message: str, replacement: str = "***") -> str:  # type: ignore
        """
        Replace filtered words with replacement string.

        Returns the filtered message.
        """
        if not self.enabled:
            return message

        filtered = message
        for pattern, _ in self._compiled:
            filtered = pattern.sub(replacement, filtered)

        return filtered

    def is_clean(self, message: str) -> bool:  # type: ignore
        """Quick check if message is clean (no filtered words)"""
        is_filtered, _ = self.check(message)
        return not is_filtered

    async def add_word(self, word: str, mode: str = "partial") -> bool:  # type: ignore
        """Add a word to the filter list"""
        # Check for duplicates
        for entry in self._words:
            if entry["word"].lower() == word.lower():
                return False

        self._words.append({"word": word, "mode": mode})
        self._compile_patterns()
        await self.save()
        logger.info(f"Filter word added: {word} (mode: {mode})")
        return True

    async def remove_word(self, word: str) -> bool:  # type: ignore
        """Remove a word from the filter list"""
        original_len = len(self._words)
        self._words = [
            e for e in self._words
            if e["word"].lower() != word.lower()
        ]
        if len(self._words) < original_len:
            self._compile_patterns()
            await self.save()
            logger.info(f"Filter word removed: {word}")
            return True
        return False

    def get_words(self) -> List[Dict[str, Any]]:  # type: ignore
        """Get all filter words"""
        return self._words.copy()

    def count(self) -> int:  # type: ignore
        return len(self._words)

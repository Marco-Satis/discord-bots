"""
Steam Changelog — Phase 7
Fetches patch notes from Steam News API when an update is detected.
"""

import aiohttp
import html
import re
from datetime import datetime
from typing import Optional, Dict, List, Any

from utils.logger import get_logger

logger = get_logger("steam_changelog")

SATISFACTORY_APP_ID = 526870
STEAM_NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/"


class SteamChangelog:
    """
    Fetches Satisfactory patch notes from Steam News API.
    Used when update_checker detects a new version.
    """

    def __init__(self, app_id: int = SATISFACTORY_APP_ID) -> None:
        self.app_id: int = app_id

    async def get_latest_patchnotes(self, count: int = 3) -> List[Dict[str, Any]]:
        """
        Fetch the latest news/patch notes from Steam.
        Returns list of {"title", "url", "date", "content_short", "author", "feedlabel"}
        """
        try:
            params = {
                "appid": self.app_id,
                "count": count,
                "maxlength": 500,
                "format": "json",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(STEAM_NEWS_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.warning(f"Steam News API returned {resp.status}")
                        return []

                    data = await resp.json()

            news_items = data.get("appnews", {}).get("newsitems", [])
            results = []

            for item in news_items:
                # Clean up HTML/BBCode from content
                content = item.get("contents", "")
                content = self._clean_content(content)

                results.append({
                    "title": item.get("title", "Unbekannt"),
                    "url": item.get("url", ""),
                    "date": datetime.fromtimestamp(item.get("date", 0)).strftime("%d.%m.%Y %H:%M"),
                    "content_short": content[:400] + ("..." if len(content) > 400 else ""),
                    "author": item.get("author", ""),
                    "feedlabel": item.get("feedlabel", ""),
                    "feed_type": item.get("feed_type", 0),
                })

            return results

        except Exception as e:
            logger.error(f"Steam News API error: {e}")
            return []

    async def get_update_changelog(self) -> Optional[Dict[str, Any]]:
        """
        Get the most recent patch note suitable for an update notification.
        Prefers "steam_community_announcements" feed.
        """
        notes = await self.get_latest_patchnotes(count=5)
        if not notes:
            return None

        # Prefer official patch notes / announcements
        for note in notes:
            feed = note.get("feedlabel", "").lower()
            title = note.get("title", "").lower()
            if any(kw in title for kw in ["patch", "update", "hotfix", "release", "changelog"]):
                return note
            if "announcement" in feed or "community" in feed:
                return note

        # Fallback: just return the newest
        return notes[0] if notes else None

    @staticmethod
    def _clean_content(text: str) -> str:  # Already typed
        """Remove HTML and BBCode tags from content"""
        # Unescape HTML entities
        text = html.unescape(text)
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Remove BBCode tags
        text = re.sub(r'\[/?[^\]]+\]', '', text)
        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()
        return text

    def format_embed_description(self, note: Dict[str, Any]) -> str:
        """Format a note for Discord embed description"""
        parts = []

        if note.get("date"):
            parts.append(f"📅 {note['date']}")
        if note.get("author"):
            parts.append(f"✍️ {note['author']}")
        parts.append("")

        if note.get("content_short"):
            parts.append(note["content_short"])

        if note.get("url"):
            parts.append(f"\n🔗 [Vollständige Patchnotes]({note['url']})")

        return "\n".join(parts)

"""
Satisfactory Save File Header Parser (Shared Module)
Used by both SavegameStats and SavegameAnalyzer.

Satisfactory save format header:
  - Save Header Version (int32)
  - Save Version (int32)
  - Build Version (int32)
  - Map Name (string)
  - Map Options (string)
  - Session Name (string)
  - Play Duration (int32, seconds)
  - Save Date (int64, Windows FILETIME ticks)
"""

import struct
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, BinaryIO, Any
from dataclasses import dataclass

from utils.logger import get_logger

logger = get_logger("satisfactory.save_header")


@dataclass
class SaveHeader:
    """Parsed save file header"""
    header_version: int = 0
    save_version: int = 0
    build_version: int = 0
    map_name: str = ""
    session_name: str = ""
    play_seconds: int = 0
    play_hours: float = 0.0
    save_date: str = ""

    @property
    def is_valid(self) -> bool:
        return self.header_version > 0


def read_length_prefixed_string(f: BinaryIO) -> str:
    """Read a length-prefixed string from a binary file (length-prefixed UTF-8)."""
    try:
        length = struct.unpack("<i", f.read(4))[0]
        if length <= 0 or length > 65536:
            return ""
        data = f.read(length)
        try:
            return data.rstrip(b'\x00').decode("utf-8")
        except UnicodeDecodeError:
            return data.rstrip(b'\x00').decode("latin-1", errors="replace")
    except (struct.error, ValueError, OSError) as e:
        logger.debug(f"Failed to read length-prefixed string: {e}")
        return ""


def read_save_header(save_file: Path) -> Optional[SaveHeader]:
    """
    Read and parse the header of a Satisfactory save file.

    Returns SaveHeader on success, None on failure.
    """
    try:
        header = SaveHeader()

        with open(save_file, "rb") as f:
            header.header_version = struct.unpack("<i", f.read(4))[0]
            header.save_version = struct.unpack("<i", f.read(4))[0]
            header.build_version = struct.unpack("<i", f.read(4))[0]

            header.map_name = read_length_prefixed_string(f)
            _map_options = read_length_prefixed_string(f)  # skip
            header.session_name = read_length_prefixed_string(f)

            header.play_seconds = struct.unpack("<i", f.read(4))[0]
            header.play_hours = round(header.play_seconds / 3600, 1)

            # Save date (Windows FILETIME ticks)
            save_ticks = struct.unpack("<q", f.read(8))[0]
            if save_ticks > 0:
                unix_ticks = save_ticks - 621355968000000000
                if unix_ticks > 0:
                    save_dt = datetime.fromtimestamp(unix_ticks / 10000000)
                    header.save_date = save_dt.strftime("%d.%m.%Y %H:%M")

        return header if header.is_valid else None

    except (struct.error, ValueError, OSError) as e:
        logger.debug(f"Header parse failed for {save_file}: {e}")
        return None


def header_to_dict(header: SaveHeader) -> Dict[str, Any]:
    """Convert SaveHeader to dict for API compatibility."""
    return {
        "header_version": header.header_version,
        "save_version": header.save_version,
        "build_version": header.build_version,
        "map_name": header.map_name,
        "session_name": header.session_name,
        "play_seconds": header.play_seconds,
        "play_hours": header.play_hours,
        "save_date": header.save_date,
    }

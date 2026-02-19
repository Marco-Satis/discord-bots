"""
Crash Replay - Captures server log context around crashes
Saves the last N lines before a crash and posts them to Discord.
"""

import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any
from collections import deque

from utils.logger import get_logger

logger = get_logger("crash_replay")


class CrashReplay:
    """
    Captures and stores server log context when crashes occur.

    Continuously tracks the tail of the server log so that when a crash
    is detected, the last N lines are immediately available.
    """

    def __init__(self, log_path: Path, replay_dir: Path,
                 context_lines: int = 50, max_replays: int = 20) -> None:
        """
        Args:
            log_path: Path to FactoryGame.log
            replay_dir: Directory to store crash replay files
            context_lines: Number of log lines to capture before crash
            max_replays: Maximum number of replay files to keep
        """
        self.log_path: Path = log_path
        self.replay_dir: Path = replay_dir
        self.context_lines: int = context_lines
        self.max_replays: int = max_replays

        # Ring buffer for recent log lines
        self._buffer: deque = deque(maxlen=context_lines)
        self._last_pos: int = 0
        self._last_size: int = 0

        # Ensure replay directory exists
        self.replay_dir.mkdir(parents=True, exist_ok=True)

    def init_position(self) -> None:
        """Seek to current end of log"""
        try:
            if self.log_path.exists():
                self._last_size = self.log_path.stat().st_size
                # Read last N lines to pre-fill buffer
                self._prefill_buffer()
                logger.info(
                    f"Crash replay initialized, buffer: {len(self._buffer)} lines"
                )
        except OSError as e:
            logger.error(f"Failed to init crash replay: {e}")

    def _prefill_buffer(self) -> None:
        """Read the last context_lines from the log to pre-fill the buffer"""
        try:
            with open(self.log_path, 'r', encoding='utf-8', errors='replace') as f:
                # Read all lines (not ideal for huge logs, but safe for startup)
                # For very large logs, seek backwards
                f.seek(0, 2)  # End of file
                file_size = f.tell()

                # Read last ~100KB which should contain enough lines
                read_size = min(file_size, 100 * 1024)
                f.seek(max(0, file_size - read_size))

                if file_size > read_size:
                    f.readline()  # Skip partial first line

                lines = f.readlines()
                for line in lines[-self.context_lines:]:
                    self._buffer.append(line.rstrip('\n'))

                self._last_pos = f.tell()
                self._last_size = file_size
        except (OSError, UnicodeDecodeError) as e:
            logger.error(f"Failed to prefill buffer: {e}")

    async def update_buffer(self) -> None:
        """
        Read new log lines into the ring buffer.
        Should be called periodically (e.g. every health check).
        """
        if not self.log_path.exists():
            return

        try:
            current_size = self.log_path.stat().st_size

            # Log rotated?
            if current_size < self._last_size:
                self._last_pos = 0
                self._buffer.clear()
            self._last_size = current_size

            if current_size <= self._last_pos:
                return

            def _read():
                with open(self.log_path, 'r', encoding='utf-8',
                          errors='replace') as f:
                    f.seek(self._last_pos)
                    content = f.read()
                    return content, f.tell()

            content, new_pos = await asyncio.get_event_loop().run_in_executor(
                None, _read
            )
            self._last_pos = new_pos

            if content:
                for line in content.splitlines():
                    if line.strip():
                        self._buffer.append(line)

        except Exception as e:
            logger.debug(f"Buffer update error: {e}")

    async def capture(self, crash_number: int) -> Optional[Path]:
        """
        Capture current buffer to a crash replay file.

        Args:
            crash_number: The crash event number

        Returns:
            Path to the saved replay file, or None on failure
        """
        if not self._buffer:
            logger.warning("No log lines in buffer to capture")
            return None

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"crash_{crash_number:03d}_{timestamp}.log"
            filepath = self.replay_dir / filename

            header = [
                f"{'='*60}",
                f"CRASH REPLAY #{crash_number}",
                f"Zeitpunkt: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
                f"Letzte {len(self._buffer)} Log-Zeilen vor dem Crash:",
                f"{'='*60}",
                "",
            ]

            def _write_file():
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(header))
                    f.write('\n'.join(self._buffer))
                    f.write('\n')

            await asyncio.get_event_loop().run_in_executor(None, _write_file)

            logger.info(
                f"Crash replay saved: {filename} "
                f"({len(self._buffer)} lines)"
            )

            # Rotate old replays
            self._rotate()

            return filepath

        except Exception as e:
            logger.error(f"Failed to save crash replay: {e}")
            return None

    def get_summary(self) -> str:
        """
        Get a short summary from the buffer focusing on error lines.
        Returns the last few error/warning lines for the Discord embed.
        """
        if not self._buffer:
            return "Keine Log-Daten verfügbar"

        # Find error/warning lines
        error_lines = []
        for line in self._buffer:
            lower = line.lower()
            if any(kw in lower for kw in [
                "error", "fatal", "crash", "assert",
                "exception", "critical", "abort"
            ]):
                # Clean up the line (remove timestamp prefix if too long)
                clean = line.strip()
                if len(clean) > 200:
                    clean = clean[:200] + "..."
                error_lines.append(clean)

        if error_lines:
            # Return last 5 error lines
            return "\n".join(error_lines[-5:])

        # No errors found, return last 5 lines
        last_lines = list(self._buffer)[-5:]
        return "\n".join(
            (l[:200] + "..." if len(l) > 200 else l) for l in last_lines
        )

    def _rotate(self) -> None:
        """Remove old replay files if over max_replays"""
        try:
            replays = sorted(
                self.replay_dir.glob("crash_*.log"),
                key=lambda p: p.stat().st_mtime
            )
            while len(replays) > self.max_replays:
                oldest = replays.pop(0)
                oldest.unlink()
                logger.debug(f"Rotated old crash replay: {oldest.name}")
        except OSError as e:
            logger.error(f"Replay rotation error: {e}")

    def list_replays(self) -> List[Dict[str, Any]]:
        """List available crash replay files"""
        replays = []
        try:
            for f in sorted(
                self.replay_dir.glob("crash_*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            ):
                replays.append({
                    "filename": f.name,
                    "path": str(f),
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "date": datetime.fromtimestamp(
                        f.stat().st_mtime
                    ).strftime("%d.%m.%Y %H:%M"),
                })
        except OSError:
            pass
        return replays

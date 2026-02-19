"""
Formatting helpers for Discord embeds and messages
"""

from datetime import datetime, timedelta


def format_uptime(seconds):
    """Format seconds into human-readable uptime string"""
    if seconds <= 0:
        return "Offline"
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    parts = []
    if d > 0:
        parts.append(f"{d}d")
    if h > 0:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


def format_bytes(num_bytes):
    """Format bytes into human-readable string"""
    if num_bytes is None:
        return "N/A"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def format_timestamp(dt=None):
    """Format datetime for German locale"""
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%d.%m.%Y %H:%M:%S")


def format_duration_minutes(minutes):
    """Format minutes into readable duration"""
    if minutes < 60:
        return f"{minutes} Min"
    h = minutes // 60
    m = minutes % 60
    if m == 0:
        return f"{h}h"
    return f"{h}h {m}m"


def truncate(text, max_len=1024):
    """Truncate text to max length with ellipsis"""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def status_emoji(online):
    """Return status indicator"""
    return "\U0001f7e2" if online else "\U0001f534"


def progress_bar(current, maximum, length=10):
    """Create a text progress bar"""
    if maximum <= 0:
        return "[" + "-" * length + "]"
    ratio = min(current / maximum, 1.0)
    filled = int(ratio * length)
    empty = length - filled
    return "[" + "#" * filled + "-" * empty + f"] {ratio*100:.0f}%"

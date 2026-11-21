"""Shared text formatting and progress utilities."""

import time
from typing import Any, Dict, Optional


def render_progress_bar(current: float, target: float, length: int = 8) -> str:
    """Format visual progress bar like [■■■■□□□□]."""
    if target <= 0:
        return "[" + "■" * length + "]"
    ratio = min(1.0, max(0.0, current / target))
    filled = round(ratio * length)
    empty = length - filled
    return f"[{'■' * filled}{'□' * empty}]"


def calculate_timer_spent_seconds(timer: Dict[str, Any], now_ts: Optional[int] = None) -> int:
    """Calculate total active seconds spent in this session."""
    current_ts = now_ts if now_ts is not None else int(time.time())
    if timer.get("is_paused"):
        return timer.get("elapsed_before_pause", 0)
    return timer.get("elapsed_before_pause", 0) + max(0, current_ts - timer.get("started_at", current_ts))


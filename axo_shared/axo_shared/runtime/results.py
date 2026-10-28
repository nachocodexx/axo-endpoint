"""How long a job's result is kept: one of these choices, picked per job
(or per function / choreography), never above the VE's longest time."""

from __future__ import annotations

from typing import Dict, Optional

NEVER = "never"
# In order, shortest first.
KEEP_RESULT_SECONDS: Dict[str, Optional[float]] = {
    "1h": 3600.0,
    "1d": 86400.0,
    "7d": 7 * 86400.0,
    "30d": 30 * 86400.0,
    "90d": 90 * 86400.0,
    "1y": 365 * 86400.0,
    NEVER: None,
}
KEEP_RESULT_CHOICES = tuple(KEEP_RESULT_SECONDS)


class KeepResultError(ValueError):
    pass


def check_keep_result(choice: str) -> str:
    if choice not in KEEP_RESULT_SECONDS:
        raise KeepResultError(f"keep_result must be one of {', '.join(KEEP_RESULT_CHOICES)}")
    return choice


def keep_seconds(choice: str) -> Optional[float]:
    """Seconds, or None for "never"."""
    return KEEP_RESULT_SECONDS[check_keep_result(choice)]


def longer(a: str, b: str) -> bool:
    """True when choice ``a`` keeps results longer than ``b``."""
    return KEEP_RESULT_CHOICES.index(check_keep_result(a)) > KEEP_RESULT_CHOICES.index(check_keep_result(b))


def capped(choice: str, longest: str) -> str:
    """``choice``, or ``longest`` when it asks for more."""
    return longest if longer(choice, longest) else check_keep_result(choice)

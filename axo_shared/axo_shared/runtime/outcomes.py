"""Whose fault a finished attempt was -- the one rule the endpoint (billing
record) and the VEM (job pages) share. Anything not listed is the
platform's fault (worker / container crash, spawn failure, ...) and is
never billed."""

from __future__ import annotations

from typing import Optional

COMPLETED = "completed"
FAILED_USER = "failed_user"              # your code failed (billed)
FAILED_PLATFORM = "failed_platform"      # the platform failed (not billed)
TIMED_OUT_IN_QUEUE = "timed_out_in_queue"  # waited too long for a runner (not billed)
CANCELLED = "cancelled"                  # stopped by a person (billed for the time it ran)
OUTCOMES = (COMPLETED, FAILED_USER, FAILED_PLATFORM, TIMED_OUT_IN_QUEUE, CANCELLED)

FAILED_USER_CODES = frozenset({
    3002,  # FunctionExecError: the function itself raised
    3007,  # JobTimeoutError: the function's own max_duration_seconds elapsed
    9404,  # ActiveObjectStateTooLargeError: the call grew the state past the plan's cap
    9407,  # ActiveObjectAttributeInvalidError: the call set a value that can't be saved
})
CANCELLED_CODES = frozenset({3008})            # JobCancelledError
TIMED_OUT_IN_QUEUE_CODES = frozenset({3009})   # QueueWaitTimeoutError


def outcome_of(error_code: Optional[int]) -> str:
    """The outcome of an attempt that ended with this error code (None = success)."""
    if error_code is None:
        return COMPLETED
    if error_code in FAILED_USER_CODES:
        return FAILED_USER
    if error_code in CANCELLED_CODES:
        return CANCELLED
    if error_code in TIMED_OUT_IN_QUEUE_CODES:
        return TIMED_OUT_IN_QUEUE
    return FAILED_PLATFORM

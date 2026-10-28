"""How a worker or runner reports an error the function's code raised: its
Python type, message and traceback (only the function's own lines -- the
platform's frames above it are left out, and a long one keeps its last
TRACEBACK_MAX_LINES lines, where the error is), as one JSON text that the
endpoint turns back into a FunctionExecError."""

from __future__ import annotations

import json
import traceback as tb
from typing import Any, Dict, Optional

from axo_endpoint.core.errors import FunctionExecError

TRACEBACK_MAX_LINES = 200
# Frames from these files are the platform calling the function, not its code.
_PLATFORM_PATHS = ("/axo_endpoint/", "/axo/runtime/", "/axo/core/axo.py", "/axo/core/decorators.py")


def report_exception(exc: BaseException) -> str:
    frames = tb.extract_tb(exc.__traceback__)
    first_own = next((i for i, f in enumerate(frames) if not any(p in f.filename for p in _PLATFORM_PATHS)),
                     len(frames))
    lines = ["Traceback (most recent call last):\n"] + tb.format_list(frames[first_own:]) + \
        tb.format_exception_only(type(exc), exc)
    text = "".join(lines).splitlines()
    if len(text) > TRACEBACK_MAX_LINES:
        text = [f"... ({len(text) - TRACEBACK_MAX_LINES} earlier lines left out)"] + text[-TRACEBACK_MAX_LINES:]
    return json.dumps({"error_type": type(exc).__name__, "message": str(exc), "traceback": "\n".join(text)})


def exec_error(payload: Any) -> FunctionExecError:
    """The FunctionExecError for a worker's report (or a plain text from an
    older worker). Its context keeps error_type and traceback."""
    report = _parse(payload)
    if report is None:
        return FunctionExecError(str(payload))
    kind, message = report.get("error_type") or "Exception", report.get("message") or ""
    return FunctionExecError(f"{kind}: {message}" if message else kind,
                             context={"error_type": kind, "traceback": report.get("traceback")})


def _parse(payload: Any) -> Optional[Dict[str, Any]]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if not isinstance(payload, str) or not payload.startswith("{"):
        return None
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    return data if isinstance(data, dict) and "error_type" in data else None

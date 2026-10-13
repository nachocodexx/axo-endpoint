from __future__ import annotations

import json
from typing import Any, Dict

from kurrentdbclient import KurrentDBClient, NewEvent, StreamState

from axo_control_plane.application.ports import HistoryLog


class KurrentHistory(HistoryLog):
    """History of everything that happened, one Kurrent stream per thing
    (``ve-<id>``, ``team-<id>``, ``usage-<ve id>``, ...). Append-only; the
    current state lives in Mongo."""

    def __init__(self, client: KurrentDBClient) -> None:
        self._client = client

    def append(self, stream: str, event_type: str, data: Dict[str, Any]) -> None:
        self._client.append_to_stream(
            stream,
            events=NewEvent(type=event_type, data=json.dumps(data, default=str).encode("utf-8")),
            current_version=StreamState.ANY,
        )

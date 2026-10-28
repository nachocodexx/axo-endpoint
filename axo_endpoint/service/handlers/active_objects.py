from __future__ import annotations

from typing import Callable

from axo_shared.protocol import Command, CommandHandler, CommandResult


class ActiveObjectHandler(CommandHandler):
    """One active object operation, answered by the leader's ObjectCoordinator
    (``ACTIVE_OBJECT_CALL`` / ``_CLONE`` / ``_DELETE`` / ``_STATE`` /
    ``_RESTORE``; each wrapped in a LeaderProxyHandler)."""

    def __init__(self, handle_fn: Callable[[Command], CommandResult]) -> None:
        self._handle = handle_fn

    def handle(self, command: Command) -> CommandResult:
        return self._handle(command)

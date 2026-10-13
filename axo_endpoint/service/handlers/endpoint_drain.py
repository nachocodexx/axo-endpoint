from __future__ import annotations

import time
from typing import Callable, Union

from axo_shared.protocol import Command, CommandHandler, CommandResult

from axo_endpoint.log import DumbLogger, Log
from axo_endpoint.log.catalog import Component, Event

_Logger = Union[Log, DumbLogger]


class EndpointDrainHandler(CommandHandler):
    """ENDPOINT_DRAIN from the VEM (scale-down, VE stop): from now on this
    endpoint takes no new jobs -- JOB_SUBMIT/JOB_FORWARD are refused and its
    heartbeat tells the cluster ledger to place work elsewhere -- while the
    jobs it already has keep running. Idempotent: the VEM sends it again to
    poll ``active_jobs`` until 0. ``draining: false`` undoes it."""

    def __init__(
        self,
        set_draining: Callable[[bool], None],
        active_job_count: Callable[[], int],
        logger: _Logger = None,
    ) -> None:
        self._set_draining = set_draining
        self._active_job_count = active_job_count
        self._logger: _Logger = logger or DumbLogger()

    def handle(self, command: Command) -> CommandResult:
        t0 = time.monotonic()
        draining = command.envelope.get("draining", True) is not False
        self._set_draining(draining)
        active = self._active_job_count()
        self._logger.info_event(
            Event.App.DRAIN_REQUESTED,
            component=Component.APP,
            draining=draining,
            active_jobs=active,
            duration_ms=round((time.monotonic() - t0) * 1000, 2),
        )
        return CommandResult(ok=True, metadata={"draining": draining, "active_jobs": active})

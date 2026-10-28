"""The runner's start-up status page.

Before the function is loaded (fetching its code, installing its packages,
loading it) the real servers aren't up yet. This small HTTP server answers
``GET /health`` on the same port meanwhile, always ``503`` with
``{"stage": ..., "error": ...}``, so the endpoint can tell a runner that is
alive and installing from one that never started: the endpoint's short
start-up limit only covers getting this far, the install gets its own
(longer) limit, and a failed install is reported at once with pip's message
instead of running out the clock. It's stopped right before the real
servers take the port.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

FETCHING = "fetching"
INSTALLING = "installing"
LOADING = "loading"
FAILED = "failed"

# Only the end of a long error is sent (pip prints a lot before the cause).
_MAX_ERROR_CHARS = 4000


class StartupStatus:
    def __init__(self, port: int) -> None:
        self._stage = FETCHING
        self._error: Optional[str] = None
        status = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = json.dumps({"stage": status._stage, "error": status._error}).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> "StartupStatus":
        self._thread.start()
        return self

    def set_stage(self, stage: str) -> None:
        self._stage = stage

    def fail(self, error: str) -> None:
        self._error = error[-_MAX_ERROR_CHARS:]
        self._stage = FAILED

    def stop(self) -> None:
        """Frees the port for the real servers."""
        self._server.shutdown()
        self._server.server_close()

"""Container runner entrypoint.

Expected environment variables:
  AXO_ENDPOINT_ADDRESS   - ZMQ address of the endpoint ROUTER (e.g. tcp://axo-endpoint:5555)
  AXO_RESULT_ADDRESS     - ZMQ address of the endpoint PULL socket (e.g. tcp://axo-endpoint:5557)
  AXO_FUNCTION_ID        - Registered function's content-hash id
  AXO_FUNCTION_VERSION   - Function version (int)
  AXO_JOB_PORT           - Port for the ZMQ ROUTER that receives jobs (default: 5600)
  AXO_FASTAPI_PORT       - Port for the FastAPI HTTP server (default: 8000)
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import NoReturn

# How long a failed start stays readable on /health before the runner exits.
_FAILED_REPORT_SECONDS = 120

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("axo.runner")


def main() -> None:
    endpoint_address = os.environ.get("AXO_ENDPOINT_ADDRESS", "")
    result_address   = os.environ.get("AXO_RESULT_ADDRESS", "")
    function_id      = os.environ.get("AXO_FUNCTION_ID", "")
    version_str      = os.environ.get("AXO_FUNCTION_VERSION", "0")
    job_port         = int(os.environ.get("AXO_JOB_PORT", "5600"))
    fastapi_port     = int(os.environ.get("AXO_FASTAPI_PORT", "8000"))
    scratch_root     = os.environ.get("AXO_SCRATCH_ROOT", "/tmp/axo_runner/scratch")
    dataio_timeout   = float(os.environ.get("AXO_DATAIO_TIMEOUT_SECONDS", "30.0"))

    if not endpoint_address or not function_id:
        logger.error("AXO_ENDPOINT_ADDRESS and AXO_FUNCTION_ID are required")
        sys.exit(1)

    version = int(version_str)

    from axo_endpoint.runner import startup
    from axo_endpoint.runner.bootstrap import fetch_code, install_requirements, materialize_function
    from axo_endpoint.runner.job_store import JobStore
    from axo_endpoint.runner.server import RunnerServer

    # Answers /health with the start-up stage until the real servers start.
    status = startup.StartupStatus(fastapi_port).start()

    logger.info("fetching function %s v%d from %s", function_id, version, endpoint_address)
    try:
        code_bytes, code_format, requirements, name, call_style = fetch_code(endpoint_address, function_id, version)
    except RuntimeError as exc:
        _fail(status, f"couldn't get the code from the endpoint: {exc}")

    if requirements:
        status.set_stage(startup.INSTALLING)
    try:
        install_requirements(requirements)
    except RuntimeError as exc:
        _fail(status, f"the packages couldn't be installed: {exc}")

    status.set_stage(startup.LOADING)
    try:
        fn = materialize_function(code_bytes, code_format, name, call_style)
    except Exception as exc:  # the user's code: any error at import time
        _fail(status, f"the code couldn't be loaded: {type(exc).__name__}: {exc}")

    status.stop()
    logger.info("function loaded, starting servers")
    server = RunnerServer(
        fn=fn,
        job_store=JobStore(),
        job_port=job_port,
        fastapi_port=fastapi_port,
        result_address=result_address or None,
        scratch_root=scratch_root,
        function_id=function_id,
        dataio_timeout_seconds=dataio_timeout,
    )
    server.start()


def _fail(status, message: str) -> "NoReturn":
    """Reports a failed start on /health for a while (the endpoint polls
    every second, so it reads it at once and removes the container), then
    exits."""
    logger.error(message)
    status.fail(message)
    time.sleep(_FAILED_REPORT_SECONDS)
    sys.exit(1)


if __name__ == "__main__":
    main()

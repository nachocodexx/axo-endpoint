import json
import socket
import urllib.error
import urllib.request

from axo_endpoint.runner import startup


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _health(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())
    raise AssertionError("the start-up page must never answer 200: only the real servers say ready")


def test_the_start_up_page_reports_each_stage_then_frees_the_port():
    port = _free_port()
    status = startup.StartupStatus(port).start()
    try:
        assert _health(port) == (503, {"stage": "fetching", "error": None})
        status.set_stage(startup.INSTALLING)
        assert _health(port)[1]["stage"] == "installing"
    finally:
        status.stop()
    with socket.socket() as s:                       # the real servers can take the port
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))


def test_a_failed_start_keeps_only_the_end_of_a_long_error():
    port = _free_port()
    status = startup.StartupStatus(port).start()
    try:
        status.fail("pip says a lot\n" * 1000 + "ERROR: No matching distribution found for rory==9.9")
        code, body = _health(port)
        assert code == 503 and body["stage"] == "failed"
        assert body["error"].endswith("No matching distribution found for rory==9.9")
        assert len(body["error"]) <= 4000
    finally:
        status.stop()

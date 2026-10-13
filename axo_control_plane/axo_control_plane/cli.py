import argparse
import os

_FLAG_ENV_MAP = [
    ("instance-id",     "AXO_CP_INSTANCE_ID",     "Unique id of this Control Plane copy"),
    ("http-host",       "AXO_CP_HTTP_HOST",       "HTTP host to bind on"),
    ("http-port",       "AXO_CP_HTTP_PORT",       "HTTP port to bind on"),
    ("router-bind",     "AXO_CP_ROUTER_BIND",     "ZMQ intake bind address (VEM heartbeats, job attempts, summaries)"),
    ("kurrent-uri",     "AXO_CP_KURRENT_URI",     "Kurrent(DB) connection URI"),
    ("mongo-uri",       "AXO_CP_MONGO_URI",       "MongoDB connection URI"),
    ("mongo-db-name",   "AXO_CP_MONGO_DB_NAME",   "MongoDB database name"),
    ("xolo-uri",        "AXO_CP_XOLO_URI",        "Xolo API root (…/api/v4)"),
    ("log-level",       "AXO_CP_LOG_LEVEL",       "Log level (DEBUG/INFO/WARNING/ERROR)"),
    ("log-to-file",     "AXO_CP_LOG_TO_FILE",     "Write logs to file (true/false)"),
    ("log-path",        "AXO_CP_LOG_PATH",        "Log directory"),
]


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="axo-control-plane",
        description="Launch the axo Control Plane.\nPriority: CLI flags > shell env vars > env file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--env-file", metavar="PATH",
                        help="Path to .env file to load (default: $AXO_CP_ENV_FILE or .env)")
    for flag, _, help_text in _FLAG_ENV_MAP:
        parser.add_argument(f"--{flag}", default=None, metavar="VALUE", help=help_text)
    args = parser.parse_args()

    if args.env_file is not None:
        os.environ["AXO_CP_ENV_FILE"] = args.env_file
    for flag, env_var, _ in _FLAG_ENV_MAP:
        value = getattr(args, flag.replace("-", "_"))
        if value is not None:
            os.environ[env_var] = value

    from axo_control_plane.main import main as _run
    _run()


if __name__ == "__main__":
    main()

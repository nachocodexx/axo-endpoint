import argparse
import os

_FLAG_ENV_MAP = [
    ("router-bind",          "AXO_STORAGE_ROUTER_BIND",          "ZMQ ROUTER bind address"),
    ("workers",              "AXO_STORAGE_WORKERS",              "Request worker threads"),
    ("backend",              "AXO_STORAGE_BACKEND",              "Chunk storage layer: fs | memory"),
    ("fs-root",              "AXO_STORAGE_FS_ROOT",              "Root directory for the fs backend"),
    ("catalog-path",         "AXO_STORAGE_CATALOG_PATH",         "SQLite catalog file (:memory: for none)"),
    ("ve-tokens",            "AXO_STORAGE_VE_TOKENS",            "VE tokens as ve-id=token,ve-id=token"),
    ("ve-token-secret",      "AXO_STORAGE_VE_TOKEN_SECRET",      "Secret VE tokens are derived from (HMAC of the VE id)"),
    ("service-tokens",       "AXO_STORAGE_SERVICE_TOKENS",       "Comma-separated platform service tokens"),
    ("access-mode",          "AXO_STORAGE_ACCESS_MODE",          "static | allow_all (dev only)"),
    ("access-file",          "AXO_STORAGE_ACCESS_FILE",          "JSON access rules for static mode"),
    ("access-cache-seconds", "AXO_STORAGE_ACCESS_CACHE_SECONDS", "How long an access answer is reused"),
    ("log-level",            "AXO_STORAGE_LOG_LEVEL",            "Log level (DEBUG/INFO/WARNING/ERROR)"),
]


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="axo-storage",
        description="Launch axo_storage, the platform bucket service.\nPriority: CLI flags > shell env vars > env file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--env-file", metavar="PATH", help="Path to .env file (default: $AXO_STORAGE_ENV_FILE or .env)")
    for flag, _, help_text in _FLAG_ENV_MAP:
        parser.add_argument(f"--{flag}", default=None, metavar="VALUE", help=help_text)
    args = parser.parse_args()

    if args.env_file is not None:
        os.environ["AXO_STORAGE_ENV_FILE"] = args.env_file
    for flag, env_var, _ in _FLAG_ENV_MAP:
        value = getattr(args, flag.replace("-", "_"))
        if value is not None:
            os.environ[env_var] = value

    from axo_storage.main import main as _run
    _run()


if __name__ == "__main__":
    main()

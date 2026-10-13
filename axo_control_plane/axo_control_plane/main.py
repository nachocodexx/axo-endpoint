import os

from dotenv import load_dotenv

from axo_control_plane.config import Config


def main() -> None:
    _env_file = os.environ.get("AXO_CP_ENV_FILE", ".env")
    load_dotenv(dotenv_path=_env_file, override=False)
    from axo_control_plane.server import Server

    server = Server(Config())
    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()

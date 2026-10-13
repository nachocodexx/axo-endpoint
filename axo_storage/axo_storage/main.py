import os
import threading

from dotenv import load_dotenv

from axo_storage.app import App
from axo_storage.config import Config


def main() -> None:
    load_dotenv(dotenv_path=os.environ.get("AXO_STORAGE_ENV_FILE", ".env"), override=False)
    app = App(Config())
    app.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()


if __name__ == "__main__":
    main()

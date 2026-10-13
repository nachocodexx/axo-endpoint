from __future__ import annotations

from typing import Optional

from axo_storage.access import (
    AccessChecker,
    AllowAllAccessChecker,
    CachingAccessChecker,
    ControlPlaneAccessChecker,
    StaticAccessChecker,
)
from axo_storage.auth import Authenticator, parse_ve_tokens
from axo_storage.catalog import StorageCatalog
from axo_storage.chunk_store import ChunkStore, FilesystemChunkStore, MemoryChunkStore
from axo_storage.config import Config
from axo_storage.log import Log
from axo_storage.server import StorageRouterServer
from axo_storage.service import StorageService


class App:
    """Wires config into catalog + chunk store + auth + access + server."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = Log(
            name="axo-storage",
            log_level=config.AXO_STORAGE_LOG_LEVEL,
            disabled=config.AXO_STORAGE_LOG_DISABLED,
            to_file=config.AXO_STORAGE_LOG_TO_FILE,
            path=config.AXO_STORAGE_LOG_PATH,
            filename=config.AXO_STORAGE_LOG_FILENAME,
            console_handler_level=config.AXO_STORAGE_LOG_CONSOLE_LEVEL,
            file_handler_level=config.AXO_STORAGE_LOG_FILE_LEVEL,
            indent=config.AXO_STORAGE_LOG_JSON_INDENT,
            use_rich=config.AXO_STORAGE_LOG_USE_RICH,
        )
        self.catalog = StorageCatalog(config.AXO_STORAGE_CATALOG_PATH)
        self.chunks: ChunkStore = (
            MemoryChunkStore() if config.AXO_STORAGE_BACKEND == "memory"
            else FilesystemChunkStore(config.AXO_STORAGE_FS_ROOT)
        )
        self.service = StorageService(
            catalog=self.catalog,
            chunks=self.chunks,
            authenticator=Authenticator(
                parse_ve_tokens(config.AXO_STORAGE_VE_TOKENS), config.AXO_STORAGE_SERVICE_TOKENS,
                config.AXO_STORAGE_VE_TOKEN_SECRET,
            ),
            access=CachingAccessChecker(self._access_checker(), config.AXO_STORAGE_ACCESS_CACHE_SECONDS),
            logger=self.logger,
        )
        self.server = StorageRouterServer(
            config.AXO_STORAGE_ROUTER_BIND, self.service, workers=config.AXO_STORAGE_WORKERS, logger=self.logger,
        )

    def _access_checker(self) -> AccessChecker:
        if self.config.AXO_STORAGE_ACCESS_MODE == "allow_all":
            return AllowAllAccessChecker()
        if self.config.AXO_STORAGE_ACCESS_MODE == "control_plane":
            return ControlPlaneAccessChecker(self.config.AXO_STORAGE_CP_URI, self.config.AXO_STORAGE_CP_CHECK_TOKEN)
        owner = self._bucket_owner
        if self.config.AXO_STORAGE_ACCESS_FILE:
            return StaticAccessChecker.from_file(self.config.AXO_STORAGE_ACCESS_FILE, owner)
        return StaticAccessChecker({}, owner)

    def _bucket_owner(self, bucket: str) -> Optional[str]:
        row = self.catalog.get_bucket(bucket)
        return row.unwrap().created_by if row.is_ok else None

    def start(self) -> None:
        self.server.start()

    def stop(self) -> None:
        self.server.stop()
        self.catalog.close()

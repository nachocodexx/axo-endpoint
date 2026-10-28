from __future__ import annotations

import os
from typing import Optional

from axo_shared.messaging import ReliableSender, SqliteJournal, ZmqDealerTransport

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
from axo_storage.usage import STORAGE_OBJECT_WRITTEN, UsageMeter, UsageReporter, object_written


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
        self.meter = UsageMeter()
        self.sender: Optional[ReliableSender] = None
        self.reporter: Optional[UsageReporter] = None
        if config.AXO_STORAGE_CP_INTAKE_URI:
            os.makedirs(config.AXO_STORAGE_USAGE_OUTBOX_PATH, exist_ok=True)
            journal = SqliteJournal(os.path.join(config.AXO_STORAGE_USAGE_OUTBOX_PATH,
                                                 f"storage-{config.AXO_STORAGE_INSTANCE_ID}.db"))
            self.sender = ReliableSender(journal, ZmqDealerTransport(config.AXO_STORAGE_CP_INTAKE_URI),
                                         source_id=f"storage:{config.AXO_STORAGE_INSTANCE_ID}")
            self.reporter = UsageReporter(
                self.meter, self.catalog.all_bucket_sizes,
                lambda event_type, data, message_id: self.sender.publish(event_type, data, message_id),
                config.AXO_STORAGE_INSTANCE_ID)
        self.service = StorageService(
            catalog=self.catalog,
            chunks=self.chunks,
            authenticator=Authenticator(
                parse_ve_tokens(config.AXO_STORAGE_VE_TOKENS), config.AXO_STORAGE_SERVICE_TOKENS,
                config.AXO_STORAGE_VE_TOKEN_SECRET,
            ),
            access=CachingAccessChecker(self._access_checker(), config.AXO_STORAGE_ACCESS_CACHE_SECONDS),
            logger=self.logger,
            meter=self.meter if self.reporter is not None else None,
            on_written=self._report_written if self.sender is not None else None,
        )
        self.server = StorageRouterServer(
            config.AXO_STORAGE_ROUTER_BIND, self.service, workers=config.AXO_STORAGE_WORKERS, logger=self.logger,
        )

    def _report_written(self, row, caller) -> None:
        data, message_id = object_written(row, caller)
        self.sender.publish(STORAGE_OBJECT_WRITTEN, data, message_id)

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
        if self.sender is not None and self.reporter is not None:
            self.sender.start()
            self.reporter.start()

    def stop(self) -> None:
        self.server.stop()
        if self.reporter is not None:
            self.reporter.stop()
        if self.sender is not None:
            self.sender.stop()
        self.catalog.close()

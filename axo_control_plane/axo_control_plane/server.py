from __future__ import annotations

import os
import socket
import threading
import time
from typing import Callable, List

import uvicorn
from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

from axo_shared.axo_storage import AxoStorageClient, ServiceCredentials, derive_ve_token
from axo_shared.errors import BucketAlreadyExistsError, BucketNotFoundError

from axo_control_plane.application.access import AccessResolver, Invalidations
from axo_control_plane.application.authz import Authorizer
from axo_control_plane.application.bucket_events import STORAGE_OBJECT_WRITTEN, BucketEvents
from axo_control_plane.application.bucket_files import BucketFiles
from axo_control_plane.application.buckets import Buckets, StorageAccess
from axo_control_plane.application.capacity import CapacityLedger
from axo_control_plane.application.billing import VeBills
from axo_control_plane.application.billing_notices import BillingNotices
from axo_control_plane.application.billing_views import BillingViews
from axo_control_plane.application.metric_tiers import MetricTiers, cp_tiers, series_for
from axo_control_plane.application.statistics import Statistics, VeStatistics
from axo_control_plane.application.storage_metering import StorageMetering
from axo_control_plane.application.catalog import PlanCatalog
from axo_control_plane.application.cleanup import Cleanup
from axo_control_plane.application.front_door import FrontDoor
from axo_control_plane.application.intake import VemIntake
from axo_control_plane.application.notifications import Notifications
from axo_control_plane.application.people import Names, People
from axo_control_plane.application.repair import VeRepair
from axo_control_plane.application.provisioner import Provisioner, StackSettings
from axo_control_plane.application.sdk_keys import SdkKeys
from axo_control_plane.application.settings import UserSettings
from axo_control_plane.application.sharing import Sharing
from axo_control_plane.application.sleeping import SleepManager
from axo_control_plane.application.teams import Teams
from axo_control_plane.application.ve_service import VeService
from axo_control_plane.config import Config
from axo_control_plane.domain.names import code_bucket_for, objects_bucket_for, results_bucket_for
from axo_control_plane.domain.plans import Resources
from axo_control_plane.infrastructure.api.app import create_app
from axo_control_plane.infrastructure.api.deps import Services
from axo_control_plane.infrastructure.docker_stack import DockerStackDriver
from axo_control_plane.infrastructure.intake_router import IntakeRouter
from axo_control_plane.infrastructure.kurrent_history import KurrentHistory
from axo_control_plane.infrastructure.mongo_leases import MongoLeases
from axo_control_plane.infrastructure.storage_gateway import CP_PRINCIPAL, AxoStorageGateway
from axo_control_plane.infrastructure.vem_http import HttpVemGateway
from axo_control_plane.infrastructure.xolo_identity import XoloIdentity
from axo_control_plane.log import Log
from axo_control_plane.log.catalog import Component, Event


def ensure_indexes(db: Database) -> None:
    db["virtual_environments"].create_index([("owner.id", ASCENDING), ("name", ASCENDING)])
    db["virtual_environments"].create_index([("lifecycle", ASCENDING)])
    db["users"].create_index([("username", ASCENDING)], unique=True)
    db["users"].create_index([("email", ASCENDING)])
    db["teams"].create_index([("name", ASCENDING)])
    db["buckets"].create_index([("owner.id", ASCENDING)])
    db["bucket_names"].create_index([("bucket_id", ASCENDING)])
    db["notifications"].create_index([("user_id", ASCENDING), ("created_at", ASCENDING)])
    db["notifications"].create_index([("created_at", ASCENDING)])
    db["invitations"].create_index([("invitee.id", ASCENDING), ("status", ASCENDING)])
    db["invitations"].create_index([("code_hash", ASCENDING)], sparse=True)
    # Billing: closing a VE bill stamps / adds up its attempts; fee lines by
    # VE + period and by team + month (spending limits).
    db["job_attempts"].create_index([("ve_id", ASCENDING), ("bill_id", ASCENDING), ("finished_at_ts", ASCENDING)])
    db["ve_charges"].create_index([("ve_id", ASCENDING), ("period_index", ASCENDING)])
    db["ve_charges"].create_index([("kind", ASCENDING), ("bill_closed", ASCENDING), ("period_end", ASCENDING)])
    db["ve_charges"].create_index([("owner.id", ASCENDING), ("at", ASCENDING)])
    db["ve_bills"].create_index([("ve_id", ASCENDING), ("period_end", ASCENDING)])
    db["ve_bills"].create_index([("owner.kind", ASCENDING), ("owner.id", ASCENDING), ("period_end", ASCENDING)])
    db["ve_charges"].create_index([("at", ASCENDING)])
    db["job_attempts"].create_index([("finished_at_ts", ASCENDING)])
    for name in ("storage_usage", "storage_size_hours"):
        db[name].create_index([("payer_key", ASCENDING), ("bill_id", ASCENDING), ("ts", ASCENDING)])
        db[name].create_index([("bill_id", ASCENDING), ("ts", ASCENDING)])
    db["storage_bills"].create_index([("payer_key", ASCENDING), ("period_start", ASCENDING)])
    # Statistics tiers: "not merged up yet" and "expired" lookups.
    for tiers_name in ("metric_minutes", "storage_usage", "metric_bins_900", "metric_bins_86400",
                       "storage_usage_bins_900", "storage_usage_bins_86400"):
        db[tiers_name].create_index([("rolled", ASCENDING), ("bin_start", ASCENDING)])
    db["metric_minutes"].create_index([("ve_id", ASCENDING), ("bin_start", ASCENDING)])
    db["job_attempts"].create_index([("ve_id", ASCENDING), ("received_at", ASCENDING)])
    # Statistics: top lists / heatmap (VE + time), one job's time bar.
    db["job_attempts"].create_index([("ve_id", ASCENDING), ("finished_at_ts", ASCENDING)])
    db["job_attempts"].create_index([("ve_id", ASCENDING), ("job_id", ASCENDING)])
    db["job_attempts"].create_index([("ve_id", ASCENDING), ("started_by_type", ASCENDING), ("started_by_id", ASCENDING),
                                     ("finished_at_ts", ASCENDING)])
    db["metric_minutes"].create_index([("ve_id", ASCENDING), ("bin_start", ASCENDING)])
    db["pending_requests"].create_index([("ve_id", ASCENDING), ("status", ASCENDING), ("created_at", ASCENDING)])
    db["cache_invalidations"].create_index([("at", ASCENDING)])
    db["sdk_keys"].create_index([("user_id", ASCENDING)])
    db["purchases"].create_index([("team_id", ASCENDING), ("month", ASCENDING)])


class Server:
    """Composition root: builds every collaborator from Config and runs the
    background loops (provisioning, sleeping, cleanup, cache invalidations)
    plus the ZMQ intake and the HTTP front door. Several identical copies may
    run at once against the same Mongo + Kurrent; leases keep their work
    apart."""

    def __init__(self, config: Config) -> None:
        self._config = c = config
        self._logger = Log(
            name="axo-control-plane", log_level=c.AXO_CP_LOG_LEVEL, disabled=c.AXO_CP_LOG_DISABLED,
            to_file=c.AXO_CP_LOG_TO_FILE, path=c.AXO_CP_LOG_PATH, filename=c.AXO_CP_LOG_FILENAME,
            console_handler_level=c.AXO_CP_LOG_CONSOLE_LEVEL, file_handler_level=c.AXO_CP_LOG_FILE_LEVEL,
            error_log=c.AXO_CP_LOG_ERROR_FILE, when=c.AXO_CP_LOG_ROTATION_WHEN,
            interval=c.AXO_CP_LOG_ROTATION_INTERVAL, indent=c.AXO_CP_LOG_JSON_INDENT,
            use_rich=c.AXO_CP_LOG_USE_RICH, colorize=c.AXO_CP_LOG_COLORIZE,
        )
        for name in ("AXO_CP_VEM_TOKEN_SECRET", "AXO_CP_STORAGE_VE_TOKEN_SECRET", "AXO_CP_STORAGE_CHECK_TOKEN",
                     "AXO_CP_SDK_KEY_PEPPER", "AXO_CP_STORAGE_SERVICE_TOKEN", "AXO_CP_XOLO_API_KEY"):
            if not getattr(c, name):
                raise SystemExit(f"{name} must be set (a long random secret)")
        instance = c.AXO_CP_INSTANCE_ID or f"{socket.gethostname()}-{os.getpid()}"
        self._stop_event = threading.Event()

        db = MongoClient(c.AXO_CP_MONGO_URI)[c.AXO_CP_MONGO_DB_NAME]
        ensure_indexes(db)
        from kurrentdbclient import KurrentDBClient

        history = KurrentHistory(KurrentDBClient(c.AXO_CP_KURRENT_URI))
        identity = XoloIdentity(c.AXO_CP_XOLO_URI, c.AXO_CP_XOLO_ACCOUNT_ID, c.AXO_CP_XOLO_API_KEY,
                                scope=c.AXO_CP_XOLO_SCOPE, license_expiration=c.AXO_CP_XOLO_LICENSE_EXPIRATION,
                                session_expiration=c.AXO_CP_SESSION_EXPIRATION)

        self._invalidations = invalidations = Invalidations(db)
        access = AccessResolver(identity, invalidations, ttl_s=c.AXO_CP_ROLE_CACHE_SECONDS)
        authz = Authorizer(access)
        names = Names(db)
        people = People(db, identity, access, names, history, session_cache_s=c.AXO_CP_SESSION_CACHE_SECONDS)
        teams = Teams(db, identity, access, names, history)
        user_settings = UserSettings(db)
        notifications = Notifications(db, settings=user_settings)
        catalog = PlanCatalog(db, history)
        catalog.seed()
        capacity = CapacityLedger(db, Resources(c.AXO_CP_CAPACITY_CPU_CORES, c.AXO_CP_CAPACITY_MEMORY_MB))
        ves = VeService(db, catalog, capacity, identity, access, authz, teams, notifications, history,
                        max_custom=Resources(c.AXO_CP_CUSTOM_SIZE_MAX_CPU_CORES, c.AXO_CP_CUSTOM_SIZE_MAX_MEMORY_MB),
                        grace_s=c.AXO_CP_GRACE_DAYS * 86400)
        self._ves = ves
        self._bills = bills = VeBills(db, history, close_after_s=c.AXO_CP_BILL_CLOSE_AFTER_SECONDS)
        self._billing_notices = BillingNotices(db, notifications, catalog, bills, grace_s=c.AXO_CP_GRACE_DAYS * 86400)

        self._storage_client = AxoStorageClient(c.AXO_CP_STORAGE_URI, ServiceCredentials(c.AXO_CP_STORAGE_SERVICE_TOKEN),
                                                timeout_seconds=c.AXO_CP_STORAGE_TIMEOUT_SECONDS)
        object_store = AxoStorageGateway(self._storage_client)
        grace_s = c.AXO_CP_GRACE_DAYS * 86400
        buckets = Buckets(db, object_store, identity, access, authz, names, teams, history,
                          default_quota_bytes=c.AXO_CP_BUCKET_DEFAULT_QUOTA_BYTES,
                          max_quota_bytes=c.AXO_CP_BUCKET_MAX_QUOTA_BYTES, grace_s=grace_s)
        sharing = Sharing(db, people, names, teams, buckets, identity, access, authz, notifications, history)
        keys = SdkKeys(db, people, authz, history, pepper=c.AXO_CP_SDK_KEY_PEPPER)

        self._leases = leases = MongoLeases(db)
        self._instance = instance
        vem_token: Callable[[str], str] = lambda ve_id: derive_ve_token(c.AXO_CP_VEM_TOKEN_SECRET, ve_id)
        vem = HttpVemGateway(vem_token, port=c.AXO_CP_VEM_HTTP_PORT, timeout_s=c.AXO_CP_VEM_REQUEST_TIMEOUT_SECONDS)
        self._storage_metering = StorageMetering(db, catalog, history,
                                                 close_after_s=c.AXO_CP_BILL_CLOSE_AFTER_SECONDS)
        intake = VemIntake(db, history, notifications, catalog, storage=self._storage_metering)
        intake.backfill_choreography_lines()
        tiers = cp_tiers(c.AXO_CP_METRIC_TIERS)
        self._metric_tiers = MetricTiers(db, tiers, series_for(tiers))
        figures = Statistics(db, catalog, tiers)
        statistics = VeStatistics(figures, authz, list_fn=ves.list_for)
        billing = BillingViews(db, authz, teams, ves, bills, self._storage_metering, figures,
                               bucket_stats=object_store.bucket_stats)

        def ensure_bucket(name: str) -> None:
            created = self._storage_client.create_bucket(CP_PRINCIPAL, name)
            if created.is_err and not isinstance(created.unwrap_err(), BucketAlreadyExistsError):
                raise RuntimeError(f"bucket {name}: {created.unwrap_err()}")

        def drop_bucket(name: str) -> None:
            dropped = self._storage_client.delete_bucket(CP_PRINCIPAL, name)
            if dropped.is_err and not isinstance(dropped.unwrap_err(), BucketNotFoundError):
                raise RuntimeError(f"bucket {name}: {dropped.unwrap_err()}")

        stack = StackSettings(
            vem_image=c.AXO_CP_VEM_IMAGE, endpoint_image=c.AXO_CP_ENDPOINT_IMAGE,
            kurrent_image=c.AXO_CP_KURRENT_IMAGE, mongo_image=c.AXO_CP_MONGO_IMAGE,
            redis_image=c.AXO_CP_REDIS_IMAGE,
            control_network=c.AXO_CP_CONTROL_NETWORK, storage_container=c.AXO_CP_STORAGE_CONTAINER,
            storage_uri=c.AXO_CP_STORAGE_URI, cp_intake_uri=c.AXO_CP_ROUTER_PUBLIC_URI,
            container_backend=c.AXO_CP_CONTAINER_BACKEND, vem_token_fn=vem_token,
            storage_token_fn=lambda ve_id: derive_ve_token(c.AXO_CP_STORAGE_VE_TOKEN_SECRET, ve_id),
            platform_limits={k: v for k, v in {"max_queue_limit": c.AXO_CP_PLATFORM_MAX_QUEUE_LIMIT,
                                               "max_wait_seconds": c.AXO_CP_PLATFORM_MAX_WAIT_SECONDS,
                                               "history_grace_days": c.AXO_CP_HISTORY_GRACE_DAYS}.items()
                             if v is not None},
            ensure_code_bucket=lambda ve_id: ensure_bucket(code_bucket_for(ve_id)),
            ensure_results_bucket=lambda ve_id: ensure_bucket(results_bucket_for(ve_id)),
            drop_results_bucket=lambda ve_id: drop_bucket(results_bucket_for(ve_id)),
            ensure_objects_bucket=lambda ve_id: ensure_bucket(objects_bucket_for(ve_id)),
        )
        driver = DockerStackDriver()
        self._provisioner = Provisioner(
            db, driver, vem, leases, capacity, catalog, identity, notifications, history, stack, instance,
            lease_s=c.AXO_CP_LEASE_SECONDS, heartbeat_timeout_s=c.AXO_CP_VEM_HEARTBEAT_TIMEOUT_SECONDS,
            vem_recreate_after_s=c.AXO_CP_VEM_DOWN_RECREATE_SECONDS,
            delete_flush_timeout_s=c.AXO_CP_DELETE_FLUSH_TIMEOUT_SECONDS, grace_s=grace_s, salvage=intake.salvage,
        )
        repair = VeRepair(db, driver, vem, self._provisioner, notifications, history, c.AXO_CP_STORAGE_CONTAINER,
                          every_s=c.AXO_CP_REPAIR_EVERY_SECONDS, auto_after_s=c.AXO_CP_AUTO_REPAIR_AFTER_SECONDS,
                          auto_every_s=c.AXO_CP_AUTO_REPAIR_EVERY_SECONDS)
        self._provisioner.after_running = repair.check_unavailable
        ves.repairs = repair
        self._sleep = SleepManager(db, catalog, vem, leases, notifications, history, instance,
                                   idle_after_s=c.AXO_CP_SLEEP_AFTER_IDLE_SECONDS,
                                   wake_timeout_s=c.AXO_CP_WAKE_TIMEOUT_SECONDS)
        self._cleanup = Cleanup(db, driver, vem, object_store, buckets, leases, history, instance,
                                confirm_after_s=c.AXO_CP_CLEANUP_INTERVAL_SECONDS * 0.75,
                                log_lines=c.AXO_CP_CLEANUP_LOG_LINES, drop_bucket=drop_bucket)
        storage_access = StorageAccess(buckets, access)
        # New files in a bucket -> the VEs whose choreography triggers watch it.
        intake.storage_handlers[STORAGE_OBJECT_WRITTEN] = BucketEvents(db, storage_access, self._sleep).written
        self._intake_router = IntakeRouter(c.AXO_CP_ROUTER_BIND, intake.handle, logger=self._logger)
        front_door = FrontDoor(db, authz, self._sleep, catalog, notifications)

        self._bootstrap(people)
        self._services = Services(
            people=people, teams=teams, ves=ves, buckets=buckets, sharing=sharing, keys=keys,
            notifications=notifications, catalog=catalog, capacity=capacity, front_door=front_door,
            sleep=self._sleep, storage_access=storage_access, cleanup=self._cleanup,
            vem_base_url=vem.base_url, vem_headers=vem.headers,
            vem_ws_url=lambda ve_id, path: f"ws://ve-{ve_id}-vem:{c.AXO_CP_VEM_HTTP_PORT}{path}?token={vem_token(ve_id)}",
            object_store=object_store, storage_check_token=c.AXO_CP_STORAGE_CHECK_TOKEN, db=db,
            statistics=statistics, billing=billing, settings=user_settings,
            bucket_files=BucketFiles(db, buckets, object_store, people),
        )
        self._threads: List[threading.Thread] = []

    def _bootstrap(self, people: People) -> None:
        """The Platform Admin + platform groups, retried while Xolo starts up."""
        c = self._config
        for attempt in range(30):
            try:
                outcome = people.bootstrap_admin(c.AXO_CP_ADMIN_USERNAME, c.AXO_CP_ADMIN_PASSWORD, c.AXO_CP_ADMIN_EMAIL)
                event = Event.Bootstrap.ADMIN_CREATED if outcome == "created" else Event.Bootstrap.ADMIN_EXISTS
                self._logger.info_event(event, component=Component.BOOTSTRAP, username=c.AXO_CP_ADMIN_USERNAME)
                return
            except Exception as exc:
                self._logger.warning_event(Event.Bootstrap.FAILED, component=Component.BOOTSTRAP,
                                           attempt=attempt + 1, error=str(exc)[:300])
                time.sleep(min(2 ** attempt, 15))
        raise SystemExit("could not create the bootstrap Platform Admin in Xolo")

    def _loop(self, name: str, target: Callable[[threading.Event, float], None], interval: float) -> None:
        thread = threading.Thread(target=target, args=(self._stop_event, interval), daemon=True, name=name)
        thread.start()
        self._threads.append(thread)

    def _renewals(self, stop_event: threading.Event, interval_s: float) -> None:
        """VE rental periods (renew / stop when cancelled / delete after the
        grace period), closing their bills, and the billing notices, one CP
        copy at a time."""
        while not stop_event.wait(interval_s):
            try:
                if self._leases.acquire("renewals", self._instance, max(interval_s * 4, 60.0)):
                    self._ves.renew_due()
                    self._bills.close_due()
                    self._storage_metering.close_due()
                    self._billing_notices.tick()
                    self._services.notifications.purge(self._config.AXO_CP_NOTIFICATION_KEEP_DAYS)
            except Exception as exc:
                self._logger.warning_event(Event.Rental.RENEWALS_FAILED, component=Component.RENTALS,
                                           error=str(exc)[:300])

    def _rollups(self, stop_event: threading.Event, interval_s: float) -> None:
        """Statistics tiers (1 min -> 15 min -> 1 day), one CP copy at a time."""
        while not stop_event.wait(interval_s):
            try:
                if self._leases.acquire("metric-tiers", self._instance, max(interval_s * 2, 60.0)):
                    self._metric_tiers.tick()
            except Exception as exc:
                self._logger.warning_event(Event.Metrics.ROLLUP_FAILED, component=Component.METRICS,
                                           error=str(exc)[:300])

    def start(self) -> None:
        c = self._config
        self._intake_router.start()
        self._loop("provisioner", self._provisioner.run_forever, c.AXO_CP_PROVISION_INTERVAL_SECONDS)
        self._loop("sleep", self._sleep.run_forever, 5.0)
        self._loop("cleanup", self._cleanup.run_forever, c.AXO_CP_CLEANUP_INTERVAL_SECONDS)
        self._loop("renewals", self._renewals, c.AXO_CP_RENEWAL_INTERVAL_SECONDS)
        self._loop("metric-tiers", self._rollups, c.AXO_CP_METRIC_ROLLUP_INTERVAL_SECONDS)
        self._loop("invalidations", self._invalidations.run_forever, 1.0)
        self._logger.info_event(Event.Server.STARTED, component=Component.SERVER,
                                http_port=c.AXO_CP_HTTP_PORT, intake=self._intake_router.bind_address)
        app = create_app(self._services, cors_origins=c.AXO_CP_CORS_ORIGINS)
        uvicorn.run(app, host=c.AXO_CP_HTTP_HOST, port=c.AXO_CP_HTTP_PORT, log_level="warning")

    def stop(self) -> None:
        self._stop_event.set()
        self._intake_router.stop()
        self._storage_client.close()
        self._logger.info_event(Event.Server.STOPPED, component=Component.SERVER)

from __future__ import annotations

from typing import Optional

import json
import threading
import time

from axo_vem.application.choreography.cancel_choreography_run import CancelChoreographyRunUseCase
from axo_vem.application.choreography.create_choreography import CreateChoreographyUseCase
from axo_vem.application.choreography.delete_choreography import DeleteChoreographyUseCase
from axo_vem.application.choreography.purge_choreography import PurgeChoreographyUseCase
from axo_vem.application.choreography.run_choreography import RunChoreographyUseCase
from axo_vem.application.choreography.update_choreography import UpdateChoreographyUseCase
from axo_vem.application.compute.delete_function import DeleteFunctionUseCase
from axo_vem.application.compute.endpoint_picker import EndpointPicker
from axo_vem.application.compute.function_code_store import FunctionCodeStore
from axo_vem.application.metering.metrics_store import MetricsStore
from axo_vem.application.virtual_environment.lifecycle_service import RUNNING_VE
from axo_vem.infrastructure.control_plane.cp_link import ControlPlaneLink
from axo_vem.application.policies.evaluator import PolicyEvaluator
from axo_vem.application.policies.policy_service import PolicyService
from axo_vem.application.virtual_environment.fleet_service import FleetService
from axo_vem.application.virtual_environment.job_tracker import JobTracker
from axo_vem.application.virtual_environment.lifecycle_service import VeLifecycleService
from axo_vem.application.virtual_environment.reconciler import Reconciler
from axo_vem.application.virtual_environment.restorer import VeRestorer
from axo_vem.infrastructure.container.endpoint_ops import DockerEndpointOps
from axo_vem.application.compute.purge_function_version import PurgeFunctionVersionUseCase
from axo_vem.application.compute.register_function import RegisterFunctionUseCase
from axo_vem.application.compute.submit_function_job import SubmitFunctionJobUseCase
from axo_vem.application.compute.update_function import UpdateFunctionUseCase
from axo_vem.application.nodes.launch_endpoint_node import LaunchEndpointNodeUseCase
from axo_vem.application.nodes.purge_endpoint import PurgeEndpointUseCase
from axo_vem.application.projector.dispatcher import apply_event
from axo_vem.application.projector.handlers import ProjectorHandlers
from axo_vem.config import Config
from axo_vem.domain.events import models
from axo_vem.infrastructure.auth.service_token import build_service_token_dependency
from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.virtual_environment.settings import PlanLimits
from axo_vem.infrastructure.container.endpoint_stats_poller import EndpointStatsPoller
from axo_vem.infrastructure.database.kurrent.appender import KurrentDBAppender
from axo_vem.application.data.bucket_storage import BucketStorage
from axo_shared.axo_storage import AxoStorageClient, ServiceCredentials, VeCredentials
from axo_vem.infrastructure.database.kurrent.client import build_kurrent_client
from axo_vem.infrastructure.database.kurrent.subscriber import KurrentSubscriber
from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository
from axo_vem.infrastructure.database.mongo.choreography_repository import MongoChoreographyRepository
from axo_vem.infrastructure.database.mongo.choreography_run_repository import MongoChoreographyRunRepository
from axo_vem.infrastructure.database.mongo.checkpoint_store import MongoCheckpointStore
from axo_vem.infrastructure.database.mongo.client import build_mongo_database
from axo_vem.infrastructure.database.mongo.collections import ReadCollections
from axo_vem.infrastructure.database.mongo.consensus_repository import MongoConsensusRepository
from axo_vem.infrastructure.database.mongo.endpoint_repository import MongoEndpointRepository
from axo_vem.infrastructure.database.mongo.function_repository import MongoFunctionRepository
from axo_vem.infrastructure.database.mongo.job_repository import MongoJobRepository
from axo_vem.infrastructure.resilience.errors import classify_kurrent_error, classify_mongo_error
from axo_vem.infrastructure.resilience.retry import retry_with_backoff
from axo_vem.infrastructure.transport.api.app import create_app
from axo_vem.infrastructure.transport.ws.broadcaster import Broadcaster
from axo_vem.infrastructure.transport.zmq_command.endpoint_liveness_worker import EndpointLivenessWorker
from axo_vem.infrastructure.transport.zmq_ingestion.router_server import IngestionRouterServer
from axo_vem.log import Log
from axo_vem.log.catalog import Component, Event
from axo_shared.container.spawner import ContainerSpawner
import uvicorn

class Server:
    """Hosts the ZMQ ingestion ROUTER (background thread), the Kurrent
    subscriber/projector (background thread), and FastAPI (main thread,
    blocking uvicorn.run) -- mirrors axo_endpoint/runner/server.py's
    RunnerServer combination pattern of one background-thread transport
    plus one blocking-on-main-thread HTTP server.

    Composition root for the domain/application/infrastructure split: reads
    config, builds real Mongo/Kurrent/Xolo clients, wraps them in concrete
    infrastructure repositories/adapters implementing the domain ABCs,
    injects those into application use cases, and attaches the use cases to
    the FastAPI/ZMQ adapters. Exercised only by the separate,
    container-backed integration suite, never by the fast unit tests.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = Log(
            name                  = "axo-vem",
            log_level             = config.AXO_VEM_LOG_LEVEL,
            disabled              = config.AXO_VEM_LOG_DISABLED,
            to_file               = config.AXO_VEM_LOG_TO_FILE,
            path                  = config.AXO_VEM_LOG_PATH,
            filename              = config.AXO_VEM_LOG_FILENAME,
            output_path           = config.AXO_VEM_LOG_OUTPUT_PATH,
            error_output_path     = config.AXO_VEM_LOG_ERROR_OUTPUT_PATH,
            console_handler_level = config.AXO_VEM_LOG_CONSOLE_LEVEL,
            file_handler_level    = config.AXO_VEM_LOG_FILE_LEVEL,
            error_log             = config.AXO_VEM_LOG_ERROR_FILE,
            when                  = config.AXO_VEM_LOG_ROTATION_WHEN,
            interval              = config.AXO_VEM_LOG_ROTATION_INTERVAL,
            indent                = config.AXO_VEM_LOG_JSON_INDENT,
            use_rich              = config.AXO_VEM_LOG_USE_RICH,
            colorize              = config.AXO_VEM_LOG_COLORIZE,
        )

        db = build_mongo_database(config.AXO_VEM_MONGO_URI, config.AXO_VEM_MONGO_DB_NAME)
        self._wait_for_mongo(db)
        read_collections = ReadCollections(endpoints=db["endpoints"], functions=db["functions"], consensus=db["consensus"])

        activity_repository            = MongoActivityRepository(db["unified_activity"])
        endpoint_repository            = MongoEndpointRepository(db["endpoints"])
        function_repository            = MongoFunctionRepository(db["functions"])
        consensus_repository           = MongoConsensusRepository(db["consensus"])
        job_repository                 = MongoJobRepository(db["jobs"])
        choreography_repository        = MongoChoreographyRepository(db["choreographies"])
        choreography_run_repository    = MongoChoreographyRunRepository(db["choreography_runs"])
        checkpoint_store               = MongoCheckpointStore(db["projector_checkpoints"])

        self._kurrent_client = build_kurrent_client(config.AXO_VEM_KURRENT_URI)
        self._wait_for_kurrent(self._kurrent_client)
        appender    = KurrentDBAppender(self._kurrent_client)
        broadcaster = Broadcaster()
        self._storage_client: Optional[AxoStorageClient] = None
        bucket_storage: Optional[BucketStorage] = None
        if config.AXO_VEM_STORAGE_URI:
            # With a Control Plane the VEM holds only its own VE's token, so it
            # can't reach another VE's data; the platform service token is for
            # local development without a CP.
            credentials = (
                ServiceCredentials(config.AXO_VEM_STORAGE_SERVICE_TOKEN) if config.AXO_VEM_STORAGE_SERVICE_TOKEN
                else VeCredentials(config.AXO_VEM_VE_ID, config.AXO_VEM_VE_STORAGE_TOKEN)
            )
            self._storage_client = AxoStorageClient(
                config.AXO_VEM_STORAGE_URI, credentials, timeout_seconds=config.AXO_VEM_STORAGE_TIMEOUT_SECONDS,
            )
            bucket_storage = BucketStorage(self._storage_client)

        # Only the Control Plane calls this VEM: its service token + the
        # acting user (the CP already checked that user's role here).
        acting_user_dependency = build_service_token_dependency(config.AXO_VEM_SERVICE_TOKEN)
        self._ve_settings = VeSettingsService(
            config.AXO_VEM_VE_ID,
            PlanLimits(
                max_endpoints=config.AXO_VEM_PLAN_MAX_ENDPOINTS,
                policies_allowed=config.AXO_VEM_PLAN_POLICIES_ALLOWED,
                max_retention_days=config.AXO_VEM_PLAN_MAX_RETENTION_DAYS,
                max_queue_limit=config.AXO_VEM_PLATFORM_MAX_QUEUE_LIMIT,
                max_wait_seconds=config.AXO_VEM_PLATFORM_MAX_WAIT_SECONDS,
                allow_wait_forever=config.AXO_VEM_PLATFORM_ALLOW_WAIT_FOREVER,
                quota_cpu_cores=config.AXO_VEM_QUOTA_CPU_CORES,
                quota_memory_mb=config.AXO_VEM_QUOTA_MEMORY_MB,
            ),
            appender,
            initial=json.loads(config.AXO_VEM_INITIAL_SETTINGS) if config.AXO_VEM_INITIAL_SETTINGS else None,
        )
        self._ve_settings.load(self._kurrent_client)  # rebuilt from its own event log
        self._stop_event = threading.Event()
        timeout = config.AXO_VEM_ENDPOINT_COMMAND_TIMEOUT_SECONDS

        # The endpoints this VEM manages (event-sourced, rebuilt on start),
        # the live job view, and the loop keeping both matched to settings.
        self._fleet = FleetService(config.AXO_VEM_VE_ID, appender)
        self._fleet.load(self._kurrent_client)
        self._jobs = JobTracker()
        container_spawner = ContainerSpawner()
        launch_endpoint_node_use_case = LaunchEndpointNodeUseCase(
            spawner                = container_spawner,
            image                  = config.AXO_VEM_NODE_IMAGE,
            network                = config.AXO_VEM_NODE_NETWORK,
            mode                   = config.AXO_VEM_DOCKER_MODE,
            endpoint_repository    = endpoint_repository,
            virtual_environment_id = config.AXO_VEM_VE_ID,
            storage_uri            = config.AXO_VEM_NODE_STORAGE_URI,
            storage_token          = config.AXO_VEM_VE_STORAGE_TOKEN,
        )
        self._reconciler_ops = DockerEndpointOps(
            container_spawner, config.AXO_VEM_DOCKER_MODE, launch_endpoint_node_use_case,
            config.AXO_VEM_NODE_API_URI, endpoint_repository, timeout,
        )
        self._reconciler = Reconciler(
            self._ve_settings,
            self._fleet,
            self._reconciler_ops,
            self._jobs,
            startup_timeout_s=config.AXO_VEM_ENDPOINT_STARTUP_TIMEOUT_SECONDS,
            drain_timeout_s=config.AXO_VEM_ENDPOINT_DRAIN_TIMEOUT_SECONDS,
            max_failures=config.AXO_VEM_ENDPOINT_MAX_FAILURES,
        )
        def has_leader() -> bool:
            latest = db["consensus"].find_one(sort=[("_id", -1)])
            return bool(latest and latest.get("leader_ids"))

        endpoint_picker = EndpointPicker(
            endpoint_repository, fleet=self._fleet, waker=self._reconciler.wake_one,
            wake_timeout_s=config.AXO_VEM_ENDPOINT_WAKE_TIMEOUT_SECONDS,
        )

        # Function code is kept in a hidden per-VE bucket so Restoring can
        # bring functions back after a stop/start.
        code_store: Optional[FunctionCodeStore] = None
        self._restorer: Optional[VeRestorer] = None
        if self._storage_client is not None:
            code_store = FunctionCodeStore(self._storage_client, config.AXO_VEM_VE_ID)
            self._restorer = VeRestorer(function_repository, code_store, endpoint_picker, timeout)
        self._lifecycle = VeLifecycleService(
            self._ve_settings, self._fleet, self._reconciler, self._reconciler_ops, self._jobs, appender,
            restorer=self._restorer, has_leader=has_leader, drain_timeout_s=config.AXO_VEM_ENDPOINT_DRAIN_TIMEOUT_SECONDS,
            before_settled=lambda: self._cp_link.flush(config.AXO_VEM_CP_FLUSH_TIMEOUT_SECONDS),
        )
        register_function_use_case = RegisterFunctionUseCase(
            config.AXO_VEM_VE_ID, endpoint_picker, timeout, code_store=code_store,
        )
        delete_function_use_case = DeleteFunctionUseCase(
            function_repository, endpoint_repository, job_repository, timeout,
        )
        update_function_use_case = UpdateFunctionUseCase(function_repository, endpoint_repository, timeout)
        purge_function_version_use_case = PurgeFunctionVersionUseCase(
            db["functions"], activity_repository, appender,
        )
        submit_function_job_use_case = SubmitFunctionJobUseCase(function_repository, endpoint_picker, timeout)
        purge_endpoint_use_case = PurgeEndpointUseCase(
            db["endpoints"], activity_repository, function_repository, job_repository, appender,
        )
        create_choreography_use_case = CreateChoreographyUseCase(appender)
        update_choreography_use_case = UpdateChoreographyUseCase(
            choreography_repository, choreography_run_repository, appender,
        )
        delete_choreography_use_case = DeleteChoreographyUseCase(
            choreography_repository, choreography_run_repository, appender,
        )
        purge_choreography_use_case = PurgeChoreographyUseCase(
            db["choreographies"], db["choreography_runs"], activity_repository,
        )
        run_choreography_use_case = RunChoreographyUseCase(
            choreography_repository=choreography_repository,
            run_repository=choreography_run_repository,
            function_repository=function_repository,
            endpoint_repository=endpoint_repository,
            submit_function_job_use_case=submit_function_job_use_case,
            bucket_storage=bucket_storage,
            broadcaster=broadcaster,
            command_timeout_seconds=timeout,
        )
        cancel_choreography_run_use_case = CancelChoreographyRunUseCase(
            choreography_repository=choreography_repository,
            run_repository=choreography_run_repository,
            run_use_case=run_choreography_use_case,
            command_timeout_seconds=timeout,
        )
        self._stats_poller = EndpointStatsPoller(
            endpoints        = read_collections.endpoints,
            spawner          = container_spawner,
            mode             = config.AXO_VEM_DOCKER_MODE,
            broadcaster      = broadcaster,
            interval_seconds = config.AXO_VEM_STATS_POLL_INTERVAL_SECONDS,
            logger           = self._logger,
        )
        self._endpoint_liveness_worker = EndpointLivenessWorker(
            endpoints            = read_collections.endpoints,
            event_publisher      = appender,
            stale_after_seconds  = config.AXO_VEM_ENDPOINT_STALE_AFTER_SECONDS,
            tick_seconds         = config.AXO_VEM_ENDPOINT_LIVENESS_TICK_SECONDS,
            ping_timeout_seconds = config.AXO_VEM_ENDPOINT_PING_TIMEOUT_SECONDS,
            logger               = self._logger,
        )

        # The endpoints' 10 s statistics (live tier, memory only) and the
        # scaling policies evaluated on them.
        self._metrics = MetricsStore(keep_seconds=config.AXO_VEM_METRICS_KEEP_SECONDS)
        self._policies = PolicyService(config.AXO_VEM_VE_ID, appender, config.AXO_VEM_PLAN_POLICIES_ALLOWED)
        self._policies.load(self._kurrent_client)

        def pause_policies_after_manual_change(_settings, changes, changed_by_type) -> None:
            if changed_by_type == "user" and "target_endpoints" in changes:
                self._policies.pause(config.AXO_VEM_POLICY_PAUSE_AFTER_MANUAL_SECONDS, "manual target change")

        self._ve_settings.on_change_detail(pause_policies_after_manual_change)

        def current_runners(function_id: str, version: int) -> Optional[int]:
            function = function_repository.get(function_id, version)
            spec = (function.runtime_spec or {}) if function is not None else {}
            return int(spec.get("max_concurrency", 1))

        def set_runners(function_id: str, version: int, count: int) -> None:
            update_function_use_case.execute(
                function_id=function_id, version=version, current_user_id="policy", max_concurrency=count,
            )

        self._policy_evaluator = PolicyEvaluator(
            self._policies, self._ve_settings, self._metrics, current_runners, set_runners,
            # "Scaling" is derived in status(); the stored lifecycle stays Running.
            is_active=lambda: self._lifecycle.lifecycle() == RUNNING_VE,
        )

        link_kwargs = dict(status_fn=self._lifecycle.status, metrics=self._metrics, event_log=appender)
        self._cp_link = (
            ControlPlaneLink.connect(config.AXO_VEM_CP_URI, config.AXO_VEM_CP_OUTBOX_PATH, config.AXO_VEM_VE_ID,
                                     **link_kwargs)
            if config.AXO_VEM_CP_URI else ControlPlaneLink(config.AXO_VEM_VE_ID, **link_kwargs)
        )

        self._ingestion = IngestionRouterServer(
            bind_address       = config.AXO_VEM_ROUTER_BIND,                   appender = appender, logger = self._logger,
            local_handlers     = {
                models.METRIC_SUMMARY_REPORTED: lambda data: self._metrics.ingest(data) >= 0,
                models.JOB_ATTEMPT_RECORDED: lambda data: self._cp_link.job_attempt(data),
            },
            max_attempts       = config.AXO_VEM_DB_CONNECT_MAX_ATTEMPTS,
            base_delay_seconds = config.AXO_VEM_DB_CONNECT_BASE_DELAY_SECONDS,
            max_delay_seconds  = config.AXO_VEM_DB_CONNECT_MAX_DELAY_SECONDS,
        )

        projector_handlers = ProjectorHandlers(
            activity_recorder              = activity_repository,
            endpoint_repository            = endpoint_repository,
            function_repository            = function_repository,
            consensus_recorder             = consensus_repository,
            job_repository                 = job_repository,
            choreography_repository        = choreography_repository,
        )
        self._subscriber = KurrentSubscriber(
            client=self._kurrent_client,
            checkpoint_store=checkpoint_store,
            on_event=lambda event_type, data: (
                apply_event(projector_handlers, event_type, data, broadcaster, logger=self._logger),
                self._jobs.observe(event_type, data),
            ),
            logger=self._logger,
            max_attempts=config.AXO_VEM_DB_CONNECT_MAX_ATTEMPTS,
            base_delay_seconds=config.AXO_VEM_DB_CONNECT_BASE_DELAY_SECONDS,
            max_delay_seconds=config.AXO_VEM_DB_CONNECT_MAX_DELAY_SECONDS,
            stale_after_seconds=config.AXO_VEM_PROJECTOR_STALE_AFTER_SECONDS,
        )

        self._lifecycle.load(self._kurrent_client)
        self._reconciler.on_after_tick(self._lifecycle.after_tick)

        self._app = create_app(
            collections                          = read_collections,
            activity_repository                  = activity_repository,
            stream_admin                          = appender,
            job_repository                        = job_repository,
            choreography_repository               = choreography_repository,
            choreography_run_repository           = choreography_run_repository,
            create_choreography_use_case          = create_choreography_use_case,
            update_choreography_use_case          = update_choreography_use_case,
            delete_choreography_use_case          = delete_choreography_use_case,
            purge_choreography_use_case           = purge_choreography_use_case,
            run_choreography_use_case             = run_choreography_use_case,
            cancel_choreography_run_use_case      = cancel_choreography_run_use_case,
            register_function_use_case            = register_function_use_case,
            delete_function_use_case              = delete_function_use_case,
            update_function_use_case              = update_function_use_case,
            purge_function_version_use_case       = purge_function_version_use_case,
            submit_function_job_use_case          = submit_function_job_use_case,
            purge_endpoint_use_case               = purge_endpoint_use_case,
            endpoint_purge_eligible_after_minutes_default = config.AXO_VEM_ENDPOINT_PURGE_ELIGIBLE_AFTER_MINUTES,
            reconciler                             = self._reconciler,
            lifecycle_service                      = self._lifecycle,
            policy_service                         = self._policies,
            fleet                                  = self._fleet,
            kurrent_reader                        = self._kurrent_client,
            ve_settings_service                   = self._ve_settings,
            current_user_dependency               = acting_user_dependency,
            endpoint_command_timeout_seconds      = timeout,
            cors_allow_origins                    = config.AXO_VEM_CORS_ORIGINS,
            broadcaster                           = broadcaster,
            service_token                         = config.AXO_VEM_SERVICE_TOKEN,
            logger                                = self._logger,
        )
        self._subscriber_thread: threading.Thread | None = None
        self._stats_poller_thread: threading.Thread | None = None
        self._endpoint_liveness_thread: threading.Thread | None = None

    def _wait_for_mongo(self, db) -> None:
        """Eager, retry-guarded connectivity probe -- closes the startup
        race where MongoDB isn't accepting connections yet when this
        process starts. Both MongoClient and KurrentDBClient are lazy (no
        network I/O until first use), so without this the first real
        failure would surface much later, inside whichever background
        thread happens to touch the database first."""
        config = self._config

        def on_attempt_failed(attempt: int, exc: Exception) -> None:
            error_code, description = classify_mongo_error(exc)
            self._logger.warning_event(
                Event.Mongo.CONNECT_FAILED,
                component=Component.MONGO,
                attempt=attempt,
                max_attempts=config.AXO_VEM_DB_CONNECT_MAX_ATTEMPTS,
                error_code=error_code,
                description=description,
            )

        t0 = time.monotonic()
        retry_with_backoff(
            lambda: db.command("ping"),
            max_attempts=config.AXO_VEM_DB_CONNECT_MAX_ATTEMPTS,
            base_delay_seconds=config.AXO_VEM_DB_CONNECT_BASE_DELAY_SECONDS,
            max_delay_seconds=config.AXO_VEM_DB_CONNECT_MAX_DELAY_SECONDS,
            on_attempt_failed=on_attempt_failed,
        )
        self._logger.info_event(
            Event.Mongo.CONNECTED,
            component=Component.MONGO,
            db_name=config.AXO_VEM_MONGO_DB_NAME,
            duration_ms=round((time.monotonic() - t0) * 1000, 2),
        )

    def _wait_for_kurrent(self, client) -> None:
        """Same eager retry-guarded probe as _wait_for_mongo, for
        KurrentDB -- this is what actually closes the race that killed
        both KurrentSubscriber and IngestionRouterServer at startup."""
        config = self._config

        def on_attempt_failed(attempt: int, exc: Exception) -> None:
            error_code, description = classify_kurrent_error(exc)
            self._logger.warning_event(
                Event.Kurrent.CONNECT_FAILED,
                component=Component.KURRENT,
                attempt=attempt,
                max_attempts=config.AXO_VEM_DB_CONNECT_MAX_ATTEMPTS,
                error_code=error_code,
                description=description,
            )

        def probe() -> None:
            list(client.read_all(backwards=True, limit=1))

        t0 = time.monotonic()
        retry_with_backoff(
            probe,
            max_attempts=config.AXO_VEM_DB_CONNECT_MAX_ATTEMPTS,
            base_delay_seconds=config.AXO_VEM_DB_CONNECT_BASE_DELAY_SECONDS,
            max_delay_seconds=config.AXO_VEM_DB_CONNECT_MAX_DELAY_SECONDS,
            on_attempt_failed=on_attempt_failed,
        )
        self._logger.info_event(
            Event.Kurrent.CONNECTED,
            component=Component.KURRENT,
            duration_ms=round((time.monotonic() - t0) * 1000, 2),
        )

    def start(self) -> None:
        """Starts the ingestion ROUTER and Kurrent subscriber threads, then
        blocks running FastAPI on the calling (main) thread."""

        self._ingestion.start()
        self._subscriber_thread = threading.Thread(target=self._subscriber.run_forever, daemon=True)
        self._subscriber_thread.start()
        self._stats_poller_thread = threading.Thread(target=self._stats_poller.run_forever, daemon=True)
        self._stats_poller_thread.start()
        self._endpoint_liveness_thread = threading.Thread(
            target=self._endpoint_liveness_worker.run_forever, daemon=True,
        )
        self._endpoint_liveness_thread.start()
        self._reconciler_thread = threading.Thread(
            target=self._reconciler.run_forever,
            args=(self._stop_event, self._config.AXO_VEM_RECONCILE_INTERVAL_SECONDS),
            daemon=True,
        )
        self._reconciler_thread.start()
        self._policy_thread = threading.Thread(
            target=self._policy_evaluator.run_forever,
            args=(self._stop_event, self._config.AXO_VEM_POLICY_INTERVAL_SECONDS),
            daemon=True,
        )
        self._policy_thread.start()
        self._cp_thread = threading.Thread(
            target=self._cp_link.run_forever,
            args=(self._stop_event, self._config.AXO_VEM_CP_HEARTBEAT_SECONDS),
            daemon=True,
        )
        self._cp_thread.start()

        self._logger.info_event(
            Event.Server.STARTED,
            component=Component.SERVER,
            router_bind=self._config.AXO_VEM_ROUTER_BIND,
            http_host=self._config.AXO_VEM_HTTP_HOST,
            http_port=self._config.AXO_VEM_HTTP_PORT,
        )

        uvicorn_config = uvicorn.Config(
            self._app,
            host      = self._config.AXO_VEM_HTTP_HOST,
            port      = self._config.AXO_VEM_HTTP_PORT,
            log_level = "warning",
        )
        uvicorn.Server(uvicorn_config).run()

    def stop(self) -> None:
        """Stops the ingestion ROUTER and signals the Kurrent subscriber,
        stats poller and liveness worker to stop at their next
        loop iteration (FastAPI itself stops when start()'s
        uvicorn.Server.run() returns, e.g. on SIGINT/SIGTERM)."""
        self._ingestion.stop()
        self._subscriber.stop()
        self._stats_poller.stop()
        self._endpoint_liveness_worker.stop()
        self._stop_event.set()
        self._cp_link.close()
        self._logger.info_event(Event.Server.STOPPED, component=Component.SERVER)

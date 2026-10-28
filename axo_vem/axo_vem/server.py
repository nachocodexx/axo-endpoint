from __future__ import annotations

from typing import Any, Dict, Optional

import json
import threading
import time

from axo_vem.application.choreography.engine import ChoreographyEngine
from axo_vem.application.choreography.events import EventsService
from axo_vem.application.choreography.jobs import EndpointStepJobs
from axo_vem.application.choreography.purge_choreography import PurgeChoreographyUseCase
from axo_vem.application.choreography.service import ChoreographyService, StepCatalog
from axo_vem.application.compute.delete_function import DeleteFunctionUseCase
from axo_vem.application.compute.endpoint_picker import EndpointPicker
from axo_vem.application.compute.function_secrets import FunctionSecrets
from axo_vem.application.compute.default_version import FunctionDefaults
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
from axo_vem.application.nodes.launch_endpoint_node import LaunchEndpointNodeUseCase, object_backend_env
from axo_vem.application.nodes.purge_endpoint import PurgeEndpointUseCase
from axo_vem.application.projector.compute_handler import apply_reading
from axo_vem.application.virtual_environment.history_cleanup import HistoryCleanup
from axo_vem.application.projector.dispatcher import apply_event
from axo_vem.application.projector.handlers import ProjectorHandlers
from axo_vem.config import Config
from axo_vem.domain.events import models
from axo_vem.infrastructure.auth.service_token import build_service_token_dependency
from axo_vem.application.virtual_environment.settings_service import VeSettingsService
from axo_vem.domain.compute.runner_size import with_runner_size
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
from axo_vem.application.objects.service import ObjectService
from axo_vem.infrastructure.database.mongo.object_repository import MongoActiveObjectRepository, MongoObjectClassRepository
from axo_vem.infrastructure.database.mongo.function_repository import MongoFunctionRepository
from axo_vem.infrastructure.database.mongo.job_attempt_repository import MongoJobAttemptRepository
from axo_vem.infrastructure.database.mongo.job_repository import MongoJobRepository
from axo_vem.application.jobs.results import MongoJobResultStore, StorageJobResultStore
from axo_vem.application.jobs.service import JobsService
from axo_vem.application.visibility import VisibilityService, creator_lookup
from axo_vem.infrastructure.resilience.errors import classify_kurrent_error, classify_mongo_error
from axo_vem.infrastructure.resilience.retry import retry_with_backoff
from axo_vem.infrastructure.transport.api.app import create_app
from axo_vem.infrastructure.transport.ws.broadcaster import Broadcaster
from axo_vem.infrastructure.transport.zmq_command.endpoint_liveness_worker import EndpointLivenessWorker
from axo_vem.infrastructure.transport.zmq_ingestion.router_server import IngestionRouterServer
from axo_vem.log import Log
from axo_vem.log.catalog import Component, Event
from axo_shared.container.spawner import ContainerSpawner
from axo_shared.metrics import MetricAggregator
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
        self._read_collections = read_collections

        activity_repository            = MongoActivityRepository(db["unified_activity"])
        endpoint_repository            = MongoEndpointRepository(db["endpoints"])
        function_repository            = MongoFunctionRepository(db["functions"])
        consensus_repository           = MongoConsensusRepository(db["consensus"])
        job_attempt_repository         = MongoJobAttemptRepository(db["job_attempts"])
        job_repository                 = MongoJobRepository(db["jobs"], attempts=job_attempt_repository)
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
                object_max_state_bytes=config.AXO_VEM_PLAN_OBJECT_MAX_STATE_BYTES,
                object_hot_max_bytes=config.AXO_VEM_PLAN_OBJECT_HOT_MAX_BYTES,
                object_keep_revisions=config.AXO_VEM_PLAN_OBJECT_KEEP_REVISIONS,
                object_max_keep_revisions=config.AXO_VEM_PLAN_OBJECT_MAX_KEEP_REVISIONS,
                history_grace_days=config.AXO_VEM_HISTORY_GRACE_DAYS,
                runner_cpu_cores=config.AXO_VEM_PLAN_RUNNER_CPU_CORES,
                runner_memory_mb=config.AXO_VEM_PLAN_RUNNER_MEMORY_MB,
                runner_max_cpu_cores=config.AXO_VEM_PLAN_RUNNER_MAX_CPU_CORES,
                runner_max_memory_mb=config.AXO_VEM_PLAN_RUNNER_MAX_MEMORY_MB,
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
            object_env             = object_backend_env(config.AXO_VEM_NODE_OBJECT_MONGO_URI,
                                                        config.AXO_VEM_NODE_OBJECT_REDIS_URI,
                                                        config.AXO_VEM_NODE_OBJECT_BLOBS),
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
        # Secret env values: only here (Restoring needs them), never shown.
        function_secrets = FunctionSecrets(db["function_secrets"])
        self._restorer: Optional[VeRestorer] = None
        if self._storage_client is not None:
            code_store = FunctionCodeStore(self._storage_client, config.AXO_VEM_VE_ID)
            self._restorer = VeRestorer(function_repository, code_store, endpoint_picker, timeout,
                                        secrets=function_secrets)
        self._lifecycle = VeLifecycleService(
            self._ve_settings, self._fleet, self._reconciler, self._reconciler_ops, self._jobs, appender,
            restorer=self._restorer, has_leader=has_leader, drain_timeout_s=config.AXO_VEM_ENDPOINT_DRAIN_TIMEOUT_SECONDS,
            before_settled=lambda: self._cp_link.settle(config.AXO_VEM_CP_FLUSH_TIMEOUT_SECONDS),
        )
        register_function_use_case = RegisterFunctionUseCase(
            config.AXO_VEM_VE_ID, endpoint_picker, timeout, code_store=code_store,
            function_repository=function_repository, secrets=function_secrets, runner_size=self._runner_size,
        )
        delete_function_use_case = DeleteFunctionUseCase(
            function_repository, endpoint_repository, job_repository, timeout,
        )
        update_function_use_case = UpdateFunctionUseCase(function_repository, endpoint_repository, timeout,
                                                         secrets=function_secrets, runner_size=self._runner_size)
        purge_function_version_use_case = PurgeFunctionVersionUseCase(
            db["functions"], activity_repository, appender,
        )
        # Every job's record, attempts and kept result (the VE's results
        # bucket, or this VEM's Mongo without axo_storage).
        job_results = (StorageJobResultStore(self._storage_client, config.AXO_VEM_VE_ID)
                       if self._storage_client is not None else MongoJobResultStore(db["job_results"]))
        self._jobs_service = JobsService(
            job_repository, job_attempt_repository, job_results, endpoint_repository,
            settings_fn=self._ve_settings.current, event_log=appender, broadcaster=broadcaster,
            forward_attempt=lambda record: self._cp_link.job_attempt(record),
            virtual_environment_id=config.AXO_VEM_VE_ID, command_timeout_seconds=timeout, logger=self._logger,
        )
        self._sweep_results_soon = threading.Event()
        self._ve_settings.on_change(lambda _settings: self._sweep_results_soon.set())
        submit_function_job_use_case = SubmitFunctionJobUseCase(function_repository, endpoint_picker, timeout,
                                                                jobs=self._jobs_service)
        self._submit_function_job = submit_function_job_use_case
        object_class_repository = MongoObjectClassRepository(db["object_classes"])
        active_object_repository = MongoActiveObjectRepository(db["active_objects"])
        object_service = ObjectService(
            config.AXO_VEM_VE_ID, endpoint_picker, register_function_use_case,
            object_class_repository, active_object_repository, appender, timeout,
            limits_fn=lambda: self._ve_settings.limits, delete_function=delete_function_use_case,
        )
        object_service.jobs = self._jobs_service
        purge_endpoint_use_case = PurgeEndpointUseCase(
            db["endpoints"], activity_repository, function_repository, job_repository, appender,
        )
        # Choreographies: the blueprints (versions, triggers) and the engine
        # running them; a step's answer is its job's result (the results bucket).
        function_defaults = FunctionDefaults(db["functions"], db["function_defaults"], activity_repository,
                                             config.AXO_VEM_VE_ID)
        step_catalog = StepCatalog(function_repository, object_service.lookup, default_version=function_defaults.version_of)
        self._choreographies = ChoreographyService(
            choreography_repository, choreography_run_repository, step_catalog, appender, config.AXO_VEM_VE_ID,
        )
        # Who sees runs and results (functions, choreographies, objects).
        visibility_service = VisibilityService(
            db["result_visibility"], appender, creator_of=creator_lookup(db["functions"], db["active_objects"], choreography_repository),
            virtual_environment_id=config.AXO_VEM_VE_ID,
        )
        # Statistics the VEM makes itself (choreography runs, the endpoints'
        # CPU / memory), drained into the live tier every few seconds.
        self._vem_metrics = MetricAggregator(width_s=10)
        self._choreography_engine = ChoreographyEngine(
            self._choreographies, choreography_repository, choreography_run_repository, step_catalog,
            EndpointStepJobs(submit_function_job_use_case, object_service, endpoint_repository, timeout,
                             job_records=self._jobs_service),
            appender,
            bucket_storage        = bucket_storage,
            results               = self._jobs_service,
            broadcaster           = broadcaster,
            metrics               = self._vem_metrics,
            virtual_environment_id = config.AXO_VEM_VE_ID,
            step_timeout_seconds  = config.AXO_VEM_CHOREOGRAPHY_STEP_TIMEOUT_SECONDS,
            poll_interval_seconds = config.AXO_VEM_CHOREOGRAPHY_POLL_INTERVAL_SECONDS,
            workers               = config.AXO_VEM_CHOREOGRAPHY_WORKERS,
        )
        # Events (people's own + file.written) and the VE's event types.
        self._events = EventsService(db["events"], db["event_types"], choreography_repository,
                                     self._choreography_engine.on_event, config.AXO_VEM_VE_ID)
        self._history_cleanup = HistoryCleanup(
            activity_repository, job_repository, choreography_run_repository,
            self._ve_settings.deletion_cutoff, events=self._events,
        )
        purge_choreography_use_case = PurgeChoreographyUseCase(
            choreography_repository, choreography_run_repository, activity_repository,
        )
        self._stats_poller = EndpointStatsPoller(
            endpoints        = read_collections.endpoints,
            spawner          = container_spawner,
            mode             = config.AXO_VEM_DOCKER_MODE,
            broadcaster      = broadcaster,
            interval_seconds = config.AXO_VEM_STATS_POLL_INTERVAL_SECONDS,
            logger           = self._logger,
            metrics          = self._vem_metrics,
            cpu_cores_of     = lambda endpoint_id: getattr(self._fleet.get(endpoint_id), "cpu_cores", None),
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

        link_kwargs = dict(status_fn=self._lifecycle.status, metrics=self._metrics, event_log=appender,
                           objects=active_object_repository,
                           triggers_fn=choreography_repository.trigger_buckets)
        self._cp_link = (
            ControlPlaneLink.connect(config.AXO_VEM_CP_URI, config.AXO_VEM_CP_OUTBOX_PATH, config.AXO_VEM_VE_ID,
                                     **link_kwargs)
            if config.AXO_VEM_CP_URI else ControlPlaneLink(config.AXO_VEM_VE_ID, **link_kwargs)
        )
        self._choreographies.on_triggers_changed = self._cp_link.heartbeat

        self._ingestion = IngestionRouterServer(
            bind_address       = config.AXO_VEM_ROUTER_BIND,                   appender = appender, logger = self._logger,
            local_handlers     = {
                models.METRIC_SUMMARY_REPORTED: lambda data: self._metrics.ingest(data) >= 0,
                # Readings, not events: the endpoint's current figures only.
                models.ENDPOINT_METRICS_REPORTED: lambda data: apply_reading(endpoint_repository, data),
                # Kept here (traceback too), then sent on to the CP without it.
                models.JOB_ATTEMPT_RECORDED: lambda data: self._jobs_service.on_attempt(data),
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
            object_class_repository        = object_class_repository,
            active_object_repository       = active_object_repository,
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
            jobs_service                          = self._jobs_service,
            function_code_store                   = code_store,
            function_defaults                     = function_defaults,
            choreography_service                  = self._choreographies,
            choreography_engine                   = self._choreography_engine,
            purge_choreography_use_case           = purge_choreography_use_case,
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
            object_service                         = object_service,
            visibility_service                     = visibility_service,
            events_service                         = self._events,
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
        # A start the VEM was in the middle of when it went down.
        self._lifecycle.resume_unfinished()
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
        # Runs that were going before a restart carry on; the runs' 10 s
        # statistics join the endpoints' (and so the CP's minute summaries).
        self._choreographies.migrate_legacy_triggers()   # bucket triggers from before versions held them
        self._choreography_engine.recover()
        self._vem_metrics_thread = threading.Thread(target=self._drain_vem_metrics, daemon=True)
        self._vem_metrics_thread.start()
        self._history_cleanup_thread = threading.Thread(target=self._clean_history, daemon=True)
        self._history_cleanup_thread.start()
        self._result_sweep_thread = threading.Thread(target=self._sweep_results, daemon=True)
        self._result_sweep_thread.start()
        self._lost_jobs_thread = threading.Thread(target=self._rescue_lost_jobs, daemon=True)
        self._lost_jobs_thread.start()

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

    def _runner_size(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """A container function's runner size, against the plan's limits
        and the size of the VE's new endpoints (see with_runner_size)."""
        settings = self._ve_settings.current()
        return with_runner_size(spec, self._ve_settings.limits, settings.new_endpoint_cpu_cores,
                                settings.new_endpoint_memory_mb)

    def _drain_vem_metrics(self) -> None:
        while not self._stop_event.wait(5.0):
            self._metrics.add(self._vem_metrics.drain_closed())

    def _sweep_results(self) -> None:
        """Every AXO_VEM_RESULT_SWEEP_INTERVAL_SECONDS (and right after the
        VE's settings change): job results past their keep time are deleted."""
        while not self._stop_event.is_set():
            try:
                deleted = self._jobs_service.sweep()
                if deleted:
                    self._logger.info_event(Event.Jobs.RESULTS_DELETED, component=Component.SERVER, results=deleted)
            except Exception as exc:  # storage / Mongo down: try again next time
                self._logger.warning_event(Event.Jobs.RESULT_SWEEP_FAILED, component=Component.SERVER,
                                           error=str(exc))
            self._sweep_results_soon.wait(self._config.AXO_VEM_RESULT_SWEEP_INTERVAL_SECONDS)
            self._sweep_results_soon.clear()

    def _rescue_lost_jobs(self) -> None:
        """Every 15 s: jobs left on an endpoint that stopped or can't be
        reached are tried again elsewhere (a free platform retry)."""
        while not self._stop_event.wait(15.0):
            try:
                lost = [d["_id"] for d in self._read_collections.endpoints.find(
                    {"status": {"$in": ["stopped", "unreachable"]}}, {"_id": 1})]
                rescued = self._jobs_service.rescue_lost(lost, self._submit_function_job.resubmit)
                if rescued:
                    self._logger.info_event(Event.Jobs.LOST_RESCUED, component=Component.SERVER, jobs=rescued)
            except Exception as exc:  # Mongo down: next time
                self._logger.warning_event(Event.Jobs.LOST_RESCUE_FAILED, component=Component.SERVER,
                                           error=str(exc))

    def _clean_history(self) -> None:
        """At start, then every AXO_VEM_HISTORY_PURGE_INTERVAL_SECONDS: history
        past the VE's retention + grace days is deleted (HistoryCleanup)."""
        while True:
            try:
                deleted = self._history_cleanup.run()
                if any(deleted.values()):
                    self._logger.info_event(Event.ActivityRetention.PURGED, component=Component.SERVER, **deleted)
            except Exception as exc:  # Mongo down: try again next time
                self._logger.warning_event(Event.ActivityRetention.TICK_FAILED, component=Component.SERVER,
                                           error=str(exc))
            if self._stop_event.wait(self._config.AXO_VEM_HISTORY_PURGE_INTERVAL_SECONDS):
                return

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
        self._choreography_engine.shutdown()
        self._sweep_results_soon.set()
        self._jobs_service.shutdown()
        self._cp_link.close()
        self._logger.info_event(Event.Server.STOPPED, component=Component.SERVER)

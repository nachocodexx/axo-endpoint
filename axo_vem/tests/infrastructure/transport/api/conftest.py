import mongomock
import pytest
from fastapi.testclient import TestClient

from axo_vem.application.compute.delete_function import DeleteFunctionUseCase
from axo_vem.application.compute.purge_function_version import PurgeFunctionVersionUseCase
from axo_vem.application.compute.register_function import RegisterFunctionUseCase
from axo_vem.application.compute.submit_function_job import SubmitFunctionJobUseCase
from axo_vem.application.compute.update_function import UpdateFunctionUseCase
from axo_vem.application.nodes.purge_endpoint import PurgeEndpointUseCase
from axo_vem.application.projector.dispatcher import apply_event
from axo_vem.application.projector.handlers import ProjectorHandlers
from axo_vem.infrastructure.database.mongo.activity_repository import MongoActivityRepository
from axo_vem.infrastructure.database.mongo.choreography_repository import MongoChoreographyRepository
from axo_vem.infrastructure.database.mongo.choreography_run_repository import MongoChoreographyRunRepository
from axo_vem.application.choreography.cancel_choreography_run import CancelChoreographyRunUseCase
from axo_vem.application.choreography.create_choreography import CreateChoreographyUseCase
from axo_vem.application.choreography.delete_choreography import DeleteChoreographyUseCase
from axo_vem.application.choreography.purge_choreography import PurgeChoreographyUseCase
from axo_vem.application.choreography.run_choreography import RunChoreographyUseCase
from axo_vem.application.choreography.update_choreography import UpdateChoreographyUseCase
from axo_vem.infrastructure.database.mongo.collections import ReadCollections
from axo_vem.infrastructure.database.mongo.consensus_repository import MongoConsensusRepository
from axo_vem.infrastructure.database.mongo.endpoint_repository import MongoEndpointRepository
from axo_vem.infrastructure.database.mongo.function_repository import MongoFunctionRepository
from axo_vem.infrastructure.database.mongo.job_repository import MongoJobRepository
from axo_vem.application.compute.endpoint_picker import EndpointPicker
from axo_vem.infrastructure.auth.service_token import ACTING_USER_HEADER, ActingUser, build_service_token_dependency
from axo_vem.infrastructure.transport.api.app import create_app


class _FakeKurrentReader:
    def __init__(self, streams=None):
        self._streams = streams or {}

    def get_stream(self, stream_name):
        if stream_name not in self._streams:
            raise KeyError(stream_name)
        return self._streams[stream_name]


class _FakeAppender:
    """Applies straight to the read model synchronously, standing in for the
    real Kurrent subscriber's async catch-up subscription so route tests
    don't need to poll/wait for eventual consistency."""

    def __init__(self, handlers):
        self._handlers = handlers
        self.appended = []

    def append_to_stream(self, stream_name, event_type, data):
        self.appended.append((stream_name, event_type, data))
        apply_event(self._handlers, event_type, data)


SERVICE_TOKEN = "cp-token"
FAKE_USER = ActingUser(key="user-1")
# What the Control Plane sends on every call.
CP_HEADERS = {"Authorization": f"Bearer {SERVICE_TOKEN}", ACTING_USER_HEADER: FAKE_USER.key}


def acting_as(user_id):
    """Headers for a call the Control Plane makes on behalf of another user."""
    return {**CP_HEADERS, ACTING_USER_HEADER: user_id}


def _fake_current_user():
    """For tests building their own minimal app without the token check."""
    return FAKE_USER


@pytest.fixture
def db():
    return mongomock.MongoClient()["test"]


@pytest.fixture
def collections(db):
    return ReadCollections(endpoints=db["endpoints"], functions=db["functions"], consensus=db["consensus"])


@pytest.fixture
def activity_repository(db):
    return MongoActivityRepository(db["unified_activity"])


@pytest.fixture
def endpoint_repository(db):
    return MongoEndpointRepository(db["endpoints"])


@pytest.fixture
def function_repository(db):
    return MongoFunctionRepository(db["functions"])


@pytest.fixture
def consensus_repository(db):
    return MongoConsensusRepository(db["consensus"])


@pytest.fixture
def job_repository(db):
    return MongoJobRepository(db["jobs"])


@pytest.fixture
def choreography_repository(db):
    return MongoChoreographyRepository(db["choreographies"])


@pytest.fixture
def choreography_run_repository(db):
    return MongoChoreographyRunRepository(db["choreography_runs"])


@pytest.fixture
def projector_handlers(
    activity_repository, endpoint_repository, function_repository, consensus_repository, job_repository,
    choreography_repository,
):
    return ProjectorHandlers(
        activity_recorder=activity_repository,
        endpoint_repository=endpoint_repository,
        function_repository=function_repository,
        consensus_recorder=consensus_repository,
        job_repository=job_repository,
        choreography_repository=choreography_repository,
    )


@pytest.fixture
def kurrent_appender(projector_handlers):
    return _FakeAppender(projector_handlers)


@pytest.fixture
def fake_kurrent_reader():
    return _FakeKurrentReader()


@pytest.fixture
def register_function_use_case(endpoint_repository):
    return RegisterFunctionUseCase("ve1", EndpointPicker(endpoint_repository), 0.3)


@pytest.fixture
def delete_function_use_case(function_repository, endpoint_repository, job_repository):
    return DeleteFunctionUseCase(function_repository, endpoint_repository, job_repository, 0.3)


@pytest.fixture
def update_function_use_case(function_repository, endpoint_repository):
    return UpdateFunctionUseCase(function_repository, endpoint_repository, 0.3)


@pytest.fixture
def purge_function_version_use_case(db, activity_repository):
    return PurgeFunctionVersionUseCase(db["functions"], activity_repository)


@pytest.fixture
def submit_function_job_use_case(function_repository, endpoint_repository):
    return SubmitFunctionJobUseCase(function_repository, EndpointPicker(endpoint_repository), 0.3)


@pytest.fixture
def purge_endpoint_use_case(db, activity_repository, function_repository, job_repository, kurrent_appender):
    return PurgeEndpointUseCase(db["endpoints"], activity_repository, function_repository, job_repository, kurrent_appender)


@pytest.fixture
def create_choreography_use_case(kurrent_appender):
    return CreateChoreographyUseCase(kurrent_appender)


@pytest.fixture
def update_choreography_use_case(choreography_repository, choreography_run_repository, kurrent_appender):
    return UpdateChoreographyUseCase(choreography_repository, choreography_run_repository, kurrent_appender)


@pytest.fixture
def delete_choreography_use_case(choreography_repository, choreography_run_repository, kurrent_appender):
    return DeleteChoreographyUseCase(choreography_repository, choreography_run_repository, kurrent_appender)


@pytest.fixture
def purge_choreography_use_case(db, activity_repository):
    return PurgeChoreographyUseCase(db["choreographies"], db["choreography_runs"], activity_repository)


@pytest.fixture
def run_choreography_use_case(
    choreography_repository, choreography_run_repository, function_repository, endpoint_repository,
    submit_function_job_use_case,
):
    return RunChoreographyUseCase(
        choreography_repository=choreography_repository,
        run_repository=choreography_run_repository,
        function_repository=function_repository,
        endpoint_repository=endpoint_repository,
        submit_function_job_use_case=submit_function_job_use_case,
    )


@pytest.fixture
def cancel_choreography_run_use_case(choreography_repository, choreography_run_repository, run_choreography_use_case):
    return CancelChoreographyRunUseCase(
        choreography_repository=choreography_repository,
        run_repository=choreography_run_repository,
        run_use_case=run_choreography_use_case,
    )


@pytest.fixture
def client(
    collections, activity_repository, job_repository,
    choreography_repository, choreography_run_repository,
    create_choreography_use_case, update_choreography_use_case, delete_choreography_use_case,
    purge_choreography_use_case, run_choreography_use_case, cancel_choreography_run_use_case,
    register_function_use_case, delete_function_use_case, update_function_use_case,
    purge_function_version_use_case, purge_endpoint_use_case, submit_function_job_use_case,
    fake_kurrent_reader,
):
    """Goes through the real service-token check: every request carries the
    Control Plane's token and the acting user ("user-1")."""
    app = create_app(
        collections=collections,
        activity_repository=activity_repository,
        job_repository=job_repository,
        choreography_repository=choreography_repository,
        choreography_run_repository=choreography_run_repository,
        create_choreography_use_case=create_choreography_use_case,
        update_choreography_use_case=update_choreography_use_case,
        delete_choreography_use_case=delete_choreography_use_case,
        purge_choreography_use_case=purge_choreography_use_case,
        run_choreography_use_case=run_choreography_use_case,
        cancel_choreography_run_use_case=cancel_choreography_run_use_case,
        register_function_use_case=register_function_use_case,
        delete_function_use_case=delete_function_use_case,
        update_function_use_case=update_function_use_case,
        purge_function_version_use_case=purge_function_version_use_case,
        submit_function_job_use_case=submit_function_job_use_case,
        purge_endpoint_use_case=purge_endpoint_use_case,
        kurrent_reader=fake_kurrent_reader,
        current_user_dependency=build_service_token_dependency(SERVICE_TOKEN),
        endpoint_command_timeout_seconds=0.3,
    )
    return TestClient(app, headers=CP_HEADERS)


@pytest.fixture
def anonymous_client(client):
    """Same app, no Control Plane credentials."""
    return TestClient(client.app)

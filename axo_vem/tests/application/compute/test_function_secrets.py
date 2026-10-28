import mongomock
import pytest

from axo_vem.application.compute.function_secrets import FunctionSecrets, masked
from axo_vem.domain.errors import NotOwnerError
from axo_vem.infrastructure.auth.service_token import ActingUser
from axo_vem.infrastructure.transport.api.controllers.functions import _may_delete, _shown

SPEC = {"type": "process", "env_vars": {"REGION": "mx", "TOKEN": "s3cret"}, "secret_env": ["TOKEN"]}


def test_a_secret_is_kept_only_here_and_put_back_for_restoring():
    secrets = FunctionSecrets(mongomock.MongoClient()["vem"]["function_secrets"])
    secrets.keep("f", 1, SPEC)
    shown = masked(SPEC)
    assert shown["env_vars"] == {"REGION": "mx", "TOKEN": None}
    assert secrets.filled("f", 1, shown)["env_vars"] == {"REGION": "mx", "TOKEN": "s3cret"}
    secrets.keep("f", 1, {"secret_env": ["TOKEN"], "env_vars": {"TOKEN": None}})   # sent back blank: kept
    assert secrets.values("f", 1) == {"TOKEN": "s3cret"}
    secrets.keep("f", 1, {"secret_env": [], "env_vars": {}}, replace=True)        # no longer secret
    assert secrets.values("f", 1) == {}


def test_viewers_see_env_var_names_only_and_nobody_sees_a_secret():
    doc = {"_id": "f:1", "function_id": "f", "version": 1, "runtime_spec": SPEC}
    assert _shown(doc, ActingUser(key="v", role="viewer"))["runtime_spec"]["env_vars"] == {"REGION": None,
                                                                                         "TOKEN": None}
    assert _shown(doc, ActingUser(key="d", role="developer"))["runtime_spec"]["env_vars"] == {"REGION": "mx",
                                                                                            "TOKEN": None}


def test_admins_delete_any_version_developers_only_their_own():
    doc = {"registered_by": "ana"}
    _may_delete(doc, ActingUser(key="boss", role="admin"))
    _may_delete(doc, ActingUser(key="ana", role="developer"))
    with pytest.raises(NotOwnerError):
        _may_delete(doc, ActingUser(key="bob", role="developer"))

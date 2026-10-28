import pytest

from axo_control_plane.domain import roles as R
from axo_control_plane.domain.errors import InvalidError
from axo_control_plane.domain.names import (
    check_owner_name, code_bucket_for, new_ve_id, results_bucket_for, split_bucket_name, ve_of_results_bucket,
)
from axo_control_plane.domain.plans import DEFAULT_PLANS, ENDPOINT_SIZES, Resources, VeShape, check_shape, endpoint_size

PLANS = {p.plan_id: p for p in DEFAULT_PLANS}


def shape(target, size="small", quota=Resources(4.0, 8192)):
    return VeShape(quota=quota, endpoint_size=ENDPOINT_SIZES[size], endpoint_size_name=size,
                   target_endpoints=target, min_endpoints=min(1, target))


def test_target_must_fit_the_plan_and_the_quota():
    check_shape(PLANS["basic"], shape(3))
    with pytest.raises(InvalidError) as too_many:
        check_shape(PLANS["basic"], shape(4, quota=Resources(8.0, 16384)))
    assert too_many.value.code == "PLAN_LIMIT"
    with pytest.raises(InvalidError) as too_big:
        check_shape(PLANS["standard"], shape(3, size="large", quota=Resources(6.0, 16384)))
    assert too_big.value.code == "QUOTA_TOO_SMALL"
    assert "need 12 CPU" in too_big.value.message


def test_free_plan_uses_small_endpoints_only():
    with pytest.raises(InvalidError):
        check_shape(PLANS["free"], shape(1, size="medium"))


def test_custom_endpoint_size_is_limited():
    assert endpoint_size("custom", 3, 3072, Resources(8, 16384)) == Resources(3.0, 3072)
    with pytest.raises(InvalidError):
        endpoint_size("custom", 16, 1024, Resources(8, 16384))
    with pytest.raises(InvalidError):
        endpoint_size("huge")


def test_names_ids_and_bucket_names():
    assert check_owner_name("Data-Team") == "data-team"
    for bad in ("-x", "a b", "axo", "admin", "x" * 40):
        with pytest.raises(InvalidError):
            check_owner_name(bad)
    ve_id = new_ve_id("ml-prod")
    assert ve_id.startswith("ml-prod-") and len(ve_id) == len("ml-prod-") + 4
    assert split_bucket_name("alice/reports") == ("alice", "reports")
    for bad in ("reports", "alice/", "/reports", "alice/a/b"):
        with pytest.raises(InvalidError):
            split_bucket_name(bad)
    assert code_bucket_for("ml-prod-7f3k") == "axo/ve-ml-prod-7f3k-functions"
    assert results_bucket_for("ml-prod-7f3k") == "axo/ve-ml-prod-7f3k-results"
    assert ve_of_results_bucket("axo/ve-ml-prod-7f3k-results") == "ml-prod-7f3k"
    assert ve_of_results_bucket("axo/ve-ml-prod-7f3k-functions") is None and ve_of_results_bucket("bkt-1") is None


def test_roles_from_cumulative_permissions():
    assert R.ve_role_from_permissions({"read"}) == R.VIEWER
    assert R.ve_role_from_permissions({"read", "write", "update"}) == R.DEVELOPER
    assert R.ve_role_from_permissions({"read", "write", "update", "delete"}) == R.ADMIN
    # Owner comes from Xolo's owner flag, never from permissions (claim gives no "update").
    assert R.ve_role_from_permissions({"read", "write", "delete", "sys:manage"}, is_owner=True) == R.OWNER
    assert R.ve_role_from_permissions(set()) is None
    assert R.strongest([R.VIEWER, None, R.ADMIN]) == R.ADMIN
    assert R.weakest(R.ADMIN, R.DEVELOPER) == R.DEVELOPER
    assert R.may_assign_ve_role(R.OWNER, R.ADMIN) and not R.may_assign_ve_role(R.ADMIN, R.ADMIN)
    assert R.may_assign_ve_role(R.ADMIN, R.DEVELOPER) and not R.may_assign_ve_role(R.DEVELOPER, R.VIEWER)

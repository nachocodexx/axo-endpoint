from axo_storage.access import READ, WRITE, AccessQuery, CachingAccessChecker, StaticAccessChecker

from tests.conftest import RULES


def _check(query, owner=None):
    return StaticAccessChecker(RULES, bucket_owner_fn=lambda b: owner).check(query).unwrap()


def test_direct_and_team_grants_combine():
    assert _check(AccessQuery("luis", "sales", READ)).allowed
    assert _check(AccessQuery("luis", "sales", WRITE)).allowed       # through team ml-lab
    assert _check(AccessQuery("ana", "sales", WRITE)).allowed        # team only
    assert not _check(AccessQuery("eve", "sales", READ)).allowed


def test_job_also_needs_developer_or_higher_in_its_ve():
    assert _check(AccessQuery("luis", "sales", READ, "ml-prod")).allowed      # developer
    denied = _check(AccessQuery("ana", "sales", READ, "ml-prod"))             # only viewer there
    assert not denied.allowed and "Developer" in denied.reason
    assert _check(AccessQuery("ana", "sales", READ, "ml-test")).allowed       # admin via team
    assert not _check(AccessQuery("luis", "sales", READ, "unknown-ve")).allowed


def test_bucket_creator_is_owner_and_quota_is_reported():
    assert _check(AccessQuery("carla", "new-bucket", WRITE), owner="carla").allowed
    assert _check(AccessQuery("luis", "sales", READ)).quota_bytes == 1000


def test_cache_reuses_answers_until_ttl_then_asks_again():
    calls = []

    class Counting(StaticAccessChecker):
        def check(self, query):
            calls.append(query)
            return super().check(query)

    now = [0.0]
    cached = CachingAccessChecker(Counting(RULES), ttl_seconds=5, now_fn=lambda: now[0])
    query = AccessQuery("luis", "sales", READ)
    cached.check(query)
    cached.check(query)
    assert len(calls) == 1
    now[0] = 6.0
    cached.check(query)
    assert len(calls) == 2


def test_checker_crash_becomes_an_unavailable_error_not_an_exception():
    class Broken(StaticAccessChecker):
        def check(self, query):
            raise ConnectionError("control plane down")

    result = CachingAccessChecker(Broken(RULES)).check(AccessQuery("luis", "sales", READ))
    assert result.is_err and result.unwrap_err().name == "STORAGE_ACCESS_CHECK_UNAVAILABLE"

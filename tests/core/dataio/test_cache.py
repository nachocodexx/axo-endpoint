import pytest

from axo_endpoint.core.dataio import ChunkCache


@pytest.fixture(params=["memory", "disk"])
def cache_factory(request, tmp_path):
    return lambda max_bytes: ChunkCache(max_bytes, None if request.param == "memory" else str(tmp_path / "cache"))


def test_hit_and_miss(cache_factory):
    cache = cache_factory(100)
    assert cache.get(("b", "x", 1, 0)) is None
    cache.put(("b", "x", 1, 0), b"abc")
    assert cache.get(("b", "x", 1, 0)) == b"abc"
    assert cache.stats()["hits"] == 1 and cache.stats()["misses"] == 1


def test_least_recently_used_is_dropped_past_max_bytes(cache_factory):
    cache = cache_factory(10)
    cache.put(("b", "x", 1, 0), b"aaaa")
    cache.put(("b", "x", 1, 1), b"bbbb")
    cache.get(("b", "x", 1, 0))            # 0 is now the most recently used
    cache.put(("b", "x", 1, 2), b"cccc")   # 12 bytes > 10: drop chunk 1
    assert cache.get(("b", "x", 1, 1)) is None
    assert cache.get(("b", "x", 1, 0)) == b"aaaa"
    assert cache.stats()["bytes"] == 8


def test_chunk_bigger_than_the_whole_cache_is_not_kept(cache_factory):
    cache = cache_factory(3)
    cache.put(("b", "x", 1, 0), b"abcd")
    assert cache.stats()["chunks"] == 0


def test_disk_cache_is_wiped_on_start_and_close(tmp_path):
    directory = tmp_path / "cache"
    first = ChunkCache(100, str(directory))
    first.put(("b", "x", 1, 0), b"abc")
    assert any(directory.rglob("*"))
    second = ChunkCache(100, str(directory))  # a restarted endpoint starts empty
    assert second.get(("b", "x", 1, 0)) is None and not any(p.is_file() for p in directory.rglob("*"))
    second.close()
    assert not directory.exists()

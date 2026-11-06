import cache_store
from cache_store import BoundedCache


def test_lru_respects_memory_and_entry_limits():
    cache = BoundedCache(2, 8, 60)
    cache.put("a", "aaaa", 1, 4)
    cache.put("b", "bbbb", 1, 4)
    assert cache.get("a", 1) == "aaaa"
    cache.put("c", "cccc", 1, 4)
    assert cache.get("b", 1) is None
    assert cache.bytes == 8
    cache.put("large", "x" * 9, 1, 9)
    assert cache.get("large", 1) is None
    cache.clear()
    assert cache.bytes == 0
    assert not cache.entries


def test_expiration_and_generation_remove_old_entries(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(cache_store.time, "monotonic", lambda: now[0])
    cache = BoundedCache(10, 100, 60)
    cache.put("old", "a", 1, 1)
    now[0] = 160.0
    assert cache.get("missing", 1) is None
    assert not cache.entries
    cache.put("current", "b", 1, 1)
    assert cache.get("current", 2) is None
    assert cache.bytes == 0

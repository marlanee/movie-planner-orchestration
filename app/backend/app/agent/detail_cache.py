import time
from copy import deepcopy
from threading import Lock


class DetailCache:
    def __init__(self, ttl_seconds=21600, clock=time.monotonic):
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = Lock()
        self._entries: dict[str, tuple[float, dict]] = {}

    def get(self, key: str) -> dict | None:
        if not key.startswith("tmdb:"):
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, result = entry
            if expires_at <= self._clock():
                del self._entries[key]
                return None
            return deepcopy(result)

    def put(self, key: str, result: dict) -> None:
        if not key.startswith("tmdb:") or result.get("ok") is not True:
            return
        result_copy = deepcopy(result)
        with self._lock:
            now = self._clock()
            self._remove_expired(now)
            self._entries[key] = (now + self._ttl_seconds, result_copy)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            self._remove_expired(self._clock())
            return len(self._entries)

    def _remove_expired(self, now: float) -> None:
        expired_keys = [key for key, (expires_at, _) in self._entries.items() if expires_at <= now]
        for key in expired_keys:
            del self._entries[key]


default_detail_cache = DetailCache()

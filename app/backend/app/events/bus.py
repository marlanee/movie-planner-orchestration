"""실행 흐름과 이벤트 구독자를 연결하는 이벤트 버스."""

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Lock


EVENT_TYPES = {
    "stage_start", "stage_end", "tool_call", "tool_result",
    "llm_decision", "replan", "error",
}
KST = timezone(timedelta(hours=9))


class EventBus:
    def __init__(self):
        self._lock = Lock()
        self._sequences: dict[str, int] = {}
        self._subscribers: dict[object, Callable[[dict], None]] = {}
        self.subscriber_errors = 0

    def subscribe(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        subscription = object()
        with self._lock:
            self._subscribers[subscription] = callback

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(subscription, None)

        return unsubscribe

    def publish(
        self,
        session_id: str,
        type: str,
        stage: str,
        payload: dict | None = None,
        duration_ms: int | None = None,
    ) -> dict:
        if isinstance(payload, dict):
            event_payload = payload.copy()
        elif payload is None:
            event_payload = {}
        else:
            event_payload = {"value": payload}
        if type not in EVENT_TYPES:
            event_payload["unknown_type"] = True

        with self._lock:
            seq = self._sequences.get(session_id, 0) + 1
            self._sequences[session_id] = seq
            ts = datetime.now(KST).isoformat(timespec="microseconds")
            subscribers = tuple(self._subscribers.values())

        event = {
            "session_id": session_id,
            "seq": seq,
            "ts": ts,
            "type": type,
            "stage": stage,
            "payload": event_payload,
            "duration_ms": duration_ms,
        }
        for callback in subscribers:
            try:
                callback(deepcopy(event))
            except Exception:
                with self._lock:
                    self.subscriber_errors += 1
        return event


class EventCollector:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.events: list[dict] = []
        self._unsubscribe: Callable[[], None] | None = None

    def attach(self, bus: EventBus) -> None:
        self.detach()
        self._unsubscribe = bus.subscribe(self._collect)

    def detach(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _collect(self, event: dict) -> None:
        if event["session_id"] == self.session_id:
            self.events.append(event)


bus = EventBus()

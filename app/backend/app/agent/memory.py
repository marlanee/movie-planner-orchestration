from threading import Lock

from app.agent.state import Conditions


class SessionMemory:
    def __init__(self):
        self._lock = Lock()
        self._sessions: dict[str, tuple[dict, dict]] = {}

    def get_conditions(self, session_id: str) -> dict | None:
        with self._lock:
            session = self._sessions.get(session_id)
            return session[0].copy() if session else None

    def get_summary(self, session_id: str) -> dict | None:
        with self._lock:
            session = self._sessions.get(session_id)
            return session[1].copy() if session else None

    def update(self, session_id: str, conditions: dict, summary: dict) -> None:
        selected_conditions = Conditions.model_validate(conditions).model_dump()
        selected_summary = {
            key: summary[key] for key in ("status", "replan_count", "recommendation_count", "excluded_count")
            if key in summary
        }
        with self._lock:
            self._sessions[session_id] = (selected_conditions, selected_summary)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


default_memory = SessionMemory()

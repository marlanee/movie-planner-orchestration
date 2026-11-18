"""실행 이벤트 추가, 세션별 조회와 삭제."""

import json

from .db import _transaction


def append_event(event: dict) -> None:
    with _transaction() as conn:
        conn.execute(
            """INSERT INTO run_logs (session_id, seq, ts, type, stage, payload, duration_ms)
               VALUES (:session_id, :seq, :ts, :type, :stage, :payload, :duration_ms)""",
            {**event, "payload": json.dumps(event["payload"], ensure_ascii=False, default=str)},
        )


def list_sessions() -> list[dict]:
    with _transaction() as conn:
        rows = conn.execute(
            """SELECT session_id, MIN(ts) AS first_ts, MAX(ts) AS last_ts,
                      COUNT(*) AS event_count,
                      SUM(CASE WHEN type = 'replan' THEN 1 ELSE 0 END) AS replan_count,
                      SUM(CASE WHEN type = 'error' THEN 1 ELSE 0 END) AS error_count
               FROM run_logs
               GROUP BY session_id
               ORDER BY last_ts DESC, session_id"""
        )
        return [dict(row) for row in rows]


def get_session_events(session_id: str) -> list[dict]:
    with _transaction() as conn:
        rows = conn.execute(
            """SELECT session_id, seq, ts, type, stage, payload, duration_ms
               FROM run_logs WHERE session_id = ? ORDER BY id""",
            (session_id,),
        )
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]


def delete_session(session_id: str) -> int:
    with _transaction() as conn:
        cursor = conn.execute("DELETE FROM run_logs WHERE session_id = ?", (session_id,))
        return cursor.rowcount

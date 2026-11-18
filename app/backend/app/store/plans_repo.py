"""관람 계획 저장, 조회, 수정과 시간 겹침 확인."""

import re
from datetime import date, time
from sqlite3 import Connection

from .db import _transaction, now_kst_iso


_REQUIRED_FIELDS = {
    "movie_nm": "영화명",
    "watch_date": "관람 날짜",
    "start_time": "시작 시각",
    "end_time": "종료 시각",
}
_PLAN_FIELDS = (
    "movie_nm", "tmdb_id", "watch_date", "start_time", "end_time",
    "theater_name", "theater_address", "memo", "session_id",
)


def _validate_plan(plan: dict) -> None:
    for field, label in _REQUIRED_FIELDS.items():
        value = plan[field]
        if value is None or isinstance(value, str) and not value.strip():
            raise ValueError(f"{label} 항목은 필수입니다.")

    if not isinstance(plan["movie_nm"], str):
        raise ValueError("영화명은 문자열이어야 합니다.")

    watch_date = plan["watch_date"]
    try:
        if not isinstance(watch_date, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}", watch_date
        ):
            raise ValueError
        date.fromisoformat(watch_date)
    except ValueError:
        raise ValueError("관람 날짜는 유효한 YYYY-MM-DD 형식이어야 합니다.") from None

    for field in ("start_time", "end_time"):
        value = plan[field]
        try:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
                raise ValueError
            time.fromisoformat(value)
        except ValueError:
            label = _REQUIRED_FIELDS[field]
            raise ValueError(f"{label}은 유효한 HH:MM 형식이어야 합니다.") from None

    if plan["end_time"] <= plan["start_time"]:
        raise ValueError("종료 시각은 시작 시각보다 늦어야 합니다.")


def _fetch_plan(conn: Connection, plan_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    return dict(row) if row is not None else None


def _find_overlaps(conn: Connection, plan: dict) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM plans
           WHERE watch_date = :watch_date AND plan_id != :plan_id
                 AND start_time < :end_time AND :start_time < end_time
           ORDER BY start_time, plan_id""",
        plan,
    )
    return [dict(row) for row in rows]


def create_plan(data: dict) -> tuple[dict, list[dict]]:
    plan = {field: data.get(field) for field in _PLAN_FIELDS}
    _validate_plan(plan)
    plan["created_at"] = now_kst_iso()

    with _transaction() as conn:
        row = conn.execute(
            """INSERT INTO plans (
                   movie_nm, tmdb_id, watch_date, start_time, end_time,
                   theater_name, theater_address, memo, session_id, created_at
               ) VALUES (
                   :movie_nm, :tmdb_id, :watch_date, :start_time, :end_time,
                   :theater_name, :theater_address, :memo, :session_id, :created_at
               ) RETURNING *""",
            plan,
        ).fetchone()
        stored = dict(row)
        return stored, _find_overlaps(conn, stored)


def list_plans(watch_date: str | None = None) -> list[dict]:
    with _transaction() as conn:
        query = "SELECT * FROM plans"
        params = ()
        if watch_date is not None:
            query += " WHERE watch_date = ?"
            params = (watch_date,)
        rows = conn.execute(query + " ORDER BY watch_date, start_time, plan_id", params)
        return [dict(row) for row in rows]


def get_plan(plan_id: int) -> dict | None:
    with _transaction() as conn:
        return _fetch_plan(conn, plan_id)


def update_plan(plan_id: int, data: dict) -> tuple[dict, list[dict]] | None:
    with _transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        plan = _fetch_plan(conn, plan_id)
        if plan is None:
            return None

        changes = {field: data[field] for field in _PLAN_FIELDS if field in data}
        plan.update(changes)
        _validate_plan(plan)
        changes["updated_at"] = now_kst_iso()
        assignments = ", ".join(f"{field} = :{field}" for field in changes)
        row = conn.execute(
            f"""UPDATE plans SET {assignments}, movie_cd = NULL, runtime_min = NULL
                WHERE plan_id = :plan_id RETURNING *""",
            {**changes, "plan_id": plan_id},
        ).fetchone()
        stored = dict(row)
        return stored, _find_overlaps(conn, stored)


def delete_plan(plan_id: int) -> bool:
    with _transaction() as conn:
        cursor = conn.execute("DELETE FROM plans WHERE plan_id = ?", (plan_id,))
        return cursor.rowcount > 0

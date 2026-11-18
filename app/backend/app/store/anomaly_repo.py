"""실행별 판정과 진단을 추가하고 조회한다."""

import json

from .db import _transaction, db_path, now_kst_iso


def append_report(session_id: str, report: dict, terminal_event: dict) -> int | None:
    with _transaction() as conn:
        # 판정 도중 세션이 삭제됐다면 늦게 도착한 보고서는 저장하지 않는다.
        cursor = conn.execute(
            """INSERT INTO anomaly_reports (
                   session_id, run_index, first_seq, last_seq, verdict,
                   warning_count, findings, missing_evidence, created_at
               )
               SELECT :session_id, :run_index, :first_seq, :last_seq, :verdict,
                      :warning_count, :findings, :missing_evidence, :created_at
               WHERE EXISTS (
                   SELECT 1 FROM run_logs
                   WHERE session_id = :session_id AND seq = :terminal_seq AND ts = :terminal_ts
               )
               ON CONFLICT (session_id, run_index) DO NOTHING""",
            {
                **report, "session_id": session_id,
                "findings": json.dumps(report["findings"], ensure_ascii=False),
                "missing_evidence": json.dumps(report["missing_evidence"], ensure_ascii=False),
                "created_at": now_kst_iso(),
                "terminal_seq": terminal_event["seq"], "terminal_ts": terminal_event["ts"],
            },
        )
        return cursor.lastrowid if cursor.rowcount == 1 else None


def append_diagnosis(report_id: int, diagnosis: dict, model: str) -> None:
    with _transaction() as conn:
        # 진단 중 세션이 삭제되면 완료된 진단을 폐기한다.
        conn.execute(
            """INSERT INTO anomaly_diagnoses (report_id, status, text, model, created_at)
               SELECT id, ?, ?, ?, ? FROM anomaly_reports WHERE id = ?""",
            (diagnosis["status"], diagnosis["text"], model, now_kst_iso(), report_id),
        )


def latest_verdict(session_id: str) -> dict | None:
    if not db_path().is_file():
        return None
    with _transaction() as conn:
        row = conn.execute(
            """SELECT run_index, verdict, warning_count, findings FROM anomaly_reports
               WHERE session_id = ? ORDER BY run_index DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "run_index": row["run_index"],
        "verdict": row["verdict"],
        "warning_count": row["warning_count"],
        "rules": list(dict.fromkeys(finding["rule"] for finding in json.loads(row["findings"]))),
    }


def list_reports(session_id: str) -> list[dict] | None:
    with _transaction() as conn:
        rows = conn.execute(
            """SELECT report.*, diagnosis.status AS diagnosis_status, diagnosis.text AS diagnosis_text
               FROM (SELECT session_id FROM run_logs WHERE session_id = ? LIMIT 1) AS session
               LEFT JOIN anomaly_reports AS report ON report.session_id = session.session_id
               LEFT JOIN anomaly_diagnoses AS diagnosis ON diagnosis.report_id = report.id
               ORDER BY report.run_index""",
            (session_id,),
        ).fetchall()
    if not rows:
        return None

    reports = []
    for row in rows:
        if row["id"] is None:
            continue
        report = dict(row)
        status = report.pop("diagnosis_status")
        diagnosis_text = report.pop("diagnosis_text")
        report["diagnosis"] = None if status is None else {"status": status, "text": diagnosis_text}
        report["findings"] = json.loads(report["findings"])
        report["missing_evidence"] = json.loads(report["missing_evidence"])
        reports.append(report)
    return reports

from fastapi import APIRouter, HTTPException, Response

from ..schemas import AnomalyReport, LogDetail, LogSession
from ..store import anomaly_repo, logs_repo


router = APIRouter(prefix="/api/logs", tags=["logs"])
NOT_FOUND = "그런 실행 로그가 없습니다."


@router.get("", response_model=list[LogSession])
def list_sessions() -> list[dict]:
    return logs_repo.list_sessions()


@router.get("/{session_id}", response_model=LogDetail)
def get_session(session_id: str) -> LogDetail:
    events = logs_repo.get_session_events(session_id)
    if not events:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    # 서버 재시작 후 같은 세션의 seq가 다시 1부터 시작할 수 있다.
    ordered = sorted(events, key=lambda event: (event["ts"], event["seq"]))
    return LogDetail(session_id=session_id, events=ordered)


@router.get("/{session_id}/anomalies", response_model=list[AnomalyReport])
def get_anomalies(session_id: str) -> list[dict]:
    reports = anomaly_repo.list_reports(session_id)
    if reports is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return reports


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: str) -> Response:
    if not logs_repo.delete_session(session_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(status_code=204)

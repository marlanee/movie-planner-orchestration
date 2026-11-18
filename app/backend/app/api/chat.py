from threading import Lock
from uuid import uuid4

from fastapi import APIRouter
from langsmith import tracing_context

from ..agent.graph import run_agent
from ..agent.nodes import ERROR_ANSWER, make_result
from ..config import has_key
from ..events.bus import EventCollector, bus
from ..schemas import ChatConfig, ChatRequest, ChatResponse, KeyStatus, PlanCandidate


router = APIRouter(prefix="/api/chat", tags=["chat"])
KEYS = {
    "kobis": ("KOBIS_API_KEY", "KOBIS"),
    "kakao": ("KAKAO_REST_API_KEY", "카카오"),
    "tmdb": ("TMDB_API_KEY", "TMDB"),
    "gms": ("GMS_KEY", "GMS"),
}
# 한 세션의 여러 요청이 대화 메모리와 이벤트 수집기를 동시에 사용하지 않도록 한다.
_SESSION_LOCKS = tuple(Lock() for _ in range(64))


def _plan_candidates(result: dict) -> list[PlanCandidate]:
    conditions = result["conditions"]
    return [
        PlanCandidate(
            movie_nm=conditions.get("movie_title"),
            tmdb_id=None if movie.get("movie_cd") else movie.get("tmdb_id"),
            watch_date=conditions.get("date"),
            start_time=conditions.get("start_after"),
        )
        for movie in result["recommendations"]
    ]


@router.get("/config", response_model=ChatConfig)
def get_config() -> ChatConfig:
    available = {field: has_key(key) for field, (key, _) in KEYS.items()}
    missing = [label for field, (_, label) in KEYS.items() if not available[field]]
    return ChatConfig(keys=KeyStatus(**available), missing_keys=missing)


@router.post("", response_model=ChatResponse)
def send_message(request: ChatRequest) -> ChatResponse:
    session_id = request.session_id if request.session_id is not None else uuid4().hex
    missing_keys = []
    try:
        missing_keys = get_config().missing_keys
        with _SESSION_LOCKS[hash(session_id) % len(_SESSION_LOCKS)]:
            collector = EventCollector(session_id)
            collector.attach(bus)
            try:
                # 그래프 상태에는 실시간 제공자 응답이 있으므로 외부 추적을 끈다.
                with tracing_context(enabled=False, parent=False):
                    result = run_agent(session_id, request.message)
            except Exception:
                result = make_result({}, "error", ERROR_ANSWER)
            finally:
                collector.detach()

        if result["status"] == "error":
            result = {**result, "answer": ERROR_ANSWER}
        return ChatResponse(
            **result,
            session_id=session_id,
            plan_candidates=_plan_candidates(result),
            trace=sorted(collector.events, key=lambda event: event["seq"]),
            missing_keys=missing_keys,
        )
    except Exception:
        return ChatResponse(
            session_id=session_id, status="error", answer=ERROR_ANSWER, missing_keys=missing_keys,
        )

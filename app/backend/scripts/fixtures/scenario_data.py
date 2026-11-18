"""대표 시나리오용으로 직접 만든 영화 데이터와 오프라인 대역."""

from datetime import date

from app.agent.state import Analysis, Conditions
from app.tools.base import BAD_ARGUMENT, NO_RESULT, fail, ok


TODAY = date(2026, 11, 18)
SATURDAY = "2026-11-21"

DAILY = (
    ("fixture-daily-1", "우편함을 빌린 구름", 110),
    ("fixture-daily-2", "새벽 세탁소의 지도", 98),
)
WEEKLY = (("fixture-weekly-1", "종이등을 접는 바다", 78),)
CURRENT = {
    1: (910001, "낮잠 우체국의 당번", 66),
    2: (910002, "귤빛 전파 수리점", 72),
}
KOBIS_DETAILS = {code: (title, runtime) for code, title, runtime in (*DAILY, *WEEKLY)}
TMDB_DETAILS = {movie_id: (title, runtime) for movie_id, title, runtime in CURRENT.values()}

ANALYSES = {
    "01_first_pool": Analysis(
        intent="plan_movie", reason="강남에서 영화 볼 일정을 정하려고 한다.", conditions=Conditions(region="강남")
    ),
    "02_replan": Analysis(
        intent="plan_movie", reason="앞서 요청한 날짜에 관람 가능 시간을 바꾸려고 한다.", conditions=Conditions()
    ),
    "03_limit": Analysis(
        intent="plan_movie", reason="짧은 시간 안에 볼 수 있는 영화를 찾는다.", conditions=Conditions()
    ),
    "04_tool_failure": Analysis(
        intent="plan_movie", reason="마라도에서 영화 볼 일정을 정하려고 한다.", conditions=Conditions(region="마라도")
    ),
    "05_smalltalk": Analysis(
        intent="smalltalk", reason="인사하며 플래너의 사용법을 묻는다.", conditions=Conditions()
    ),
    "06_clarify": Analysis(
        intent="plan_movie", reason="종료 시각을 기준으로 영화를 추천받으려고 한다.", conditions=Conditions()
    ),
}


class FixtureLLM:
    def __init__(self, messages: dict[str, str]):
        self._responses = {message: ANALYSES[scenario_id] for scenario_id, message in messages.items()}

    def with_structured_output(self, schema):
        if schema is not Analysis:
            raise ValueError("fixture LLM은 Analysis만 반환합니다.")
        return self

    def invoke(self, messages: list[tuple[str, str]]) -> Analysis:
        # 날짜와 시각은 빈 상태로 전달해 실제 analyzer 규칙이 추출하게 한다.
        _, message = messages[-1]
        return self._responses[message]


def run_fixture_tool(tool: str, **args) -> dict:
    if tool == "get_boxoffice":
        movies = {"daily": DAILY, "weekly": WEEKLY}[args["period"]]
        return ok({
            "movies": [
                {"movie_cd": code, "movie_nm": title, "rank": rank}
                for rank, (code, title, _) in enumerate(movies, 1)
            ],
            "source": "합성 박스오피스",
        })
    if tool == "get_now_playing":
        movie_id, title, _ = CURRENT[args["page"]]
        return ok({"movies": [{"tmdb_id": movie_id, "title": title}], "source": "합성 현재 상영작"})
    if tool == "get_movie_detail":
        if "movie_cd" in args:
            title, runtime = KOBIS_DETAILS[args["movie_cd"]]
            source = "합성 박스오피스 상세"
        else:
            title, runtime = TMDB_DETAILS[args["tmdb_id"]]
            source = "합성 현재 상영작 상세"
        return ok({"movie_nm": title, "runtime_min": runtime, "source": source})
    if tool == "search_theaters":
        if args["region"] == "마라도":
            return fail(NO_RESULT, "합성 영화관 검색 결과가 없습니다.")
        if args["region"] == "강남":
            return ok({
                "theaters": [{"name": "유리새 영화관 (합성)", "road_address": "강남 별유리길 7 (합성)"}],
                "source": "합성 영화관 검색",
            })
    return fail(BAD_ARGUMENT, "정의되지 않은 합성 Tool 또는 입력입니다.")


DAILY_TITLES = [title for _, title, _ in DAILY]
WEEKLY_TITLES = [title for _, title, _ in WEEKLY]
EXPECTATIONS = {
    "01_first_pool": {
        "status": "ok", "replan_count": 0, "recommended_titles": DAILY_TITLES,
        "date": SATURDAY, "start_after": "15:00", "end_by": "19:00",
    },
    "02_replan": {
        "status": "ok", "replan_count": 1, "recommended_titles": WEEKLY_TITLES,
        "date": SATURDAY, "start_after": "15:00", "end_by": "16:35",
        "time_exclusions": DAILY_TITLES,
        "pool_trace": ["get_boxoffice", "replan", "get_boxoffice"],
    },
    "03_limit": {
        "status": "limit", "replan_count": 3, "limit_reason": "time", "recommended_titles": [],
        "date": SATURDAY, "start_after": "15:00", "end_by": "16:00",
    },
    "04_tool_failure": {
        "status": "ok", "replan_count": 0, "recommended_titles": DAILY_TITLES,
        "date": SATURDAY, "start_after": "15:00", "end_by": "19:00",
        "theater_trace": ["tool_call", {"ok": False, "code": "NO_RESULT"}],
        "theater_failure_explained": True,
    },
    "05_smalltalk": {"status": "smalltalk", "replan_count": 0, "tool_call_count": 0},
    "06_clarify": {
        "status": "clarify", "replan_count": 0, "tool_call_count": 0,
        "end_by": "19:00", "asks_for_start": True,
    },
}


def compare(filename: str, result: dict, events: list[dict], metrics: dict, sqlite_matches: bool) -> dict:
    expected = EXPECTATIONS[filename].copy()
    expected.update(
        answer_present=True, events_match_sqlite=True, final_event_status=expected["status"],
        replan_event_count=expected["replan_count"],
    )
    pool_trace = []
    theater_trace = []
    for event in events:
        kind = event.get("type")
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if kind == "replan":
            pool_trace.append(kind)
        elif kind == "tool_call" and payload.get("tool") == "get_boxoffice":
            pool_trace.append("get_boxoffice")
        elif payload.get("tool") == "search_theaters":
            if kind == "tool_call":
                theater_trace.append(kind)
            elif kind == "tool_result":
                theater_trace.append({"ok": payload.get("ok"), "code": payload.get("code")})

    conditions = result.get("conditions") or {}
    answer = result.get("answer") or ""
    observed = {
        "status": result.get("status"),
        "replan_count": result.get("replan_count"),
        "limit_reason": result.get("limit_reason"),
        "recommended_titles": [movie.get("movie_nm") for movie in result.get("recommendations", [])],
        "time_exclusions": [
            movie.get("movie_nm") for movie in result.get("excluded", [])
            if "종료 제약 불충족" in (movie.get("reason") or "")
        ],
        "pool_trace": pool_trace,
        "theater_trace": theater_trace,
        "theater_failure_explained": "주변에서 영화관을 찾지 못했습니다" in answer,
        "asks_for_start": "몇 시 이후" in answer and "시작 시각" in answer,
        "date": conditions.get("date"),
        "start_after": conditions.get("start_after"),
        "end_by": conditions.get("end_by"),
        "tool_call_count": metrics["tool_call_count"],
        "answer_present": bool(answer.strip()),
        "events_match_sqlite": sqlite_matches,
        "final_event_status": metrics["status"],
        "replan_event_count": metrics["replan_count"],
    }
    actual = {field: observed[field] for field in expected}
    differences = {
        field: {"expected": value, "actual": actual[field]}
        for field, value in expected.items()
        if actual[field] != value
    }
    return {"expected": expected, "actual": actual, "differences": differences}

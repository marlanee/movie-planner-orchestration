import json
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from unicodedata import normalize
from uuid import uuid4

from app.agent.analyzer import MissingKeyError, analyze_with_time_overrides
from app.agent.detail_cache import DetailCache, default_detail_cache
from app.agent.judge import evaluate
from app.agent.state import AgentState, Conditions, LimitReason, Status
from app.config import AD_MINUTES, REPLAN_LIMIT
from app.store.anomaly_repo import latest_verdict


POOLS = (
    ("일별 박스오피스", "get_boxoffice", {"period": "daily"}),
    ("주간 박스오피스", "get_boxoffice", {"period": "weekly"}),
    ("TMDB 현재 상영작 1페이지", "get_now_playing", {"page": 1}),
    ("TMDB 현재 상영작 2페이지", "get_now_playing", {"page": 2}),
)
POOL_KEYS = {"get_boxoffice": "KOBIS_API_KEY", "get_now_playing": "TMDB_API_KEY"}
TOOL_CALL_FIELDS = {
    "get_boxoffice": ("KOBIS", ("target_date", "period")),
    "get_now_playing": ("TMDB", ("page",)),
    "search_theaters": ("KAKAO", ("region",)),
}
FORMULA = "필요 시간 = 러닝타임 + 광고 시간. 희망 시작 시각 + 필요 시간이 제약 종료 시각 이내면 충족합니다."
ERROR_ANSWER = "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."
THEATER_NOTE = "영화관 검색은 상영 영화를 알려 주지 않으므로 상영 여부는 영화관에서 확인해 주세요."
THEATER_KEY_NOTE = "카카오 API 키가 없거나 올바르지 않습니다. backend/.env를 확인하세요."
FAILURE_REASONS = {
    "NOT_FOUND": "영화 정보를 찾지 못했습니다",
    "NO_DATA": "조회 결과가 비어 있습니다",
    "BAD_ARGUMENT": "요청 값이 올바르지 않습니다",
    "NO_RESULT": "영화관 검색 결과가 없습니다",
    "NO_RUNTIME": "러닝타임 정보를 확인하지 못했습니다",
    "UPSTREAM_ERROR": "외부 API 응답 없음",
    "INVALID_KEY": "API 키가 없거나 올바르지 않습니다 — backend/.env 확인",
}
GENRE_EXCLUSION = "선호 장르 아님"
EXCLUSION_DISPLAY_LIMIT = 10
DETAIL_WORKERS = 4
TIME_ADJUSTMENT = "끝나는 시각을 늦추거나 시작 시각을 앞당길 수 있을까요?"
RECOMMENDATION_FIELDS = (
    "movie_nm", "movie_cd", "tmdb_id", "runtime_min", "required_min",
    "latest_start", "watch_grade", "genres",
)


def _build_plan(conditions: dict, pool_index: int, *, details: bool, theaters: bool) -> list[dict]:
    pool, tool, args = POOLS[pool_index]
    if tool == "get_boxoffice":
        args = {**args, "target_date": conditions["date"]}
    steps = [(tool, args, pool)]
    if details:
        steps.append(("get_movie_detail", {"from": "candidates"}, "후보 영화 러닝타임·장르 확인"))
    if theaters and conditions.get("region"):
        steps.append(("search_theaters", {"region": conditions["region"]}, "지역 영화관 위치 검색"))
    return [
        {"step": index, "tool": tool, "args": args, "purpose": purpose}
        for index, (tool, args, purpose) in enumerate(steps, 1)
    ]


def _title_key(title: str | None) -> str:
    normalized = normalize("NFKC", title or "").casefold().strip()
    return "".join(char for char in normalized if char.isalnum()) or normalized


def _movie_name(movie: dict) -> str:
    return movie.get("movie_nm") or movie.get("title") or "제목 미상"


def _movie_ref(movie: dict) -> tuple[str, dict] | None:
    if movie.get("movie_cd"):
        return f"kobis:{movie['movie_cd']}", {"movie_cd": movie["movie_cd"]}
    if movie.get("tmdb_id"):
        return f"tmdb:{movie['tmdb_id']}", {"tmdb_id": movie["tmdb_id"]}
    return None


def _rank_key(movie: dict) -> tuple[bool, int]:
    rank = movie.get("rank")
    return rank is None, rank or 0


def _summarize_tool(tool: str, data: dict) -> str:
    if tool in POOL_KEYS:
        movies = data.get("movies", [])
        names = ", ".join(map(_movie_name, movies[:3]))
        summary = f"후보 {len(movies)}편" + (f" ({names})" if names else "")
        if data.get("queried_date"):
            summary += f", 조회일 {data['queried_date']}"
        return summary
    if tool == "get_movie_detail":
        summary = f"{_movie_name(data)} 러닝타임 {data.get('runtime_min')}분"
        return f"{summary}, 출처 {data['source']}" if data.get("source") else summary
    return f"영화관 {len(data.get('theaters', []))}곳"


def _summarize_detail(response: dict) -> str:
    data = response["data"]
    if response.get("service_cache_hit"):
        summary = _summarize_tool("get_movie_detail", {**data, "source": data.get("source") or "TMDB"})
        return f"{summary} (캐시)"
    return _summarize_tool("get_movie_detail", data)


def _theater_lines(theaters: dict | None) -> list[str]:
    if not theaters or not theaters.get("theaters"):
        return []
    lines = ["영화관 위치"]
    for theater in theaters["theaters"][:5]:
        parts = (theater.get("name"), theater.get("road_address") or theater.get("address"), theater.get("url"))
        lines.append("- " + " · ".join(part for part in parts if part))
    return [*lines, THEATER_NOTE]


def _fact_answer(state: AgentState) -> str:
    topic = state["analysis"].fact_topic
    if topic == "theater":
        if state.get("theaters"):
            return "\n".join(_theater_lines(state["theaters"]))
        return _theater_failure_line(state) or "해당 지역의 영화관 정보를 확인하지 못했습니다."
    if topic == "runtime":
        details = [
            _summarize_detail(response)
            for response in state["details_cache"].values() if response["ok"]
        ]
        return "\n".join(details) or "박스오피스에서 해당 영화의 러닝타임을 확인하지 못했습니다."
    lines = [
        entry["summary"] for entry in state["tool_results"]
        if entry["tool"] == "get_boxoffice" and entry["ok"]
    ]
    if not lines:
        return "박스오피스 순위를 확인하지 못했습니다."
    for index, movie in enumerate(sorted(state["candidates"], key=_rank_key), 1):
        lines.append(f"- {movie.get('rank') or index}위 {_movie_name(movie)}")
    if state.get("estimated_note"):
        lines.append(state["estimated_note"])
    return "\n".join(lines)


def _limit_reason(title: str, title_matches: int, evaluations: list[dict], excluded: list[dict]) -> LimitReason:
    if title and title_matches == 0:
        return "title_not_found"
    if any(item["reason"] == GENRE_EXCLUSION for item in excluded):
        return "mixed" if any(item["fits"] is False for item in evaluations) else "genre"
    return "time"


def _topic_particle(title: str) -> str:
    last = title[-1:]
    if last and "가" <= last <= "힣":
        return "은" if (ord(last) - ord("가")) % 28 else "는"
    return "은(는)"


def _limit_lines(state: AgentState) -> list[str]:
    conditions = state["conditions"]
    reason = state["limit_reason"]
    count = state["replan_count"]
    if reason == "title_not_found":
        return [
            f"재계획 {count}회 후에도 '{conditions['movie_title']}'을(를) 일별·주간 박스오피스와 현재 상영작에서 찾지 못했습니다.",
            "영화 제목을 확인하거나 다른 영화를 말씀해 주세요.",
        ]

    lines = [f"재계획 {count}회(상한) 후에도 조건에 맞는 영화가 없습니다"]
    if reason in ("time", "mixed"):
        near_matches = [
            item for item in state["evaluations"]
            if item["fits"] is False and item["latest_start"] is not None
        ]
        if near_matches:
            closest = max(near_matches, key=lambda item: item["latest_start"])
            title = closest["movie_nm"]
            lines.append(f"가장 가까웠던 {title}{_topic_particle(title)} 늦어도 {closest['latest_start']}에 시작해야 합니다.")
        lines.append(TIME_ADJUSTMENT)
    if reason in ("genre", "mixed"):
        lines.append(f"선호 장르({conditions['genre']})를 바꾸거나 빼도 될까요?")
    return lines


def _exclusion_lines(excluded: list[dict]) -> list[str]:
    if not excluded:
        return []
    return ["제외 후보", *(
        f"- {item['movie_nm']} — {item['reason']}" for item in excluded[:EXCLUSION_DISPLAY_LIMIT]
    )]


def _theater_failure_line(state: AgentState) -> str | None:
    for failure in state.get("failures", []):
        if failure["tool"] != "search_theaters":
            continue
        code, args = failure["code"], failure["args"]
        if code == "INVALID_KEY":
            return THEATER_KEY_NOTE
        if code == "UPSTREAM_ERROR":
            return "영화관 정보를 잠시 가져오지 못했습니다."
        reason = _failure_reason(code, args)
        if code == "NO_RESULT":
            return f"영화관 정보: {reason}. 지역명을 다시 알려 주시면 찾아 드릴게요."
        return f"영화관 정보: {reason}."
    return None


def _failure_reason(code: str, args: dict) -> str:
    if code == "NO_RESULT" and args.get("region"):
        return f"'{args['region']}' 주변에서 영화관을 찾지 못했습니다"
    return FAILURE_REASONS.get(code, "조회하지 못했습니다")


def _failure_lines(state: AgentState) -> list[str]:
    # 상세 결과와 제외 후보는 모두 후보 순서로 누적된다. 답변에 표시된 제외만 중복을 피한다.
    shown_no_runtime = sum(
        item["reason"] == "러닝타임 확인 불가(NO_RUNTIME)"
        for item in state.get("excluded", [])[:EXCLUSION_DISPLAY_LIMIT]
    )
    lines = []
    for failure in state.get("failures", []):
        tool, code, args = failure["tool"], failure["code"], failure["args"]
        if tool == "get_movie_detail" and code == "NO_RUNTIME" and shown_no_runtime:
            shown_no_runtime -= 1
            continue
        lines.append(f"- {tool} — {_failure_reason(code, args)}")
    return ["조회 실패", *lines] if lines else []


def _plan_answer(state: AgentState, status: Status, recommendations: list[dict]) -> str:
    if status == "limit":
        lines = _limit_lines(state)
    else:
        end_by = state["conditions"]["end_by"]
        lines = ["추천 영화"]
        for movie in recommendations:
            line = f"- {movie['movie_nm']} — 러닝타임 {movie['runtime_min']}분(광고 포함 {movie['required_min']}분)"
            if movie["latest_start"] is not None:
                line += f", 늦어도 {movie['latest_start']}에 시작해야 {end_by}까지 끝낼 수 있습니다."
            lines.append(line)
        lines.append(f"재계획 {state.get('replan_count', 0)}회")

    lines.append(f"판단 기준 — 광고 {AD_MINUTES}분 적용. {FORMULA}")
    evidence = [
        f"- {entry['tool']} — {entry['summary']}"
        for entry in state["tool_results"] if entry["ok"] and not entry.get("cached")
    ]
    evidence.extend(f"- {entry['summary']}" for entry in state["tool_results"] if entry.get("cached"))
    if evidence:
        lines.extend(["조회 근거", *evidence])
    if state.get("estimated_note"):
        lines.append(state["estimated_note"])
    theater_failure = _theater_failure_line(state)
    lines.extend([theater_failure] if theater_failure else _theater_lines(state.get("theaters")))
    lines.extend(_exclusion_lines(state["excluded"]))
    return "\n".join(lines)


def _clarify(answer: str) -> tuple[AgentState, dict]:
    return {"plan": [], "status": "clarify", "answer": answer}, {"plan": [], "status": "clarify"}


def make_result(state: AgentState, status: Status, answer: str) -> dict:
    conditions = state.get("conditions", {})
    return {
        "status": status,
        "answer": answer,
        "recommendations": [
            {key: movie.get(key) for key in RECOMMENDATION_FIELDS}
            for movie in state.get("feasible", [])[:3]
        ],
        "excluded": state.get("excluded", []),
        "theaters": state.get("theaters"),
        "criteria": {
            "ad_minutes": AD_MINUTES, "start_after": conditions.get("start_after"),
            "end_by": conditions.get("end_by"), "date": conditions.get("date"), "formula": FORMULA,
        },
        "evidence": state.get("tool_results", []),
        "replan_count": state.get("replan_count", 0),
        "limit_reason": state.get("limit_reason"),
        "estimated_note": state.get("estimated_note"),
        "conditions": conditions,
    }


class RunState(AgentState, total=False):
    tool_call_ids: dict[tuple[str, str], str]


class AgentNodes:
    def __init__(
        self, llm, tool_runner, event_bus, memory, *,
        detail_cache: DetailCache = default_detail_cache,
        read_previous_verdict=latest_verdict,
    ):
        self.llm = llm
        self.tool_runner = tool_runner
        self.event_bus = event_bus
        self.memory = memory
        self.detail_cache = detail_cache
        self.read_previous_verdict = read_previous_verdict

    def staged(self, stage: str, node):
        def run(state: AgentState) -> AgentState:
            session_id = state["session_id"]
            self.event_bus.publish(session_id, "stage_start", stage)
            payload = {"status": "error"}
            start = perf_counter()
            try:
                updates, payload = node(state)
                return updates
            finally:
                elapsed_ms = int((perf_counter() - start) * 1000)
                self.event_bus.publish(session_id, "stage_end", stage, payload, duration_ms=elapsed_ms)

        return run

    def analyze_node(self, state: AgentState) -> tuple[AgentState, dict]:
        session_id = state["session_id"]
        previous_data = self.memory.get_conditions(session_id)
        previous = Conditions.model_validate(previous_data) if previous_data is not None else None
        try:
            analysis, rule_overrides = analyze_with_time_overrides(state["message"], state["today"], previous, self.llm)
        except MissingKeyError:
            self.event_bus.publish(session_id, "error", "analyze", {"kind": "MissingKeyError"})
            answer = "LLM 키(GMS_KEY)가 설정되지 않았습니다. backend/.env를 확인하세요."
            return {"status": "config_error", "answer": answer}, {"status": "config_error"}
        except Exception as error:
            self.event_bus.publish(session_id, "error", "analyze", {"kind": type(error).__name__})
            answer = "요청을 분석하지 못했습니다. 잠시 후 다시 시도해 주세요."
            return {"status": "error", "answer": answer}, {"status": "error"}

        extracted = analysis.conditions.model_dump()
        merged = {
            key: value if value is not None else (previous_data or {}).get(key)
            for key, value in extracted.items()
        }
        defaulted = merged["date"] is None
        if defaulted:
            merged["date"] = state["today"].isoformat()
        payload = {
            "intent": analysis.intent, "reason": analysis.reason,
            "extracted": extracted, "merged": merged.copy(), "date_defaulted": defaulted,
        }
        if rule_overrides:
            payload["rule_overrides"] = rule_overrides
        try:
            previous_verdict = self.read_previous_verdict(session_id)
        except Exception:
            previous_verdict = None
        if previous_verdict is not None:
            payload["previous_run_verdict"] = previous_verdict
        self.event_bus.publish(session_id, "llm_decision", "analyze", payload)
        return {"analysis": analysis, "conditions": merged, "date_defaulted": defaulted}, {
            "intent": analysis.intent, "date_defaulted": defaulted,
        }

    def plan_node(self, state: AgentState) -> tuple[AgentState, dict]:
        analysis, conditions = state["analysis"], state["conditions"]
        if analysis.intent == "smalltalk":
            return {"plan": [], "status": "smalltalk"}, {"plan": [], "skipped": True}
        if analysis.intent == "plan_movie":
            start_after, end_by = conditions["start_after"], conditions["end_by"]
            if end_by and not start_after:
                return _clarify("몇 시 이후에 보실 수 있는지 알려 주세요. 시작 시각을 알아야 끝나는 시각을 계산할 수 있습니다.")
            if start_after and end_by and start_after >= end_by:
                return _clarify(
                    f"시작 가능 시각({start_after})이 끝나야 하는 시각({end_by})보다 늦습니다. 시간을 다시 알려 주세요."
                )
            plan = _build_plan(conditions, 0, details=True, theaters=True)
        elif analysis.fact_topic == "theater":
            if not conditions["region"]:
                return _clarify("어느 지역의 영화관을 찾을까요?")
            plan = [{
                "step": 1, "tool": "search_theaters", "args": {"region": conditions["region"]},
                "purpose": "지역 영화관 위치 검색",
            }]
        else:
            if analysis.fact_topic == "runtime" and not _title_key(conditions["movie_title"]):
                return _clarify("어떤 영화의 러닝타임을 알려 드릴까요?")
            plan = _build_plan(conditions, 0, details=analysis.fact_topic == "runtime", theaters=False)
        return {"plan": plan, "pool_index": 0, "replan_count": 0}, {"plan": plan}

    def _call_tool(self, state: AgentState, tool: str, args: dict) -> dict:
        call = self._publish_tool_call(state, tool, args)
        response, duration_ms = self._run_tool(tool, args)
        self._record_tool_result(state, call, args, response, duration_ms)
        return response

    def _publish_tool_call(self, state: RunState, tool: str, args: dict) -> dict:
        if tool == "get_movie_detail":
            id_field = "movie_cd" if "movie_cd" in args else "tmdb_id"
            provider = "KOBIS" if id_field == "movie_cd" else "TMDB"
            # 상세 조회 식별자는 후보 조회 결과에서 왔으므로 값은 남기지 않는다.
            logged_args = {"arg_names": [id_field]}
        else:
            provider, fields = TOOL_CALL_FIELDS[tool]
            logged_args = {"args": {key: args[key] for key in fields if key in args}}
        call = {"call_id": uuid4().hex, "tool": tool, "provider": provider}
        # 비교 키는 이 실행의 그래프 상태에만 보관한다.
        key = (tool, json.dumps(args, sort_keys=True, separators=(",", ":")))
        first_call_id = state.setdefault("tool_call_ids", {}).setdefault(key, call["call_id"])
        if first_call_id != call["call_id"]:
            logged_args["duplicate_of"] = first_call_id
        self.event_bus.publish(state["session_id"], "tool_call", "execute", {**call, **logged_args})
        return call

    def _run_tool(self, tool: str, args: dict) -> tuple[dict, int]:
        start = perf_counter()
        try:
            response = self.tool_runner(tool, **args)
        except Exception:
            response = {"ok": False, "error": {"code": "UPSTREAM_ERROR", "message": "Tool 실행 중 오류가 발생했습니다."}}
        return response, int((perf_counter() - start) * 1000)

    def _record_tool_result(self, state: AgentState, call: dict, args: dict, response: dict, duration_ms: int) -> None:
        tool = call["tool"]
        if response["ok"]:
            code = None
            summary = _summarize_tool(tool, response["data"])
        else:
            code = response["error"]["code"]
            summary = f"{_failure_reason(code, args)} ({code})"
            state["failures"].append({"tool": tool, "args": args, **response["error"]})
        state["tool_results"].append({"tool": tool, "ok": response["ok"], "code": code, "summary": summary, "args": args})
        payload = {
            **call, "ok": response["ok"], "duration_ms": duration_ms,
            "code": code if code is None or code in FAILURE_REASONS else "UPSTREAM_ERROR",
        }
        self.event_bus.publish(state["session_id"], "tool_result", "execute", payload, duration_ms=duration_ms)

    def _call_details(self, state: AgentState, title: str) -> tuple[int, int]:
        tool = "get_movie_detail"
        cache = state["details_cache"]
        requests = {}
        cache_hits = 0
        for movie in state["candidates"]:
            if title and title not in _title_key(_movie_name(movie)):
                continue
            ref = _movie_ref(movie)
            if ref is None:
                continue
            key, args = ref
            if key in cache or key in requests:
                cache_hits += 1
            else:
                requests[key] = args

        responses = {}
        for key, args in requests.items():
            response = self.detail_cache.get(key)
            if response is not None:
                responses[key] = {**response, "service_cache_hit": True}
                state["tool_results"].append({
                    "tool": tool, "provider": "TMDB", "args": args, "ok": True,
                    "code": None, "cached": True, "summary": _summarize_detail(responses[key]),
                })
        service_cache_hits = len(responses)

        if service_cache_hits < len(requests):
            with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
                pending = []
                for key, args in requests.items():
                    if key in responses:
                        continue
                    call = self._publish_tool_call(state, tool, args)
                    pending.append((key, call, args, executor.submit(self._run_tool, tool, args)))
                for key, call, args, future in pending:
                    response, duration_ms = future.result()
                    self._record_tool_result(state, call, args, response, duration_ms)
                    self.detail_cache.put(key, response)
                    responses[key] = response

        cache.update((key, responses[key]) for key in requests)
        return cache_hits + service_cache_hits, service_cache_hits

    def execute_node(self, state: RunState) -> tuple[RunState, dict]:
        updates: RunState = {
            "session_id": state["session_id"],
            "tool_call_ids": dict(state.get("tool_call_ids", {})),
            "candidates": [],
            "seen_ids": set(state.get("seen_ids", ())),
            "seen_titles": set(state.get("seen_titles", ())),
            "details_cache": dict(state.get("details_cache", {})),
            "tool_results": list(state.get("tool_results", [])),
            "failures": list(state.get("failures", [])),
            "theaters": state.get("theaters"),
            "estimated_note": state.get("estimated_note"),
        }
        title = _title_key(state["conditions"]["movie_title"])
        cache_hits = 0
        service_cache_hits = 0
        for step in state["plan"]:
            tool, args = step["tool"], step["args"]
            if tool == "get_movie_detail":
                hits, service_hits = self._call_details(updates, title)
                cache_hits += hits
                service_cache_hits += service_hits
                continue
            response = self._call_tool(updates, tool, args)
            if tool == "search_theaters":
                updates["theaters"] = response["data"] if response["ok"] else None
            elif response["ok"]:
                data = response["data"]
                movies = data.get("movies", [])
                if state["analysis"].intent == "plan_movie":
                    self._add_candidates(updates, movies)
                else:
                    updates["candidates"] = movies
                if data.get("estimated") and data.get("note"):
                    updates["estimated_note"] = data["note"]
            elif response["error"]["code"] == "INVALID_KEY":
                updates["status"] = "config_error"
                updates["answer"] = f"후보 조회에 필요한 {POOL_KEYS[tool]} 설정을 확인해 주세요."
                break
        return updates, {
            "candidate_count": len(updates["candidates"]), "cache_hits": cache_hits,
            "service_cache_hits": service_cache_hits,
            "failure_count": len(updates["failures"]),
        }

    @staticmethod
    def _add_candidates(state: AgentState, movies: list[dict]) -> None:
        for movie in movies:
            ref = _movie_ref(movie)
            key = ref[0] if ref else None
            title = _title_key(movie.get("movie_nm") or movie.get("title"))
            seen = key in state["seen_ids"] or title in state["seen_titles"]
            if key:
                state["seen_ids"].add(key)
            if title:
                state["seen_titles"].add(title)
            if not seen:
                state["candidates"].append(movie)

    def judge_node(self, state: AgentState) -> tuple[AgentState, dict]:
        conditions = state["conditions"]
        title = _title_key(conditions["movie_title"])
        genre = (conditions["genre"] or "").casefold()
        excluded = list(state.get("excluded", []))
        evaluations = list(state.get("evaluations", []))
        title_matches = state.get("title_matches", 0)
        feasible = []
        for candidate in state["candidates"]:
            name = _movie_name(candidate)
            if title:
                if title not in _title_key(name):
                    continue
                title_matches += 1
            ref = _movie_ref(candidate)
            response = state["details_cache"].get(ref[0]) if ref else None
            if response is None or not response["ok"]:
                code = response["error"]["code"] if response is not None else "BAD_ARGUMENT"
                excluded.append({"movie_nm": name, "reason": f"러닝타임 확인 불가({code})"})
                continue
            details = response["data"]
            if genre and not any(genre in item.casefold() for item in details.get("genres") or []):
                excluded.append({"movie_nm": name, "reason": GENRE_EXCLUSION})
                continue
            evaluation = evaluate({**details, "movie_nm": details.get("movie_nm") or name}, conditions, AD_MINUTES)
            evaluations.append(evaluation)
            if evaluation["fits"]:
                feasible.append({
                    **details, **evaluation, "rank": candidate.get("rank"),
                    "movie_cd": details.get("movie_cd") or candidate.get("movie_cd"),
                    "tmdb_id": details.get("tmdb_id") or candidate.get("tmdb_id"),
                })
            else:
                excluded.append({"movie_nm": evaluation["movie_nm"], "reason": evaluation["reason"]})
        feasible.sort(key=_rank_key)
        updates: AgentState = {
            "evaluations": evaluations, "feasible": feasible, "excluded": excluded, "title_matches": title_matches,
        }
        if feasible:
            updates.update(status="ok", next="respond")
        elif state["replan_count"] < REPLAN_LIMIT and state["pool_index"] + 1 < len(POOLS):
            updates["next"] = "replan"
        else:
            updates.update(status="limit", next="respond", limit_reason=_limit_reason(title, title_matches, evaluations, excluded))
        return updates, {
            "feasible_count": len(feasible), "excluded_count": len(excluded),
            "title_matches": title_matches, "next": updates["next"],
        }

    def replan_node(self, state: AgentState) -> tuple[AgentState, dict]:
        count, pool_index = state["replan_count"] + 1, state["pool_index"] + 1
        plan = _build_plan(state["conditions"], pool_index, details=True, theaters=False)
        payload = {
            "replan_count": count, "reason": "충족 후보 0개",
            "next_pool": POOLS[pool_index][0], "reused_details": len(state["details_cache"]),
        }
        self.event_bus.publish(state["session_id"], "replan", "replan", payload)
        return {"replan_count": count, "pool_index": pool_index, "plan": plan}, {**payload, "plan": plan}

    def respond_node(self, state: AgentState) -> tuple[AgentState, dict]:
        status = state.get("status", "fact")
        result = make_result(state, status, state.get("answer", ""))
        if status == "smalltalk":
            result["answer"] = "관람 날짜, 지역, 가능한 시간대를 알려 주세요. 예를 들어 ‘토요일 3시 이후 강남에서 영화 보고 저녁 7시까지 끝내고 싶어요’라고 물어보세요."
        elif status == "fact":
            result["answer"] = _fact_answer(state)
        elif status in ("ok", "limit"):
            result["answer"] = _plan_answer(state, status, result["recommendations"])
        elif status == "config_error":
            lines = [result["answer"], *_exclusion_lines(result["excluded"])]
            if state.get("analysis") and state["analysis"].intent == "plan_movie":
                theater_failure = _theater_failure_line(state)
                if theater_failure:
                    lines.append(theater_failure)
            result["answer"] = "\n".join(lines)
        failure_lines = _failure_lines(state)
        if failure_lines:
            result["answer"] = "\n".join([result["answer"], *failure_lines])
        response_status = {"status": status, "recommendation_count": len(result["recommendations"])}
        if "conditions" in state:
            memory_conditions = state["conditions"].copy()
            if state.get("date_defaulted"):
                memory_conditions["date"] = None
            self.memory.update(state["session_id"], memory_conditions, {
                **response_status,
                "excluded_count": len(result["excluded"]), "replan_count": result["replan_count"],
            })
        return {"status": status, "answer": result["answer"], "result": result}, response_status

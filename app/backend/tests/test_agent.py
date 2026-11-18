import json
import unittest
from datetime import date
from threading import Event, get_ident
from time import sleep
from unittest.mock import patch

from app.agent.analyzer import analyze, analyze_with_time_overrides, extract_date_ko, extract_times_ko
from app.agent.detail_cache import DetailCache
from app.agent.graph import build_graph, run_agent
from app.agent.judge import evaluate
from app.agent.memory import SessionMemory
from app.agent.nodes import _failure_reason, _limit_lines
from app.agent.state import Analysis, Conditions
from app.events.bus import EventBus, EventCollector


TODAY = date(2026, 11, 18)
SESSION_ID = "movie-session"
DAILY = {"target_date": TODAY.isoformat(), "period": "daily"}
WEEKLY = {"target_date": TODAY.isoformat(), "period": "weekly"}
THEATER = {
    "name": "강남 영화관", "road_address": "서울 강남대로 1",
    "url": "https://example.test/theater", "phone": None,
}
KOBIS_TITLE = "고유제목KOBIS-XYZ"
KOBIS_CODE = "KOBIS-CODE-777"
KOBIS_RUNTIME = 137
KOBIS_LATEST_START = "16:33"


def ok(**data):
    return {"ok": True, "data": data}


def fail(code, message="가짜 Tool 오류"):
    return {"ok": False, "error": {"code": code, "message": message}}


def movie(code, title, rank):
    return {"movie_cd": code, "movie_nm": title, "rank": rank}


def detail(title, runtime, genres=None, *, source="가짜 상세 API"):
    return ok(movie_nm=title, runtime_min=runtime, genres=genres or ["드라마"], source=source)


def planning(**conditions):
    return Analysis(intent="plan_movie", reason="관람 시간 조건이 있습니다.", conditions=Conditions(**conditions))


def fact(topic, **conditions):
    return Analysis(
        intent="fact_query", reason="사실 정보가 필요합니다.", fact_topic=topic, conditions=Conditions(**conditions)
    )


class FakeLLM:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.messages = []
        self.schemas = []

    def with_structured_output(self, schema):
        self.schemas.append(schema)
        return self

    def invoke(self, messages):
        self.messages.append(messages)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class FakeTools:
    def __init__(self):
        self.responses = {}
        self.calls = []
        self.unexpected_calls = []

    def register(self, tool, response, **args):
        self.responses[(tool, tuple(sorted(args.items())))] = response

    def __call__(self, tool, **args):
        self.calls.append((tool, args))
        key = (tool, tuple(sorted(args.items())))
        if key not in self.responses:
            self.unexpected_calls.append((tool, args))
            return fail("UNEXPECTED_CALL")
        return self.responses[key]


def register_kobis_storage_fixture(tools):
    tools.register("get_boxoffice", ok(movies=[
        movie(KOBIS_CODE, KOBIS_TITLE, 47), movie("failed-detail", "상세 실패 영화", 48),
    ]), **DAILY)
    tools.register("get_movie_detail", detail(KOBIS_TITLE, KOBIS_RUNTIME, source="KOBIS"), movie_cd=KOBIS_CODE)
    tools.register("get_movie_detail", fail("NO_RUNTIME", f"{KOBIS_TITLE} {KOBIS_CODE}"), movie_cd="failed-detail")
    tools.register("search_theaters", ok(theaters=[THEATER]), region="강남")


def assert_no_kobis_event_data(test, events):
    payloads = json.dumps([event["payload"] for event in events], ensure_ascii=False)
    for fragment in (KOBIS_TITLE, KOBIS_CODE, f"{KOBIS_RUNTIME}분", KOBIS_LATEST_START, "17:27"):
        test.assertNotIn(fragment, payloads)
    for field in ("summary", "movie_nm", "movie_cd", "rank", "runtime_min", "required_min", "latest_start"):
        test.assertNotIn(f'"{field}":', payloads)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.memory = SessionMemory()
        self.detail_cache = DetailCache()
        self.bus = EventBus()
        self.collector = EventCollector(SESSION_ID)
        self.collector.attach(self.bus)
        self.addCleanup(self.collector.detach)
        self.tools = FakeTools()
        self.read_previous_verdict = lambda _: None

        def check_tool_calls():
            self.assertEqual(self.tools.unexpected_calls, [])

        self.addCleanup(check_tool_calls)

    def run_request(self, llm, session_id=SESSION_ID, *, today=TODAY, message="영화 요청"):
        return run_agent(
            session_id, message, llm=llm, tool_runner=self.tools,
            event_bus=self.bus, memory=self.memory, today=today, detail_cache=self.detail_cache,
            read_previous_verdict=self.read_previous_verdict,
        )

    def events(self, kind, stage=None):
        return [
            event for event in self.collector.events
            if event["type"] == kind and (stage is None or event["stage"] == stage)
        ]

    def register_daily(self, longest_runtime=200):
        longest_title = f"{longest_runtime}분 영화"
        self.tools.register(
            "get_boxoffice",
            ok(
                movies=[movie("b", "130분 영화", 2), movie("a", "100분 영화", 1), movie("c", longest_title, 3)],
                queried_date="2026-11-17", estimated=True, note="어제 집계로 추정했습니다.",
            ),
            **DAILY,
        )
        self.tools.register("get_movie_detail", detail("100분 영화", 100, ["코미디 드라마"]), movie_cd="a")
        self.tools.register("get_movie_detail", detail("130분 영화", 130), movie_cd="b")
        self.tools.register("get_movie_detail", detail(longest_title, longest_runtime), movie_cd="c")
        self.tools.register("search_theaters", ok(theaters=[THEATER]), region="강남")

    def register_tmdb_candidates(self, *movies):
        self.tools.register("get_boxoffice", ok(movies=[]), **DAILY)
        self.tools.register("get_boxoffice", ok(movies=[]), **WEEKLY)
        self.tools.register("get_now_playing", ok(movies=list(movies)), page=1)

    def test_first_pool_recommends_ranked_movies_with_calculated_deadlines(self):
        self.register_daily(longest_runtime=240)
        llm = FakeLLM(planning(start_after="15:00", end_by="19:00", region="강남"))

        result = self.run_request(llm)

        self.assertEqual(result["status"], "ok")
        self.assertEqual([(item["movie_nm"], item["latest_start"]) for item in result["recommendations"]],
                         [("100분 영화", "17:10"), ("130분 영화", "16:40")])
        self.assertEqual(result["recommendations"][0]["required_min"], 110)
        self.assertEqual([item["movie_nm"] for item in result["excluded"]], ["240분 영화"])
        self.assertIn("250분", result["excluded"][0]["reason"])
        self.assertIn("19:10", result["excluded"][0]["reason"])
        for fragment in ("강남 영화관", "서울 강남대로 1", THEATER["url"], "상영 여부는 영화관에서 확인",
                         "늦어도 17:10에 시작", "조회일 2026-11-17", "가짜 상세 API", "어제 집계로 추정"):
            self.assertIn(fragment, result["answer"])
        self.assertEqual(result["theaters"]["theaters"], [THEATER])
        self.assertEqual(result["replan_count"], 0)
        self.assertEqual(llm.schemas, [Analysis])

    def test_two_hundred_minute_movie_fits_the_four_hour_window(self):
        self.register_daily()
        result = self.run_request(FakeLLM(planning(start_after="15:00", end_by="19:00")))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["excluded"], [])
        self.assertEqual(result["recommendations"][2]["runtime_min"], 200)
        self.assertEqual(result["recommendations"][2]["latest_start"], "15:30")

    def test_tmdb_details_are_reused_across_sessions_with_evidence_and_without_tool_events(self):
        self.register_tmdb_candidates({"tmdb_id": 7, "title": "캐시 영화"})
        self.tools.register("get_movie_detail", detail("캐시 영화", 100, source="TMDB"), tmdb_id=7)
        first_collector = EventCollector("first-session")
        first_collector.attach(self.bus)
        self.addCleanup(first_collector.detach)
        first = self.run_request(FakeLLM(planning()), "first-session")
        self.assertEqual(first["status"], "ok")
        self.assertEqual(self.tools.calls.count(("get_movie_detail", {"tmdb_id": 7})), 1)
        self.assertNotIn("(캐시)", first["answer"])
        self.assertIsNone(self.memory.get_summary(SESSION_ID))
        calls = [event["payload"] for event in first_collector.events if event["type"] == "tool_call"]
        self.assertEqual(calls[-2]["args"], {"page": 1})
        self.assertEqual(calls[-1], {
            "call_id": calls[-1]["call_id"], "tool": "get_movie_detail", "provider": "TMDB", "arg_names": ["tmdb_id"],
        })
        self.tools.calls.clear()

        second = self.run_request(FakeLLM(planning()))

        self.assertEqual(second["status"], "ok")
        self.assertEqual(second["recommendations"], first["recommendations"])
        self.assertEqual(self.tools.calls, [("get_boxoffice", DAILY), ("get_boxoffice", WEEKLY),
                                           ("get_now_playing", {"page": 1})])
        self.assertIn("조회 근거", second["answer"])
        self.assertIn("- 캐시 영화 러닝타임 100분, 출처 TMDB (캐시)", second["answer"].splitlines())
        expected_tools = [tool for tool, _ in self.tools.calls]
        self.assertEqual([entry["tool"] for entry in second["evidence"]], [*expected_tools, "get_movie_detail"])
        self.assertEqual(second["evidence"][-1], {
            "tool": "get_movie_detail", "provider": "TMDB", "args": {"tmdb_id": 7}, "ok": True,
            "code": None, "cached": True, "summary": "캐시 영화 러닝타임 100분, 출처 TMDB (캐시)",
        })
        for kind in ("tool_call", "tool_result"):
            self.assertEqual([event["payload"]["tool"] for event in self.events(kind)], expected_tools)
        payload = self.events("stage_end", "execute")[-1]["payload"]
        self.assertEqual((payload["cache_hits"], payload["service_cache_hits"]), (1, 1))

    def test_kobis_detail_is_fetched_again_for_the_next_request(self):
        self.tools.register("get_boxoffice", ok(movies=[movie("a", "영화", 1)]), **DAILY)
        self.tools.register("get_movie_detail", detail("영화", 100, source="KOBIS"), movie_cd="a")
        first = self.run_request(FakeLLM(planning()))
        self.tools.register("get_movie_detail", detail("영화", 105, source="KOBIS"), movie_cd="a")

        second = self.run_request(FakeLLM(planning()))

        self.assertEqual(first["recommendations"][0]["runtime_min"], 100)
        self.assertEqual(second["recommendations"][0]["runtime_min"], 105)
        self.assertEqual(self.tools.calls.count(("get_movie_detail", {"movie_cd": "a"})), 2)
        self.assertEqual(len(self.detail_cache), 0)
        self.assertEqual(self.events("stage_end", "execute")[-1]["payload"]["service_cache_hits"], 0)

    def test_failed_details_are_retried_on_the_next_request(self):
        self.register_tmdb_candidates(*(
            {"tmdb_id": index, "title": f"{index}번 영화"} for index in (1, 2, 3)
        ))
        self.tools.register("get_movie_detail", fail("NO_RUNTIME"), tmdb_id=1)
        self.tools.register("get_movie_detail", fail("UPSTREAM_ERROR"), tmdb_id=2)
        self.tools.register("get_movie_detail", detail("3번 영화", 100, source="TMDB"), tmdb_id=3)

        first = self.run_request(FakeLLM(planning()))

        self.assertEqual([item["movie_nm"] for item in first["recommendations"]], ["3번 영화"])
        self.assertEqual(len(self.detail_cache), 1)
        for index in (1, 2):
            self.tools.register("get_movie_detail", detail(f"{index}번 영화", 100, source="TMDB"), tmdb_id=index)
        self.tools.calls.clear()

        second = self.run_request(FakeLLM(planning()))

        self.assertEqual(second["status"], "ok")
        self.assertEqual(len(second["recommendations"]), 3)
        self.assertCountEqual(
            [args for tool, args in self.tools.calls if tool == "get_movie_detail"],
            [{"tmdb_id": 1}, {"tmdb_id": 2}],
        )
        payload = self.events("stage_end", "execute")[-1]["payload"]
        self.assertEqual((payload["cache_hits"], payload["service_cache_hits"]), (1, 1))

    def test_details_are_refetched_when_the_default_ttl_expires(self):
        now = 0.0
        self.detail_cache = DetailCache(clock=lambda: now)
        self.tools.register("get_boxoffice", ok(movies=[{"tmdb_id": 1, "title": "100분 영화"}]), **DAILY)
        self.tools.register("get_movie_detail", detail("100분 영화", 100, source="TMDB"), tmdb_id=1)
        analysis = fact("runtime", movie_title="100분")

        self.assertIn("러닝타임 100분", self.run_request(FakeLLM(analysis))["answer"])
        now = 21599.0
        self.assertIn("러닝타임 100분, 출처 TMDB (캐시)", self.run_request(FakeLLM(analysis))["answer"])
        self.tools.register("get_movie_detail", detail("100분 영화", 105, source="TMDB"), tmdb_id=1)
        now = 21600.0

        result = self.run_request(FakeLLM(analysis))

        self.assertIn("러닝타임 105분", result["answer"])
        self.assertEqual(self.tools.calls.count(("get_movie_detail", {"tmdb_id": 1})), 2)
        self.assertEqual(
            [event["payload"]["service_cache_hits"] for event in self.events("stage_end", "execute")],
            [0, 1, 0],
        )

    def test_explicit_time_overrides_llm_value_and_records_change(self):
        self.tools.register("get_boxoffice", ok(movies=[movie("a", "80분 영화", 1)]), **DAILY)
        self.tools.register("get_movie_detail", detail("80분 영화", 80), movie_cd="a")
        llm = FakeLLM(planning(start_after="15:00", end_by="19:25"))

        result = self.run_request(llm, message="같은 날 3시 이후에 시작해서 4시 35분 전에 끝나는 걸로")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["conditions"]["end_by"], "16:35")
        decision = self.events("llm_decision", "analyze")[0]["payload"]
        self.assertEqual(decision["rule_overrides"], {"end_by": {"llm": "19:25", "rule": "16:35"}})

    def test_explicit_date_supplies_lookup_date_when_llm_date_is_missing(self):
        saturday = "2026-11-21"
        saturday_daily = {**DAILY, "target_date": saturday}
        self.tools.register("get_boxoffice", ok(movies=[movie("a", "80분 영화", 1)]), **saturday_daily)
        self.tools.register("get_movie_detail", detail("80분 영화", 80), movie_cd="a")

        result = self.run_request(FakeLLM(planning()), message="토요일 3시 이후에 영화 보고 싶어")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["conditions"]["date"], saturday)
        self.assertIn(("get_boxoffice", saturday_daily), self.tools.calls)
        decision = self.events("llm_decision", "analyze")[0]["payload"]
        self.assertFalse(decision["date_defaulted"])
        self.assertEqual(decision["rule_overrides"]["date"], {"llm": None, "rule": saturday})

    def test_replan_uses_new_movies_and_does_not_repeat_theater_search(self):
        self.tools.register("get_boxoffice", ok(movies=[movie("a", "긴 영화", 1)]), **DAILY)
        self.tools.register("get_movie_detail", detail("긴 영화", 180), movie_cd="a")
        self.tools.register("search_theaters", ok(theaters=[THEATER]), region="강남")
        self.tools.register(
            "get_boxoffice",
            ok(movies=[movie("a", "긴 영화", 1), movie("alias", "긴·영화", 2), movie("b", "짧은 영화", 3)]),
            **WEEKLY,
        )
        self.tools.register("get_movie_detail", detail("짧은 영화", 65), movie_cd="b")

        result = self.run_request(FakeLLM(planning(start_after="15:00", end_by="17:00", region="강남")))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["replan_count"], 1)
        self.assertEqual(result["recommendations"][0]["movie_nm"], "짧은 영화")
        self.assertEqual(result["excluded"][0]["movie_nm"], "긴 영화")
        self.assertEqual(self.tools.calls.count(("get_movie_detail", {"movie_cd": "a"})), 1)
        self.assertEqual(self.tools.calls.count(("search_theaters", {"region": "강남"})), 1)
        self.assertEqual(len(self.events("replan")), 1)
        self.assertEqual(self.events("replan")[0]["payload"]["reused_details"], 1)
        self.assertEqual(result["theaters"]["theaters"], [THEATER])

    def test_replan_limit_traverses_all_pools_and_suggests_a_time_adjustment(self):
        self.tools.register("get_boxoffice", ok(movies=[movie("a", "일별 영화", 1)]), **DAILY)
        self.tools.register("get_movie_detail", detail("일별 영화", 150), movie_cd="a")
        self.tools.register("get_boxoffice", ok(movies=[movie("b", "주간 영화", 1)]), **WEEKLY)
        self.tools.register("get_movie_detail", detail("주간 영화", 100), movie_cd="b")
        for page in (1, 2):
            self.tools.register("get_now_playing", ok(movies=[{"tmdb_id": page, "title": f"TMDB 영화 {page}"}]), page=page)
            self.tools.register("get_movie_detail", detail(f"TMDB 영화 {page}", 120), tmdb_id=page)

        result = self.run_request(FakeLLM(planning(start_after="15:00", end_by="16:00")))

        self.assertEqual(result["status"], "limit")
        self.assertEqual(result["limit_reason"], "time")
        self.assertEqual(result["replan_count"], 3)
        self.assertEqual(len(result["excluded"]), 4)
        self.assertIn("재계획 3회(상한)", result["answer"])
        self.assertIn("가장 가까웠던 주간 영화는 늦어도 14:10에 시작해야 합니다.", result["answer"])
        self.assertIn("끝나는 시각을 늦추거나 시작 시각을 앞당길 수 있을까요?", result["answer"])
        self.assertEqual([args for tool, args in self.tools.calls if tool == "get_now_playing"],
                         [{"page": 1}, {"page": 2}])
        self.assertEqual(len(self.events("replan")), 3)

    def test_missing_title_suggests_checking_the_title_after_all_pools(self):
        self.register_daily()
        self.tools.register("get_boxoffice", ok(movies=[movie("w", "주간 영화", 1)]), **WEEKLY)
        for page in (1, 2):
            self.tools.register("get_now_playing", ok(movies=[{"tmdb_id": page, "title": f"현재 상영작 {page}"}]), page=page)

        result = self.run_request(FakeLLM(planning(movie_title="없는영화", start_after="15:00", end_by="19:00")))

        self.assertEqual(result["status"], "limit")
        self.assertEqual(result["limit_reason"], "title_not_found")
        self.assertEqual(result["replan_count"], 3)
        self.assertIn("'없는영화'을(를) 일별·주간 박스오피스와 현재 상영작에서 찾지 못했습니다.", result["answer"])
        self.assertIn("영화 제목을 확인하거나 다른 영화를 말씀해 주세요.", result["answer"])
        self.assertNotIn("끝나는 시각을 늦추거나 시작 시각을 앞당길 수 있을까요?", result["answer"])
        self.assertFalse(any(tool == "get_movie_detail" for tool, _ in self.tools.calls))

    def test_limit_suggests_adjustments_for_genre_and_time_exclusions(self):
        self.tools.register("get_movie_detail", detail("시리즈 첫 편", 70), movie_cd="a")
        self.tools.register("get_movie_detail", detail("시리즈 두 번째 편", 150, ["코미디"]), movie_cd="b")
        self.tools.register("get_boxoffice", ok(movies=[]), **WEEKLY)
        for page in (1, 2):
            self.tools.register("get_now_playing", ok(movies=[]), page=page)

        wrong_genre = movie("a", "시리즈 첫 편", 1)
        long_comedy = movie("b", "시리즈 두 번째 편", 2)
        for reason, movies in (("genre", [wrong_genre]), ("mixed", [wrong_genre, long_comedy])):
            with self.subTest(reason=reason):
                self.tools.register("get_boxoffice", ok(movies=movies), **DAILY)
                result = self.run_request(FakeLLM(planning(
                    movie_title="시리즈", genre="코미디", start_after="15:00", end_by="17:00",
                )), session_id=reason)

                self.assertEqual(result["status"], "limit")
                self.assertEqual(result["limit_reason"], reason)
                self.assertIn("선호 장르(코미디)를 바꾸거나 빼도 될까요?", result["answer"])
                self.assertEqual(
                    "끝나는 시각을 늦추거나 시작 시각을 앞당길 수 있을까요?" in result["answer"],
                    reason == "mixed",
                )

    def test_recoverable_tool_failures_keep_other_recommendations(self):
        self.register_daily()
        self.tools.register("get_movie_detail", fail("NO_RUNTIME"), movie_cd="b")
        secret_url = "https://example.test/search?key=test-credential"
        self.tools.register("search_theaters", fail("NO_RESULT", secret_url), region="강남")

        result = self.run_request(FakeLLM(planning(start_after="15:00", end_by="19:00", region="강남")))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["recommendations"]), 2)
        self.assertIn({"movie_nm": "130분 영화", "reason": "러닝타임 확인 불가(NO_RUNTIME)"}, result["excluded"])
        self.assertIsNone(result["theaters"])
        self.assertEqual([item["code"] for item in result["evidence"] if not item["ok"]],
                         ["NO_RUNTIME", "NO_RESULT"])
        self.assertIn(
            "영화관 정보: '강남' 주변에서 영화관을 찾지 못했습니다. 지역명을 다시 알려 주시면 찾아 드릴게요.",
            result["answer"].splitlines(),
        )
        self.assertIn("- search_theaters — '강남' 주변에서 영화관을 찾지 못했습니다", result["answer"].splitlines())
        self.assertEqual(result["answer"].count("러닝타임 확인 불가(NO_RUNTIME)"), 1)
        self.assertNotIn("- get_movie_detail — 러닝타임 정보를 확인하지 못했습니다", result["answer"])
        self.assertNotIn(secret_url, result["answer"])
        self.assertNotIn("test-credential", result["answer"])

    def test_failed_lookup_has_a_readable_reason_in_answer_and_event(self):
        cases = (
            ("NOT_FOUND", "영화 정보를 찾지 못했습니다"),
            ("NO_DATA", "조회 결과가 비어 있습니다"),
            ("BAD_ARGUMENT", "요청 값이 올바르지 않습니다"),
            ("UNRECOGNIZED", "조회하지 못했습니다"),
        )
        for code, reason in cases:
            with self.subTest(code=code):
                self.tools.register("get_boxoffice", fail(code), **DAILY)
                result = self.run_request(FakeLLM(fact("boxoffice")))

                self.assertIn(f"- get_boxoffice — {reason}", result["answer"].splitlines())
                payload = self.events("tool_result")[-1]["payload"]
                self.assertEqual(payload["code"], "UPSTREAM_ERROR" if code == "UNRECOGNIZED" else code)
                self.assertNotIn("summary", payload)

    def test_theater_bad_argument_does_not_suggest_retrying_with_another_region(self):
        self.tools.register("search_theaters", fail("BAD_ARGUMENT"), region="강남")

        result = self.run_request(FakeLLM(fact("theater", region="강남")))

        self.assertEqual(result["status"], "fact")
        self.assertIn("영화관 정보: 요청 값이 올바르지 않습니다.", result["answer"].splitlines())
        self.assertNotIn("지역명을 다시", result["answer"])
        self.assertNotIn("영화관을 찾지 못했습니다", result["answer"])

    def test_theater_upstream_failure_reports_safe_reason(self):
        self.register_daily()
        message = "exception text: https://example.test/search?key=test-credential"
        self.tools.register("search_theaters", fail("UPSTREAM_ERROR", message), region="강남")

        result = self.run_request(FakeLLM(planning(region="강남")))

        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["theaters"])
        self.assertIn("영화관 정보를 잠시 가져오지 못했습니다.", result["answer"].splitlines())
        self.assertIn("- search_theaters — 외부 API 응답 없음", result["answer"].splitlines())
        self.assertNotIn("지역명을 다시", result["answer"])
        for fragment in ("exception text", "https://example.test/search", "test-credential"):
            self.assertNotIn(fragment, str(result))
            self.assertNotIn(fragment, str(self.collector.events))

    def test_theater_key_failure_keeps_recommendations_and_explains_missing_theaters(self):
        self.register_daily()
        self.tools.register("search_theaters", fail("INVALID_KEY", "test-credential"), region="강남")

        result = self.run_request(FakeLLM(planning(region="강남")))

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["recommendations"])
        self.assertIsNone(result["theaters"])
        self.assertIn(
            "카카오 API 키가 없거나 올바르지 않습니다. backend/.env를 확인하세요.",
            result["answer"].splitlines(),
        )
        self.assertNotIn("지역명을 다시", result["answer"])
        self.assertNotIn("test-credential", str(result))
        self.assertNotIn("test-credential", str(self.collector.events))

    def test_smalltalk_skips_tool_plan(self):
        result = self.run_request(FakeLLM(Analysis(intent="smalltalk", reason="인사입니다.", conditions=Conditions())))

        self.assertEqual(result["status"], "smalltalk")
        self.assertEqual(self.tools.calls, [])
        self.assertTrue(self.events("stage_end", "plan")[0]["payload"]["skipped"])
        self.assertIn("토요일", result["answer"])

    def test_end_by_alone_asks_before_calling_tools(self):
        result = self.run_request(FakeLLM(planning(end_by="19:00")))

        self.assertEqual(result["status"], "clarify")
        self.assertIn("몇 시 이후", result["answer"])
        self.assertEqual(self.tools.calls, [])
        self.assertEqual(self.memory.get_conditions(SESSION_ID)["end_by"], "19:00")

    def test_equal_or_later_start_time_asks_before_calling_tools(self):
        for start_after in ("19:00", "20:00"):
            with self.subTest(start_after=start_after):
                result = self.run_request(FakeLLM(planning(start_after=start_after, end_by="19:00")))

                self.assertEqual(result["status"], "clarify")
                self.assertEqual(
                    result["answer"],
                    f"시작 가능 시각({start_after})이 끝나야 하는 시각(19:00)보다 늦습니다. 시간을 다시 알려 주세요.",
                )
                self.assertEqual(self.tools.calls, [])

    def test_invalid_pool_key_stops_immediately(self):
        self.tools.register("get_boxoffice", fail("INVALID_KEY", "test-credential"), **DAILY)

        result = self.run_request(FakeLLM(planning(region="강남")))

        self.assertEqual(result["status"], "config_error")
        self.assertEqual(result["replan_count"], 0)
        self.assertIn("KOBIS_API_KEY", result["answer"])
        self.assertNotIn("test-credential", str(result))
        self.assertNotIn("test-credential", str(self.collector.events))
        self.assertEqual(self.tools.calls, [("get_boxoffice", DAILY)])
        self.assertEqual(self.events("stage_start", "judge"), [])

    def test_tmdb_key_failure_during_replan_includes_prior_exclusions(self):
        self.tools.register("get_boxoffice", ok(movies=[movie("a", "일별 영화", 1)]), **DAILY)
        self.tools.register("get_movie_detail", detail("일별 영화", 150), movie_cd="a")
        self.tools.register("get_boxoffice", ok(movies=[movie("b", "주간 영화", 1)]), **WEEKLY)
        self.tools.register("get_movie_detail", fail("NO_RUNTIME"), movie_cd="b")
        self.tools.register("get_now_playing", fail("INVALID_KEY", "test-credential"), page=1)

        result = self.run_request(FakeLLM(planning(start_after="15:00", end_by="16:00")))

        self.assertEqual(result["status"], "config_error")
        self.assertEqual(result["replan_count"], 2)
        self.assertEqual(result["answer"].splitlines(), [
            "후보 조회에 필요한 TMDB_API_KEY 설정을 확인해 주세요.",
            "제외 후보",
            "- 일별 영화 — 필요 시간 160분. 15:00에 시작할 경우 17:40 종료, 16:00 종료 제약 불충족",
            "- 주간 영화 — 러닝타임 확인 불가(NO_RUNTIME)",
            "조회 실패",
            "- get_now_playing — API 키가 없거나 올바르지 않습니다 — backend/.env 확인",
        ])
        self.assertNotIn("test-credential", str(result))
        self.assertNotIn("test-credential", str(self.collector.events))
        self.assertEqual(self.tools.calls[-1], ("get_now_playing", {"page": 1}))

    def test_missing_gms_key_is_reported_without_tools(self):
        with patch("app.agent.analyzer.config.get_key", return_value=None):
            result = self.run_request(None)

        self.assertEqual(result["status"], "config_error")
        self.assertIn("GMS_KEY", result["answer"])
        self.assertEqual(self.tools.calls, [])
        self.assertEqual([event["payload"] for event in self.events("error", "analyze")],
                         [{"kind": "MissingKeyError"}])

    def test_memory_merges_changed_conditions_and_limits_summary_fields(self):
        self.register_daily()
        first = FakeLLM(planning(date=TODAY.isoformat(), start_after="15:00", end_by="19:00"))
        self.assertEqual(self.run_request(first)["status"], "ok")

        second = FakeLLM(planning(genre="코미디"))
        result = self.run_request(second)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["conditions"], Conditions(date=TODAY.isoformat(), start_after="15:00", end_by="19:00", genre="코미디").model_dump(),
        )
        self.assertIn('"end_by":"19:00"', second.messages[0][0][1])
        self.assertEqual([movie["movie_nm"] for movie in result["recommendations"]], ["100분 영화"])
        self.assertIn({"movie_nm": "200분 영화", "reason": "선호 장르 아님"}, result["excluded"])
        self.assertEqual(self.memory.get_summary(SESSION_ID), {
            "status": "ok", "replan_count": 0, "recommendation_count": 1, "excluded_count": 2,
        })
        self.assertIsNone(self.memory.get_conditions("other-session"))
        self.memory.clear(SESSION_ID)
        self.assertIsNone(self.memory.get_conditions(SESSION_ID))
        self.assertIsNone(self.memory.get_summary(SESSION_ID))

    def test_kobis_results_stay_in_live_result_and_previous_prompt_keeps_conditions(self):
        register_kobis_storage_fixture(self.tools)
        conditions = Conditions(date=TODAY.isoformat(), start_after="15:00", end_by="19:00", region="강남")
        llm = FakeLLM(planning(**conditions.model_dump()), planning())

        first = self.run_request(llm)
        self.assertEqual(first["status"], "ok")
        self.assertIn(KOBIS_TITLE, first["answer"])
        self.assertIn(f"러닝타임 {KOBIS_RUNTIME}분", first["answer"])
        movie_result = first["recommendations"][0]
        self.assertEqual((movie_result["movie_nm"], movie_result["movie_cd"], movie_result["runtime_min"],
                          movie_result["latest_start"]), (KOBIS_TITLE, KOBIS_CODE, KOBIS_RUNTIME, KOBIS_LATEST_START))
        self.assertEqual(self.memory.get_conditions(SESSION_ID), conditions.model_dump())
        self.assertEqual(self.memory.get_summary(SESSION_ID), {
            "status": "ok", "replan_count": 0, "recommendation_count": 1, "excluded_count": 1,
        })

        follow_up = self.run_request(llm, message="같은 날 다른 영화로")
        self.assertEqual(follow_up["status"], "ok")
        self.assertEqual(follow_up["conditions"], conditions.model_dump())
        previous_json = llm.messages[1][0][1].split("직전 조건 데이터: ", 1)[1]
        self.assertEqual(json.loads(previous_json), conditions.model_dump())
        self.assertEqual(llm.messages[1][1], ("human", "같은 날 다른 영화로"))
        self.assertEqual(self.tools.calls.count(("get_movie_detail", {"movie_cd": KOBIS_CODE})), 2)
        for fragment in (KOBIS_TITLE, KOBIS_CODE, f"{KOBIS_RUNTIME}분", KOBIS_LATEST_START):
            self.assertNotIn(fragment, str(llm.messages))
        assert_no_kobis_event_data(self, self.collector.events)
        self.assertEqual(self.events("stage_end", "respond")[-1]["payload"], {"status": "ok", "recommendation_count": 1})

    def test_events_include_stages_tool_pairs_and_llm_decision(self):
        self.register_daily()
        self.run_request(FakeLLM(planning(start_after="15:00", end_by="19:00", region="강남")))

        stages = ["analyze", "plan", "execute", "judge", "respond"]
        for kind in ("stage_start", "stage_end"):
            self.assertEqual([event["stage"] for event in self.events(kind)], stages)
        sequence = [event["seq"] for event in self.collector.events]
        self.assertEqual(sequence, list(range(1, len(sequence) + 1)))
        tool_events = [event for event in self.collector.events if event["type"] in ("tool_call", "tool_result")]
        self.assertEqual(len(tool_events), 2 * len(self.tools.calls))
        self.assertEqual([(event["type"], event["payload"]["tool"]) for event in tool_events], [
            ("tool_call", "get_boxoffice"), ("tool_result", "get_boxoffice"),
            *[("tool_call", "get_movie_detail")] * 3,
            *[("tool_result", "get_movie_detail")] * 3,
            ("tool_call", "search_theaters"), ("tool_result", "search_theaters"),
        ])
        calls = self.events("tool_call")
        outputs = self.events("tool_result")
        self.assertEqual([event["payload"]["provider"] for event in calls], ["KOBIS"] * 4 + ["KAKAO"])
        self.assertEqual(calls[0]["payload"]["args"], DAILY)
        self.assertEqual(calls[-1]["payload"]["args"], {"region": "강남"})
        self.assertEqual(len({event["payload"]["call_id"] for event in calls}), len(calls))
        for call, output in zip(calls, outputs, strict=True):
            self.assertGreaterEqual(output["duration_ms"], 0)
            self.assertEqual(output["payload"], {
                "call_id": call["payload"]["call_id"], "tool": call["payload"]["tool"],
                "provider": call["payload"]["provider"], "ok": True, "code": None,
                "duration_ms": output["duration_ms"],
            })
        decision = self.events("llm_decision")[0]["payload"]
        self.assertEqual(decision["intent"], "plan_movie")
        self.assertEqual(decision["reason"], "관람 시간 조건이 있습니다.")
        self.assertEqual(self.events("stage_end", "plan")[0]["payload"]["plan"][1]["args"], {"from": "candidates"})

    def test_details_run_in_parallel_with_serial_results_and_candidate_order_events(self):
        codes = [f"movie-{index}" for index in range(10)]
        self.tools.register(
            "get_boxoffice",
            ok(movies=[movie(code, code, index) for index, code in enumerate(codes, 1)]),
            **DAILY,
        )
        for index, code in enumerate(codes):
            self.tools.register("get_movie_detail", detail(code, 90 + 10 * index), movie_cd=code)
        initial = {"session_id": SESSION_ID, "message": "영화 요청", "today": TODAY}
        second_detail_finished = Event()
        completed_codes = []
        coordinator_threads = []
        event_threads = []

        def track_tool_event(event):
            if event["type"] in ("tool_call", "tool_result"):
                event_threads.append(get_ident())

        self.addCleanup(self.bus.subscribe(track_tool_event))

        def fetch(tool, **args):
            if args.get("movie_cd") == codes[4]:
                raise RuntimeError("exception text: https://example.test/detail?key=test-credential")
            return self.tools(tool, **args)

        def fetch_with_delay(tool, **args):
            if tool != "get_movie_detail":
                coordinator_threads.append(get_ident())
                return fetch(tool, **args)
            code = args["movie_cd"]
            sleep(0.2)
            if code == codes[0]:
                second_detail_finished.wait(timeout=0.6)
            try:
                return fetch(tool, **args)
            finally:
                completed_codes.append(code)
                if code == codes[1]:
                    second_detail_finished.set()

        def fetch_serially(nodes, state, _title):
            for candidate in state["candidates"]:
                code = candidate["movie_cd"]
                state["details_cache"][f"kobis:{code}"] = nodes._call_tool(
                    state, "get_movie_detail", {"movie_cd": code}
                )
            return 0, 0

        analysis = planning(start_after="15:00", end_by="17:00")
        parallel = build_graph(
            FakeLLM(analysis), fetch_with_delay, self.bus, self.memory, detail_cache=self.detail_cache,
            read_previous_verdict=self.read_previous_verdict,
        ).invoke(initial)
        with patch("app.agent.nodes.AgentNodes._call_details", fetch_serially):
            serial = build_graph(
                FakeLLM(analysis), fetch, EventBus(), SessionMemory(), detail_cache=DetailCache(),
                read_previous_verdict=self.read_previous_verdict,
            ).invoke(initial)

        self.assertLess(self.events("stage_end", "execute")[0]["duration_ms"], 1000)
        self.assertLess(completed_codes.index(codes[1]), completed_codes.index(codes[0]))
        self.assertEqual(set(event_threads), set(coordinator_threads))
        self.assertEqual(parallel["result"], serial["result"])
        self.assertEqual(parallel["details_cache"], serial["details_cache"])
        self.assertEqual(list(parallel["details_cache"]), [f"kobis:{code}" for code in codes])
        self.assertEqual(parallel["result"]["status"], "ok")
        self.assertIn("- get_movie_detail — 외부 API 응답 없음", parallel["result"]["answer"].splitlines())
        for fragment in ("exception text", "https://example.test/detail", "test-credential"):
            self.assertNotIn(fragment, str(parallel["result"]))
            self.assertNotIn(fragment, str(self.collector.events))

        calls = [event for event in self.events("tool_call") if event["payload"]["tool"] == "get_movie_detail"]
        outputs = [event for event in self.events("tool_result") if event["payload"]["tool"] == "get_movie_detail"]
        call_ids = [event["payload"]["call_id"] for event in calls]
        self.assertEqual(len(set(call_ids)), len(codes))
        self.assertEqual([event["payload"]["call_id"] for event in outputs], call_ids)
        for call in calls:
            self.assertEqual(call["payload"], {
                "call_id": call["payload"]["call_id"], "tool": "get_movie_detail",
                "provider": "KOBIS", "arg_names": ["movie_cd"],
            })
        self.assertEqual([event["payload"]["code"] for event in outputs],
                         ["UPSTREAM_ERROR" if code == codes[4] else None for code in codes])
        self.assertLess(calls[-1]["seq"], outputs[0]["seq"])
        for output in outputs:
            self.assertGreaterEqual(output["duration_ms"], 190)

    def test_date_default_and_no_deadline(self):
        self.register_daily()
        result = self.run_request(FakeLLM(planning()))

        self.assertEqual(result["conditions"]["date"], TODAY.isoformat())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["recommendations"]), 3)
        self.assertTrue(all(movie["latest_start"] is None for movie in result["recommendations"]))
        decision = self.events("llm_decision")[0]["payload"]
        self.assertTrue(decision["date_defaulted"])
        self.assertIsNone(decision["extracted"]["date"])
        self.assertEqual(decision["merged"]["date"], TODAY.isoformat())
        self.assertIsNone(self.memory.get_conditions(SESSION_ID)["date"])

        tomorrow = date(2026, 11, 19)
        tomorrow_daily = {**DAILY, "target_date": tomorrow.isoformat()}
        self.tools.register("get_boxoffice", ok(movies=[movie("a", "100분 영화", 1)]), **tomorrow_daily)
        next_result = self.run_request(FakeLLM(planning()), today=tomorrow)

        self.assertEqual(next_result["status"], "ok")
        self.assertEqual(next_result["conditions"]["date"], tomorrow.isoformat())
        self.assertIn(("get_boxoffice", tomorrow_daily), self.tools.calls)
        self.assertTrue(self.events("llm_decision")[-1]["payload"]["date_defaulted"])
        self.assertIsNone(self.memory.get_conditions(SESSION_ID)["date"])

    def test_fact_topics_route_to_the_requested_tools(self):
        self.register_daily()
        rank = self.run_request(FakeLLM(fact("boxoffice")), "rank")
        runtime = self.run_request(FakeLLM(fact("runtime", movie_title="100분")), "runtime")
        theater = self.run_request(FakeLLM(fact("theater", region="강남")), "theater")
        for result, fragment in ((rank, "1위 100분 영화"), (runtime, "러닝타임 100분"), (theater, "강남 영화관")):
            self.assertEqual(result["status"], "fact")
            self.assertIn(fragment, result["answer"])
        self.assertEqual(self.tools.calls, [
            ("get_boxoffice", DAILY), ("get_boxoffice", DAILY),
            ("get_movie_detail", {"movie_cd": "a"}), ("search_theaters", {"region": "강남"}),
        ])
        result = self.run_request(FakeLLM(fact("theater")), "no-region")
        self.assertEqual(result["status"], "clarify")
        self.assertIn("어느 지역", result["answer"])
        self.assertEqual(len(self.tools.calls), 4)

    def test_runtime_without_title_asks_before_calling_tools(self):
        result = self.run_request(FakeLLM(fact("runtime")))

        self.assertEqual(result["status"], "clarify")
        self.assertEqual(result["answer"], "어떤 영화의 러닝타임을 알려 드릴까요?")
        self.assertEqual(self.tools.calls, [])

    def test_runtime_answer_keeps_candidate_order_with_cached_and_fresh_details(self):
        self.register_daily()
        self.tools.register("get_movie_detail", detail("100분 영화", 100, source="TMDB"), tmdb_id=1)
        self.tools.register("get_boxoffice", ok(movies=[
            movie("b", "130분 영화", 2),
            {"tmdb_id": 1, "title": "100분 영화", "rank": 1},
            movie("c", "200분 영화", 3),
        ]), **DAILY)
        self.run_request(FakeLLM(fact("runtime", movie_title="100분")), "first-session")
        self.tools.calls.clear()

        result = self.run_request(FakeLLM(fact("runtime", movie_title="분 영화")))

        self.assertEqual(result["status"], "fact")
        self.assertEqual(result["answer"].splitlines(), [
            "130분 영화 러닝타임 130분, 출처 가짜 상세 API",
            "100분 영화 러닝타임 100분, 출처 TMDB (캐시)",
            "200분 영화 러닝타임 200분, 출처 가짜 상세 API",
        ])
        self.assertCountEqual([args for tool, args in self.tools.calls if tool == "get_movie_detail"],
                              [{"movie_cd": "b"}, {"movie_cd": "c"}])

    def test_llm_failure_and_unexpected_graph_error_return_safe_results(self):
        result = self.run_request(FakeLLM(RuntimeError("private exception text")))
        self.assertEqual(result["status"], "error")
        self.assertNotIn("private exception text", result["answer"])
        self.assertEqual(self.events("error", "analyze")[0]["payload"], {"kind": "RuntimeError"})

        with patch("app.agent.graph.build_graph", side_effect=RuntimeError("private exception text")):
            result = self.run_request(FakeLLM(planning()))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["recommendations"], [])
        self.assertNotIn("private exception text", result["answer"])
        self.assertEqual(self.events("error", "agent")[0]["payload"], {"kind": "RuntimeError"})

    def test_graph_has_all_six_stages_and_the_replan_loop(self):
        graph = build_graph(llm=object(), detail_cache=self.detail_cache).get_graph()

        self.assertEqual(set(graph.nodes) - {"__start__", "__end__"},
                         {"analyze", "plan", "execute", "judge", "replan", "respond"})
        edges = {(edge.source, edge.target): edge.conditional for edge in graph.edges}
        self.assertTrue(edges[("judge", "replan")])
        self.assertFalse(edges[("replan", "execute")])


class DetailCacheTests(unittest.TestCase):
    def test_cache_copies_nested_results_and_can_be_cleared(self):
        cache = DetailCache()
        result = detail("영화", 100)
        cache.put("tmdb:1", result)
        cache.put("tmdb:1", fail("NO_RUNTIME"))
        cache.put("kobis:1", detail("다른 영화", 110))

        result["data"]["genres"].append("코미디")
        cache.get("tmdb:1")["data"]["genres"].clear()

        self.assertEqual(cache.get("tmdb:1"), detail("영화", 100))
        self.assertIsNone(cache.get("kobis:1"))
        self.assertEqual(len(cache), 1)
        cache.clear()
        self.assertIsNone(cache.get("tmdb:1"))
        self.assertEqual(len(cache), 0)


class AnalysisAndJudgeTests(unittest.TestCase):
    def test_extract_date_ko(self):
        cases = [
            ("토요일 3시 이후", "2026-11-21"),
            ("이번 주 토요일", "2026-11-21"),
            ("이번주 토요일", "2026-11-21"),
            ("이번 주 월요일", None),
            ("월요일", "2026-11-23"),
            ("수요일", "2026-11-18"),
            ("다음 주 토요일", "2026-11-28"),
            ("다음주 월요일", "2026-11-23"),
            ("오늘", "2026-11-18"),
            ("내일", "2026-11-19"),
            ("모레", "2026-11-20"),
            ("12월 3일", "2026-12-03"),
            ("1월 5일", "2027-01-05"),
            ("2월 30일", None),
            ("12월 3일이나 내일", None),
            ("이번 주 월요일이나 토요일", None),
            ("그럼 내일 말고 토요일은?", None),
            ("오늘 말고 내일", None),
            ("내일모레", None),
            ("다다음 주 월요일", None),
            ("지난주 토요일", None),
            ("지난 토요일", None),
            ("저번 주 토요일", None),
            ("내일 출근이라 오늘 9시까지", None),
            ("토, 토욜", None),
            ("안녕", None),
        ]
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(extract_date_ko(message, TODAY), expected)

    def test_analysis_only_replaces_llm_date_when_rule_resolves(self):
        llm_date = "2026-11-23"
        for message, expected in (("토요일 영화", "2026-11-21"), ("이번 주 월요일 영화", llm_date)):
            with self.subTest(message=message):
                result = analyze(message, TODAY, None, FakeLLM(planning(date=llm_date)))
                self.assertEqual(result.conditions.date, expected)

    def test_equal_date_and_ambiguous_times_do_not_override_llm(self):
        llm = FakeLLM(planning(date="2026-11-21", end_by="23:59"))
        result, overrides = analyze_with_time_overrides("토요일 7시 이후 시작해서 밤 12시 전에", TODAY, None, llm)

        self.assertEqual(result.conditions, Conditions(date="2026-11-21", start_after="19:00", end_by="23:59"))
        self.assertEqual(overrides, {"start_after": {"llm": None, "rule": "19:00"}})

    def test_extract_times_ko(self):
        cases = [
            ("같은 날 3시 이후에 시작해서 4시 35분 전에 끝나는 걸로", {"start_after": "15:00", "end_by": "16:35"}),
            ("이번 주 토요일 오후 3시 이후 강남에서 영화 보고 저녁 7시 전에 끝내고 싶어",
             {"start_after": "15:00", "end_by": "19:00"}),
            ("7시 반까지 끝나야 해", {"end_by": "19:30"}),
            ("오전 10시부터", {"start_after": "10:00"}),
            ("12시 이후", {"start_after": "12:00"}),
            ("토요일 오후 3시 이후 시작해서 4시 전에 끝나는 영화", {"start_after": "15:00", "end_by": "16:00"}),
            ("새벽 12시를 지나서 아침 7시 이전에", {"start_after": "00:00", "end_by": "07:00"}),
            ("밤 12시까지", {}),
            ("저녁 12시까지", {}),
            ("밤 1시까지", {}),
            ("밤 2시 전에", {}),
            ("새벽 5시까지", {}),
            ("7시 이후 시작해서 밤 12시 전에", {"start_after": "19:00"}),
            ("4시 10분 전", {}),
            ("4시 10분 전에", {}),
            ("4시 20분 전까지", {}),
            ("4시 10분까지", {"end_by": "16:10"}),
            ("4시 35분 전에", {"end_by": "16:35"}),
            ("3시부터, 4시 넘어서, 7시 전까지, 8시 안에", {"start_after": "15:00", "end_by": "19:00"}),
            ("3시      이후에", {"start_after": "15:00"}),
            ("3시       후에", {}),
            ("4시 60분 전에", {}),
            ("35분 전에 알려줘", {}),
            ("세 시 이후", {}),
            ("안녕", {}),
        ]
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(extract_times_ko(message), expected)

    def test_theater_no_result_without_region_has_a_general_reason(self):
        self.assertEqual(_failure_reason("NO_RESULT", {}), "영화관 검색 결과가 없습니다")

    def test_limit_answer_uses_topic_particle_for_the_movie_title(self):
        for title, expected in (("긴 영화", "긴 영화는"), ("첫 편", "첫 편은"), ("영화 1", "영화 1은(는)")):
            with self.subTest(title=title):
                lines = _limit_lines({
                    "conditions": {}, "limit_reason": "time", "replan_count": 3,
                    "evaluations": [{"movie_nm": title, "fits": False, "latest_start": "14:10"}],
                })
                self.assertIn(f"가장 가까웠던 {expected} 늦어도 14:10에 시작해야 합니다.", lines)

    def test_analysis_normalizes_bad_date_and_time_values(self):
        llm = FakeLLM(planning(date="2026-02-30", start_after="9:00", end_by="24:00", region="강남"))
        result = analyze("영화 보고 싶어요.", TODAY, None, llm)

        self.assertEqual(result.conditions, Conditions(region="강남"))
        self.assertIn("2026-11-18 (수요일)", llm.messages[0][0][1])

    def test_judge_handles_exact_deadline_midnight_and_unknown_runtime(self):
        result = evaluate({"runtime_min": 100}, {"start_after": "17:10", "end_by": "19:00"}, 10)
        self.assertTrue(result["fits"])
        self.assertEqual(result["latest_start"], "17:10")
        result = evaluate({"runtime_min": 100}, {"start_after": "23:30", "end_by": "00:30"}, 10)
        self.assertFalse(result["fits"])
        self.assertIsNone(result["latest_start"])
        self.assertIn("다음 날 01:20", result["reason"])
        self.assertIsNone(evaluate({}, {}, 10)["fits"])


if __name__ == "__main__":
    unittest.main()

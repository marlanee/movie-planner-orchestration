import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from langsmith.utils import tracing_is_enabled

from app.agent.detail_cache import DetailCache
from app.agent.graph import run_agent
from app.agent.memory import SessionMemory
from app.api import chat
from app.events.bus import EventCollector, bus
from app.main import app
from app.store import logs_repo, plans_repo
from tests.test_agent import (
    KOBIS_CODE, KOBIS_LATEST_START, KOBIS_RUNTIME, KOBIS_TITLE, THEATER, TODAY,
    FakeLLM, FakeTools, assert_no_kobis_event_data, planning, register_kobis_storage_fixture,
)


WATCH_DATE = "2026-11-21"
PRIVATE_ERROR = "예외 상세 https://example.test/private Traceback"


def fake_agent(session_id, message):
    start_after = None if message == "시작 미정" else "15:00"
    movie_title = "테스트" if message == "테스트 영화 예매" else None
    call = {"call_id": "test-call", "tool": "get_boxoffice", "provider": "KOBIS"}
    bus.publish(session_id, "stage_start", "analyze")
    bus.publish(session_id, "tool_call", "execute", {**call, "args": {"period": "daily"}})
    bus.publish(session_id, "tool_result", "execute", {
        **call, "ok": False, "code": "NO_DATA", "duration_ms": 8,
    }, duration_ms=8)
    bus.publish(session_id, "replan", "replan", {"replan_count": 1})
    bus.publish(session_id, "stage_end", "respond", duration_ms=3)
    return {
        "status": "ok",
        "answer": "추천 영화입니다.\n관람 시간을 확인해 주세요.",
        "recommendations": [{
            "movie_nm": "테스트 영화", "movie_cd": None, "tmdb_id": 520,
            "runtime_min": 120, "required_min": 130, "latest_start": "15:30",
            "watch_grade": "전체관람가", "genres": ["드라마"],
        }],
        "excluded": [],
        "theaters": {"region": "대전", "theaters": [{"name": "테스트 영화관", "address": "대전"}], "note": None},
        "criteria": {"date": WATCH_DATE, "start_after": start_after, "ad_minutes": 10},
        "evidence": [{"tool": "get_boxoffice", "args": {}, "ok": False, "code": "NO_RESULT", "summary": PRIVATE_ERROR}],
        "replan_count": 1,
        "estimated_note": None,
        "conditions": {"date": WATCH_DATE, "start_after": start_after, "movie_title": movie_title},
    }


class ApiTests(TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {
            "PLANNER_DB_PATH": str(Path(directory) / "planner.db"),
            "DISABLE_LOGGING_AGENT": "0",
            "KOBIS_API_KEY": "test-kobis-value",
            "KAKAO_REST_API_KEY": "",
            "TMDB_API_KEY": "test-tmdb-value",
            "GMS_KEY": "",
        }))
        self.enterContext(patch.object(chat, "run_agent", side_effect=fake_agent))

    def test_chat_response_trace_session_and_candidate_times(self):
        with TestClient(app) as client:
            response = client.post("/api/chat", json={"message": "영화 추천"})
            self.assertEqual(response.status_code, 200)
            result = response.json()
            self.assertEqual(set(result), {
                "session_id", "status", "answer", "recommendations", "excluded", "theaters",
                "criteria", "evidence", "replan_count", "estimated_note", "conditions",
                "limit_reason", "plan_candidates", "trace", "missing_keys",
            })
            session_id = result["session_id"]
            self.assertRegex(session_id, r"^[a-f0-9]{32}$")
            self.assertEqual(result["status"], "ok")
            self.assertIsNone(result["limit_reason"])
            self.assertEqual(result["missing_keys"], ["카카오", "GMS"])
            self.assertEqual(result["plan_candidates"], [{
                "movie_nm": None, "tmdb_id": 520,
                "watch_date": WATCH_DATE, "start_time": "15:00", "end_time": None,
                "theater_name": None, "theater_address": None,
            }])
            self.assertEqual([event["seq"] for event in result["trace"]], [1, 2, 3, 4, 5])
            self.assertEqual([event["type"] for event in result["trace"]], [
                "stage_start", "tool_call", "tool_result", "replan", "stage_end",
            ])
            self.assertTrue(all(event["session_id"] == session_id for event in result["trace"]))
            self.assertEqual(result["trace"][2]["duration_ms"], 8)
            self.assertEqual(result["trace"][2]["payload"], {
                "call_id": "test-call", "tool": "get_boxoffice", "provider": "KOBIS",
                "ok": False, "code": "NO_DATA", "duration_ms": 8,
            })
            self.assertNotIn(PRIVATE_ERROR, response.text)

            for message, title, start in [("시작 미정", None, None), ("테스트 영화 예매", "테스트", "15:00")]:
                with self.subTest(message=message):
                    response = client.post("/api/chat", json={"session_id": session_id, "message": message})
                    self.assertEqual(response.status_code, 200)
                    result = response.json()
                    self.assertEqual(result["session_id"], session_id)
                    candidate = result["plan_candidates"][0]
                    self.assertEqual((candidate["movie_nm"], candidate["start_time"], candidate["end_time"]),
                                     (title, start, None))

    def test_chat_limit_response_includes_reason_and_requested_title(self):
        chat.run_agent.side_effect = None
        with TestClient(app) as client:
            for reason in ("time", "genre", "mixed", "title_not_found"):
                with self.subTest(reason=reason):
                    chat.run_agent.return_value = {
                        "status": "limit", "answer": "재계획 상한 안내", "recommendations": [],
                        "conditions": {"movie_title": "요청한 영화"}, "limit_reason": reason,
                    }
                    response = client.post("/api/chat", json={"message": "요청한 영화"})
                    self.assertEqual(response.status_code, 200)
                    result = response.json()
                    self.assertEqual(result["status"], "limit")
                    self.assertEqual(result["limit_reason"], reason)
                    self.assertEqual(result["conditions"]["movie_title"], "요청한 영화")

    def test_live_kobis_fields_do_not_cross_trace_logs_or_plan_boundary(self):
        tools = FakeTools()
        register_kobis_storage_fixture(tools)
        llm = FakeLLM(
            planning(date=TODAY.isoformat(), start_after="15:00", end_by="19:00", region="강남"),
            planning(movie_title="고유제목"),
        )
        memory = SessionMemory()
        cache = DetailCache()
        traced = []

        def run_fixture(session_id, message):
            traced.append(tracing_is_enabled())
            return run_agent(session_id, message, llm=llm, tool_runner=tools, memory=memory, today=TODAY, detail_cache=cache)

        chat.run_agent.side_effect = run_fixture
        session_id = uuid4().hex
        collector = EventCollector(session_id)
        collector.attach(bus)
        self.addCleanup(collector.detach)

        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true"}), TestClient(app) as client:
            response = client.post("/api/chat", json={"session_id": session_id, "message": "영화 추천"})
            self.assertEqual(response.status_code, 200)
            result = response.json()
            self.assertEqual(result["status"], "ok")
            self.assertIn(KOBIS_TITLE, result["answer"])
            self.assertIn(f"{KOBIS_RUNTIME}분", result["answer"])
            self.assertEqual(len(result["recommendations"]), 1)
            recommendation = result["recommendations"][0]
            self.assertEqual(recommendation["movie_cd"], KOBIS_CODE)
            self.assertEqual(recommendation["runtime_min"], KOBIS_RUNTIME)
            self.assertEqual(recommendation["latest_start"], KOBIS_LATEST_START)
            self.assertEqual(result["excluded"], [{"movie_nm": "상세 실패 영화", "reason": "러닝타임 확인 불가(NO_RUNTIME)"}])
            self.assertEqual(result["theaters"]["theaters"], [THEATER])
            self.assertIn(KOBIS_TITLE, result["evidence"][0]["summary"])
            self.assertEqual(result["plan_candidates"], [{
                "movie_nm": None, "tmdb_id": None, "watch_date": TODAY.isoformat(),
                "start_time": "15:00", "end_time": None, "theater_name": None, "theater_address": None,
            }])

            stored = logs_repo.get_session_events(session_id)
            self.assertTrue(stored)
            self.assertEqual(stored, collector.events)
            self.assertEqual(result["trace"], stored)
            self.assertEqual(client.get(f"/api/logs/{session_id}").json()["events"], stored)
            assert_no_kobis_event_data(self, stored)

            response = client.post("/api/chat", json={"session_id": session_id, "message": "고유제목 보고 싶어요"})
            self.assertEqual(response.status_code, 200)
            candidate = response.json()["plan_candidates"][0]
            self.assertEqual(candidate["movie_nm"], "고유제목")
            self.assertIsNone(candidate["end_time"])
            self.assertEqual(traced, [False, False])
            self.assertEqual(tools.unexpected_calls, [])

    def test_chat_validation_and_agent_failure(self):
        with TestClient(app) as client:
            for message in ("", " \t\n", "가" * 1001):
                response = client.post("/api/chat", json={"message": message})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(set(response.json()), {"detail"})
            chat.run_agent.assert_not_called()

            def failing_agent(session_id, _):
                bus.publish(session_id, "error", "analyze", {"kind": "RuntimeError"})
                raise RuntimeError(PRIVATE_ERROR)

            chat.run_agent.side_effect = failing_agent
            response = client.post("/api/chat", json={"message": "영화 추천"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "error")
            self.assertEqual(response.json()["answer"], "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
            self.assertEqual(response.json()["trace"][0]["type"], "error")
            self.assertEqual(response.json()["trace"][0]["payload"], {"kind": "RuntimeError"})
            self.assertNotIn(PRIVATE_ERROR, response.text)

    def test_chat_session_id_validation(self):
        with TestClient(app) as client:
            for session_id in ("", "a" * 65, "a b", "abc\n"):
                with self.subTest(session_id=session_id):
                    response = client.post("/api/chat", json={"session_id": session_id, "message": "영화 추천"})
                    self.assertEqual(response.status_code, 422)
                    self.assertTrue(response.json()["detail"].startswith("세션 ID"))
            chat.run_agent.assert_not_called()

            for session_id in ("abc_123-X", "a", "a" * 64):
                with self.subTest(session_id=session_id):
                    response = client.post("/api/chat", json={"session_id": session_id, "message": "영화 추천"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["session_id"], session_id)

            response = client.post("/api/chat", json={"session_id": None, "message": "영화 추천"})
            self.assertEqual(response.status_code, 200)
            self.assertRegex(response.json()["session_id"], r"^[a-f0-9]{32}$")

    def test_logging_lifespan_and_log_endpoints(self):
        session_id = uuid4().hex
        path = f"/api/logs/{session_id}"
        with TestClient(app) as client:
            self.assertEqual(client.get(path).json(), {"detail": "그런 실행 로그가 없습니다."})
            result = client.post("/api/chat", json={"session_id": session_id, "message": "영화 추천"}).json()
            bus.publish(session_id, "error", "execute", {"kind": "RuntimeError"})

            sessions = client.get("/api/logs").json()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["session_id"], session_id)
            self.assertEqual(sessions[0]["event_count"], 6)
            self.assertEqual(sessions[0]["replan_count"], 1)
            self.assertEqual(sessions[0]["error_count"], 1)
            detail = client.get(path).json()
            self.assertEqual(detail["session_id"], session_id)
            self.assertEqual(detail["events"][:-1], result["trace"])
            self.assertEqual(detail["events"][-1]["seq"], 6)
            self.assertEqual(detail["events"][-1]["payload"], {"kind": "RuntimeError"})

            for method in ("PUT", "PATCH", "POST"):
                self.assertEqual(client.request(method, path, json={}).status_code, 405)
            self.assertEqual(client.delete(path).status_code, 204)
            self.assertEqual(client.get(path).status_code, 404)
            self.assertEqual(client.delete(path).status_code, 404)
            self.assertEqual(client.get("/api/logs").json(), [])

        bus.publish(session_id, "stage_start", "analyze")
        with patch.dict(os.environ, {"DISABLE_LOGGING_AGENT": "1"}), TestClient(app) as client:
            response = client.post("/api/chat", json={"session_id": session_id, "message": "영화 추천"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "ok")
            self.assertEqual(len(response.json()["trace"]), 5)
            self.assertEqual(client.get("/api/logs").json(), [])

    def test_plan_crud_with_date_filter_and_overlaps(self):
        first = {"movie_nm": " 첫 영화 ", "watch_date": WATCH_DATE, "start_time": "15:00", "end_time": "17:10"}
        second = {**first, "movie_nm": "두 번째 영화", "start_time": "16:00", "end_time": "18:00"}
        other_date = {**second, "watch_date": "2026-11-22"}
        with TestClient(app) as client:
            forbidden = {"movie_cd": KOBIS_CODE, "runtime_min": KOBIS_RUNTIME}
            response = client.post("/api/plans", json={**first, **forbidden})
            self.assertEqual(response.status_code, 201)
            saved_first = response.json()
            first_plan = saved_first["plan"]
            self.assertTrue(first_plan["created_at"])
            self.assertEqual(first_plan["movie_nm"], "첫 영화")
            self.assertTrue(forbidden.keys().isdisjoint(first_plan))
            stored_first = plans_repo.get_plan(first_plan["plan_id"])
            self.assertIsNone(stored_first["movie_cd"])
            self.assertIsNone(stored_first["runtime_min"])
            self.assertEqual(saved_first["overlaps"], [])
            self.assertIsNone(saved_first["warning"])

            response = client.post("/api/plans", json=second)
            self.assertEqual(response.status_code, 201)
            saved_second = response.json()
            second_plan = saved_second["plan"]
            self.assertEqual(saved_second["overlaps"], [first_plan])
            self.assertEqual(saved_second["warning"], "같은 날 시간이 겹치는 계획이 1건 있습니다: 첫 영화 15:00~17:10 (저장은 완료됨)")
            self.assertEqual(client.post("/api/plans", json=other_date).json()["overlaps"], [])

            self.assertEqual(len(client.get("/api/plans").json()), 3)
            self.assertEqual(client.get("/api/plans", params={"watch_date": WATCH_DATE}).json(), [first_plan, second_plan])
            path = f"/api/plans/{second_plan['plan_id']}"
            self.assertEqual(client.get(path).json(), second_plan)
            response = client.put(path, json={"start_time": "17:10", **forbidden})
            self.assertEqual(response.status_code, 200)
            updated = response.json()
            self.assertEqual(updated["overlaps"], [])
            self.assertEqual(updated["plan"]["start_time"], "17:10")
            self.assertEqual(updated["plan"]["end_time"], "18:00")
            self.assertEqual(updated["plan"]["movie_nm"], "두 번째 영화")
            self.assertTrue(updated["plan"]["updated_at"])
            self.assertTrue(forbidden.keys().isdisjoint(updated["plan"]))
            stored_second = plans_repo.get_plan(second_plan["plan_id"])
            self.assertIsNone(stored_second["movie_cd"])
            self.assertIsNone(stored_second["runtime_min"])
            self.assertEqual(client.put(path, json={}).status_code, 422)
            self.assertEqual(client.delete(path).status_code, 204)
            self.assertEqual(client.get(path).status_code, 404)
            self.assertEqual(client.put(path, json={"memo": "수정"}).status_code, 404)
            self.assertEqual(client.delete(path).status_code, 404)

    def test_plan_validation_hides_input_and_returns_repository_detail(self):
        plan = {"movie_nm": "테스트", "watch_date": WATCH_DATE, "start_time": "15:00", "end_time": "17:00"}
        with TestClient(app) as client:
            for invalid in (
                {key: value for key, value in plan.items() if key != "watch_date"},
                {**plan, "watch_date": PRIVATE_ERROR},
                {**plan, "watch_date": "2026-02-30"},
                {**plan, "start_time": "24:00"},
                {**plan, "movie_nm": "  "},
            ):
                response = client.post("/api/plans", json=invalid)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(set(response.json()), {"detail"})
                self.assertNotIn(PRIVATE_ERROR, response.text)
            response = client.post("/api/plans", json={**plan, "end_time": "15:00"})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json(), {"detail": "종료 시각은 시작 시각보다 늦어야 합니다."})

    def test_config_health_cors_and_routes(self):
        with TestClient(app) as client:
            response = client.get("/api/chat/config")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {
                "keys": {"kobis": True, "kakao": False, "tmdb": True, "gms": False},
                "missing_keys": ["카카오", "GMS"],
            })
            self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
            for origin in ("http://localhost:5500", "http://127.0.0.1:5500"):
                response = client.options("/api/chat", headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["access-control-allow-origin"], origin)
            paths = client.get("/openapi.json").json()["paths"]
            self.assertEqual(set(paths), {
                "/api/chat", "/api/chat/config", "/api/plans", "/api/plans/{plan_id}",
                "/api/logs", "/api/logs/{session_id}", "/api/logs/{session_id}/anomalies", "/api/health",
            })
            self.assertEqual(set(paths["/api/logs"]), {"get"})
            self.assertEqual(set(paths["/api/logs/{session_id}"]), {"get", "delete"})
            self.assertTrue(set(paths) <= {route.path for route in app.routes})

    def test_unhandled_error_has_generic_response_and_cors(self):
        origin = "http://localhost:5500"
        with TestClient(app, raise_server_exceptions=False) as client:
            with patch("app.api.plans.plans_repo.list_plans", side_effect=RuntimeError(PRIVATE_ERROR)):
                response = client.get("/api/plans", headers={"Origin": origin})
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json(), {"detail": "서버 오류가 발생했습니다."})
            self.assertEqual(response.headers["access-control-allow-origin"], origin)

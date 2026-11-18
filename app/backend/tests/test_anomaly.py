import ast
import json
import os
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread, current_thread
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.agent.detail_cache import DetailCache
from app.agent.graph import run_agent
from app.agent.memory import SessionMemory
from app.agent.nodes import AgentNodes
from app.events.bus import EventBus
from app.logging_agent.agent import LoggingAgent
from app.logging_agent.diagnoser import diagnose
from app.logging_agent.evaluator import evaluate_run, split_runs
from app.main import app
from app.store import anomaly_repo, db, logs_repo
from tests.test_agent import (
    DAILY, KOBIS_CODE, KOBIS_TITLE, TODAY, WEEKLY, FakeLLM, FakeTools,
    assert_no_kobis_event_data, ok, planning, register_kobis_storage_fixture,
)


def begin(bus, session="one", tools=()):
    bus.publish(session, "stage_start", "analyze")
    bus.publish(session, "stage_end", "plan", {"plan": [{"tool": tool} for tool in tools]})


def finish(bus, session="one"):
    bus.publish(session, "stage_end", "respond", {"status": "ok"})


def publish_call(bus, tool, call_id, *, duplicate_of=None, with_result=True):
    payload = {"tool": tool, "call_id": call_id}
    call = bus.publish("one", "tool_call", "execute", {**payload, "duplicate_of": duplicate_of})
    if with_result:
        bus.publish("one", "tool_result", "execute", {**payload, "ok": True})
    return call


def publish_duplicate_run(bus):
    begin(bus, tools=["get_boxoffice"])
    publish_call(bus, "get_boxoffice", "first")
    publish_call(bus, "get_boxoffice", "second", duplicate_of="first")
    finish(bus)


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.events = []
        self.bus.subscribe(self.events.append)

    def test_rule_evaluator_imports_no_llm_or_application_modules(self):
        from app.logging_agent import evaluator

        module = ast.parse(Path(evaluator.__file__).read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(module)))

    def test_repeat_has_both_call_sequences_and_missing_evidence_takes_precedence(self):
        publish_duplicate_run(self.bus)
        report = evaluate_run(self.events, 3)
        self.assertEqual(report["verdict"], "anomaly")
        finding, = report["findings"]
        self.assertEqual(finding["rule"], "repeated_call")
        self.assertEqual(finding["evidence_seqs"], [3, 5])
        self.assertEqual(finding["detail"], "get_boxoffice 호출이 같은 입력으로 반복됐습니다.")
        self.assertEqual(report["warning_count"], 0)
        self.assertEqual(evaluate_run(self.events[:-1], 3)["verdict"], "undetermined")

    def test_replan_above_limit_is_anomaly(self):
        begin(self.bus)
        for count in range(1, 5):
            self.bus.publish("one", "replan", "replan", {"replan_count": count})
        finish(self.bus)
        report = evaluate_run(self.events, 3)
        self.assertEqual(report["verdict"], "anomaly")
        self.assertEqual(report["warning_count"], 0)
        finding, = report["findings"]
        self.assertEqual(finding["rule"], "excessive_replan")
        self.assertEqual(finding["evidence_seqs"], [3, 4, 5, 6])

    def test_replan_pool_is_allowed_and_unplanned_tool_is_evidence(self):
        begin(self.bus, tools=["get_boxoffice"])
        self.bus.publish("one", "replan", "replan", {
            "replan_count": 1, "next_pool": "TMDB 현재 상영작 1페이지",
        })
        publish_call(self.bus, "get_now_playing", "replan-call")
        unexpected = publish_call(self.bus, "search_theaters", "unexpected")
        finish(self.bus)
        report = evaluate_run(self.events, 3)
        self.assertEqual(report["verdict"], "anomaly")
        finding, = report["findings"]
        self.assertEqual(finding["rule"], "unplanned_call")
        self.assertEqual(finding["evidence_seqs"], [unexpected["seq"]])
        self.assertEqual(finding["detail"], "search_theaters 호출이 계획에 없었습니다.")

    def test_missing_required_events_are_undetermined(self):
        begin(self.bus, tools=["get_boxoffice"])
        publish_call(self.bus, "get_boxoffice", "first", with_result=False)
        finish(self.bus)
        for events, expected in (
            (self.events, "결과 이벤트"),
            (self.events[1:], "요청 분석 시작"),
            (self.events[:-1], "응답 생성 종료"),
            (self.events[:1] + self.events[2:], "작업 계획 종료"),
        ):
            with self.subTest(expected=expected):
                report = evaluate_run(events, 3)
                self.assertEqual(report["verdict"], "undetermined")
                self.assertIn(expected, " ".join(report["missing_evidence"]))

    def test_split_runs_preserves_incomplete_prefix_and_empty_plans(self):
        self.bus.publish("one", "error", "agent")
        begin(self.bus)
        finish(self.bus)
        begin(self.bus)
        finish(self.bus)
        partial, first, second = split_runs(self.events)
        self.assertEqual(evaluate_run(partial, 3)["verdict"], "undetermined")
        self.assertEqual(evaluate_run(first, 3)["verdict"], "normal")
        self.assertEqual(evaluate_run(second, 3)["verdict"], "normal")
        self.assertEqual([event["seq"] for event in first], [2, 3, 4])


class DiagnoserTests(unittest.TestCase):
    def test_four_sentences_without_space_before_tool_name_are_trimmed(self):
        llm = Mock()
        llm.invoke.return_value = SimpleNamespace(content=(
            "같은 호출이 발생했습니다.get_movie_detail도 반복됐습니다. 입력을 확인하세요. 넷째 문장입니다."
        ))
        self.assertEqual(diagnose({"verdict": "anomaly", "findings": []}, [], llm=llm), {
            "status": "ok",
            "text": "같은 호출이 발생했습니다. get_movie_detail도 반복됐습니다. 입력을 확인하세요.",
        })

    def test_numbered_items_do_not_count_as_sentences(self):
        items = ["1. 첫 문장입니다.", "2. 둘째입니다.", "3. 셋째입니다.", "4. 넷째입니다."]
        for separator in ("\n", " "):
            with self.subTest(separator=separator):
                llm = Mock()
                llm.invoke.return_value = SimpleNamespace(content=separator.join(items))
                self.assertEqual(diagnose({"verdict": "anomaly", "findings": []}, [], llm=llm), {
                    "status": "ok", "text": "첫 문장입니다. 둘째입니다. 셋째입니다.",
                })


class AnomalyIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {
            "PLANNER_DB_PATH": str(Path(directory) / "anomaly.db"), "LANGSMITH_TRACING": "false",
        }))
        self.enterContext(patch("app.logging_agent.agent.REPLAN_LIMIT", 3))
        self.enterContext(patch("app.agent.nodes.REPLAN_LIMIT", 3))
        self.bus = EventBus()

    def attach_logging(self, llm):
        logging_agent = LoggingAgent(diagnose_run=lambda report, events: diagnose(report, events, llm=llm))
        logging_agent.start(self.bus)
        self.addCleanup(logging_agent.stop)
        return logging_agent

    def capture_diagnosis_save(self):
        saved = Event()
        append = anomaly_repo.append_diagnosis

        def save(*args):
            try:
                append(*args)
            finally:
                saved.set()

        self.enterContext(patch.object(anomaly_repo, "append_diagnosis", side_effect=save))
        return saved

    def run_graph(self, tools, **options):
        return run_agent(
            "one", "영화 관람 계획", llm=FakeLLM(planning(start_after="15:00", end_by="19:00")),
            tool_runner=tools, event_bus=self.bus, memory=SessionMemory(), today=TODAY,
            detail_cache=DetailCache(), **options,
        )

    def test_normal_graph_and_previous_verdict_from_database_or_injected_reader(self):
        llm = Mock()
        logger = self.attach_logging(llm)
        tools = FakeTools()
        register_kobis_storage_fixture(tools)

        self.assertEqual(self.run_graph(tools)["status"], "ok")
        events = logs_repo.get_session_events("one")
        calls = [event["payload"] for event in events if event["type"] == "tool_call"]
        self.assertTrue(calls)
        self.assertFalse(any(call.get("duplicate_of") for call in calls))
        assert_no_kobis_event_data(self, events)
        report, = anomaly_repo.list_reports("one")
        self.assertEqual(report["verdict"], "normal")
        self.assertIsNone(report["diagnosis"])

        self.assertEqual(self.run_graph(tools)["status"], "ok")
        first, second = split_runs(logs_repo.get_session_events("one"))
        first_decision = next(event for event in first if event["type"] == "llm_decision")
        second_decision = next(event for event in second if event["type"] == "llm_decision")
        self.assertNotIn("previous_run_verdict", first_decision["payload"])
        self.assertEqual(second_decision["payload"]["previous_run_verdict"], {
            "run_index": 1, "verdict": "normal", "warning_count": 0, "rules": [],
        })
        self.assertEqual(anomaly_repo.latest_verdict("one"), {
            "run_index": 2, "verdict": "normal", "warning_count": 0, "rules": [],
        })
        reports = anomaly_repo.list_reports("one")
        self.assertEqual([report["run_index"] for report in reports], [1, 2])
        self.assertEqual((reports[1]["first_seq"], reports[1]["last_seq"]), (
            second[0]["seq"], second[-1]["seq"],
        ))
        unavailable = Mock(side_effect=OSError("read failed"))
        self.assertEqual(self.run_graph(tools, read_previous_verdict=unavailable)["status"], "ok")
        unavailable.assert_called_once_with("one")
        self.assertEqual(tools.unexpected_calls, [])
        self.assertEqual(logger.failed_count, 0)
        llm.invoke.assert_not_called()

    def test_real_graph_replans_three_times_without_diagnosis(self):
        llm = Mock()
        self.attach_logging(llm)
        tools = FakeTools()
        tools.register("get_boxoffice", ok(movies=[]), **DAILY)
        tools.register("get_boxoffice", ok(movies=[]), **WEEKLY)
        for page in (1, 2):
            tools.register("get_now_playing", ok(movies=[]), page=page)
        result = self.run_graph(tools)
        self.assertEqual(result["replan_count"], 3)
        report, = anomaly_repo.list_reports("one")
        self.assertEqual((report["verdict"], report["warning_count"]), ("normal", 1))
        self.assertEqual([finding["rule"] for finding in report["findings"]], ["excessive_replan"])
        self.assertIn("상한에 도달", report["findings"][0]["detail"])
        self.assertIsNone(report["diagnosis"])
        self.assertEqual(tools.unexpected_calls, [])
        llm.invoke.assert_not_called()

    def test_duplicate_detection_private_data_and_separate_three_sentence_diagnosis(self):
        llm = Mock()
        llm.invoke.return_value = SimpleNamespace(content="첫 문장입니다. 둘째입니다. 셋째입니다. 넷째입니다.")
        self.attach_logging(llm)
        saved = self.capture_diagnosis_save()
        tools = FakeTools()
        register_kobis_storage_fixture(tools)
        nodes = AgentNodes(None, tools, self.bus, SessionMemory())
        begin(self.bus, tools=["get_movie_detail"])
        state = {"session_id": "one", "failures": [], "tool_results": []}
        for _ in range(2):
            nodes._call_tool(state, "get_movie_detail", {"movie_cd": KOBIS_CODE})
        finish(self.bus)
        self.assertTrue(saved.wait(5))

        events = logs_repo.get_session_events("one")
        first, second = [event["payload"] for event in events if event["type"] == "tool_call"]
        self.assertNotIn("duplicate_of", first)
        self.assertEqual(second["duplicate_of"], first["call_id"])
        self.assertEqual(len(tools.calls), 2)
        assert_no_kobis_event_data(self, events)
        report, = anomaly_repo.list_reports("one")
        self.assertEqual((report["verdict"], report["warning_count"]), ("anomaly", 0))
        self.assertEqual([finding["rule"] for finding in report["findings"]], ["repeated_call"])
        self.assertEqual(report["diagnosis"], {"status": "ok", "text": "첫 문장입니다. 둘째입니다. 셋째입니다."})
        llm.invoke.assert_called_once()
        sent = json.loads(llm.invoke.call_args.args[0][1][1])
        self.assertEqual(set(sent), {"events", "findings"})
        self.assertEqual(sent["findings"], report["findings"])
        with closing(db.get_conn()) as conn:
            findings, = conn.execute("SELECT findings FROM anomaly_reports").fetchone()
            diagnosis = conn.execute("SELECT status, text FROM anomaly_diagnoses WHERE report_id = ?", (
                report["id"],
            )).fetchone()
        self.assertEqual(json.loads(findings), report["findings"])
        self.assertEqual(dict(diagnosis), report["diagnosis"])
        for value in (KOBIS_TITLE, KOBIS_CODE, "runtime_min", "latest_start", "17:27"):
            self.assertNotIn(value, json.dumps([sent, report], ensure_ascii=False))

    def test_duplicate_key_is_order_independent_and_run_scoped(self):
        nodes = AgentNodes(None, None, self.bus, SessionMemory())
        events = []
        self.bus.subscribe(events.append)
        state = {"session_id": "one"}
        nodes._publish_tool_call(state, "get_boxoffice", {"period": "daily", "target_date": "2026-11-18"})
        nodes._publish_tool_call(state, "get_boxoffice", {"target_date": "2026-11-18", "period": "daily"})
        nodes._publish_tool_call(state, "get_boxoffice", {"period": "weekly", "target_date": "2026-11-18"})
        nodes._publish_tool_call({"session_id": "one"}, "get_boxoffice", DAILY)
        first, second, different, next_run = (event["payload"] for event in events)
        self.assertEqual(second["duplicate_of"], first["call_id"])
        self.assertNotIn("duplicate_of", different)
        self.assertNotIn("duplicate_of", next_run)

    def test_diagnosis_exception_preserves_report_and_logs(self):
        llm = Mock()
        llm.invoke.side_effect = RuntimeError("PRIVATE-FAILURE")
        logger = self.attach_logging(llm)
        saved = self.capture_diagnosis_save()
        publish_duplicate_run(self.bus)
        self.assertTrue(saved.wait(5))
        report, = anomaly_repo.list_reports("one")
        self.assertEqual(report["verdict"], "anomaly")
        self.assertEqual(report["diagnosis"], {"status": "failed", "text": None})
        self.assertEqual(len(logs_repo.get_session_events("one")), 7)
        self.assertEqual(logger.failed_count, 1)
        llm.invoke.assert_called_once()

    def test_no_llm_for_missing_evidence_and_failed_missing_key_is_sanitized(self):
        llm = Mock()
        self.attach_logging(llm)
        begin(self.bus, tools=["get_boxoffice"])
        publish_call(self.bus, "get_boxoffice", "first", with_result=False)
        self.bus.publish("one", "error", "agent")
        report, = anomaly_repo.list_reports("one")
        self.assertEqual(report["verdict"], "undetermined")
        self.assertIsNone(report["diagnosis"])
        self.assertEqual(diagnose(report, [], llm=llm), {"status": "skipped", "text": None})
        llm.invoke.assert_not_called()
        with patch.dict(os.environ, {"GMS_KEY": ""}):
            self.assertEqual(diagnose({"verdict": "anomaly"}, []), {"status": "failed", "text": None})

    def test_bus_restart_and_repeated_terminal_record_keep_one_report_per_run(self):
        llm = Mock()
        logger = self.attach_logging(llm)
        begin(self.bus)
        finish(self.bus)
        self.bus.publish("one", "error", "agent")
        logger.stop()

        restarted_bus = EventBus()
        logger.start(restarted_bus)
        begin(restarted_bus)
        finish(restarted_bus)
        reports = anomaly_repo.list_reports("one")
        self.assertEqual([(r["run_index"], r["first_seq"], r["last_seq"]) for r in reports], [
            (1, 1, 3), (2, 1, 3),
        ])
        self.assertEqual(logger.failed_count, 0)
        llm.invoke.assert_not_called()

    def test_diagnosis_is_nonblocking_untraced_and_cannot_reappear_after_delete(self):
        began, release, published = Event(), Event(), Event()
        llm = Mock()
        observation = {}

        def invoke(_messages):
            from langsmith import utils

            observation.update(daemon=current_thread().daemon, tracing=utils.tracing_is_enabled())
            began.set()
            release.wait(10)
            return SimpleNamespace(content="중복 호출을 제거하세요.")

        def publish():
            publish_duplicate_run(self.bus)
            published.set()

        llm.invoke.side_effect = invoke
        self.attach_logging(llm)
        saved = self.capture_diagnosis_save()
        publisher = Thread(target=publish, daemon=True)
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true"}):
            publisher.start()
            try:
                self.assertTrue(began.wait(5))
                self.assertTrue(published.wait(5), "이벤트 발행이 진단 완료를 기다렸습니다.")
                self.assertEqual(observation, {"daemon": True, "tracing": False})
                report, = anomaly_repo.list_reports("one")
                self.assertEqual(report["verdict"], "anomaly")
                self.assertIsNone(report["diagnosis"])
                self.assertEqual(logs_repo.delete_session("one"), 7)
            finally:
                release.set()
                publisher.join(5)
                self.assertTrue(saved.wait(5))
        self.assertIsNone(anomaly_repo.list_reports("one"))
        with closing(db.get_conn()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM anomaly_diagnoses").fetchone()[0], 0)

    def test_api_reads_reports_rejects_edits_and_deletes_both_tables(self):
        llm = Mock()
        llm.invoke.return_value = SimpleNamespace(content="중복 호출을 제거하세요.")
        self.attach_logging(llm)
        saved = self.capture_diagnosis_save()
        publish_duplicate_run(self.bus)
        self.assertTrue(saved.wait(5))
        self.bus.publish("legacy", "stage_start", "plan")
        path = "/api/logs/one/anomalies"
        with patch.dict(os.environ, {"DISABLE_LOGGING_AGENT": "1"}), TestClient(app) as client:
            self.assertEqual(client.get("/api/logs/missing/anomalies").status_code, 404)
            self.assertEqual(client.get("/api/logs/legacy/anomalies").json(), [])
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            report, = response.json()
            self.assertEqual(report["verdict"], "anomaly")
            self.assertEqual(report["diagnosis"], {"status": "ok", "text": "중복 호출을 제거하세요."})
            for method in ("PUT", "PATCH", "POST", "DELETE"):
                self.assertEqual(client.request(method, path).status_code, 405)
            self.assertEqual(client.delete("/api/logs/one").status_code, 204)
            self.assertEqual(client.get(path).status_code, 404)
        with closing(db.get_conn()) as conn:
            for table in ("anomaly_reports", "anomaly_diagnoses"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

import ast
import inspect
import os
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.events.bus import EventBus, EventCollector
from app.logging_agent.agent import LoggingAgent
from app.store import db, logs_repo, plans_repo


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.db_file = Path(directory) / "data" / "planner.db"
        self.enterContext(patch.dict(os.environ, {"PLANNER_DB_PATH": str(self.db_file)}))


class EventBusTests(StoreTestCase):
    def test_publish_without_subscribers_and_session_sequences(self):
        bus = EventBus()
        timestamp = datetime(2026, 11, 18, 9, tzinfo=timezone(timedelta(hours=9)))
        with patch("app.events.bus.datetime") as clock:
            clock.now.return_value = timestamp
            first = bus.publish("one", "stage_start", "fetch")
        other = bus.publish("two", "stage_start", "fetch")
        second = bus.publish("one", "tool_call", "fetch")
        third = bus.publish("one", "stage_end", "fetch", duration_ms=42)

        self.assertEqual(first, {
            "session_id": "one", "seq": 1,
            "ts": "2026-11-18T09:00:00.000000+09:00", "type": "stage_start",
            "stage": "fetch", "payload": {}, "duration_ms": None,
        })
        self.assertEqual(datetime.fromisoformat(first["ts"]).utcoffset(), timedelta(hours=9))
        self.assertEqual([first["seq"], second["seq"], third["seq"], other["seq"]], [1, 2, 3, 1])
        self.assertEqual(third["duration_ms"], 42)
        self.assertEqual(bus.publish("two", "stage_end", "fetch")["seq"], 2)

    def test_concurrent_publications_assign_unique_sequences(self):
        bus = EventBus()

        def publish_event(_):
            return bus.publish("one", "tool_call", "fetch")["seq"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            sequences = list(pool.map(publish_event, range(40)))
        self.assertEqual(sorted(sequences), list(range(1, 41)))

    def test_unknown_type_is_preserved_and_marked(self):
        payload = {"value": 42}
        event = EventBus().publish("one", "custom", "fetch", payload)
        self.assertEqual(event["type"], "custom")
        self.assertEqual(event["payload"], {"value": 42, "unknown_type": True})
        self.assertEqual(payload, {"value": 42})

    def test_non_dict_payload_is_wrapped_even_for_unknown_type(self):
        event = EventBus().publish("one", "custom", "fetch", [1, 2])

        self.assertEqual(event["type"], "custom")
        self.assertEqual(event["payload"], {"value": [1, 2], "unknown_type": True})

    def test_subscriber_mutations_are_isolated_from_other_subscribers_and_return(self):
        bus = EventBus()
        received = []

        def mutate(event):
            event["stage"] = "changed"
            event["payload"]["nested"]["values"].append(2)

        bus.subscribe(mutate)
        bus.subscribe(received.append)
        event = bus.publish("one", "tool_call", "fetch", {"nested": {"values": [1]}})

        self.assertEqual(len(received), 1)
        for delivered in (event, received[0]):
            self.assertEqual(delivered["stage"], "fetch")
            self.assertEqual(delivered["payload"], {"nested": {"values": [1]}})
        self.assertEqual(bus.subscriber_errors, 0)

    def test_subscriber_exception_is_isolated_and_unsubscribe_is_idempotent(self):
        bus = EventBus()
        received = []

        def fail(_event):
            raise RuntimeError("subscriber failed")

        bus.subscribe(fail)
        unsubscribe = bus.subscribe(received.append)
        first = bus.publish("one", "tool_call", "fetch")
        self.assertEqual(received, [first])
        self.assertEqual(bus.subscriber_errors, 1)

        unsubscribe()
        unsubscribe()
        self.assertEqual(bus.publish("one", "tool_result", "fetch")["seq"], 2)
        self.assertEqual(received, [first])
        self.assertEqual(bus.subscriber_errors, 2)

    def test_collector_keeps_only_its_session_until_detached(self):
        bus = EventBus()
        collector = EventCollector("one")
        collector.attach(bus)
        collector.attach(bus)
        first = bus.publish("one", "stage_start", "fetch")
        bus.publish("two", "stage_start", "fetch")
        second = bus.publish("one", "stage_end", "fetch")
        collector.detach()
        bus.publish("one", "tool_call", "fetch")
        self.assertEqual(collector.events, [first, second])


class DatabaseTests(StoreTestCase):
    def test_get_conn_creates_parent_and_enables_foreign_keys(self):
        self.assertFalse(self.db_file.parent.exists())
        self.assertEqual(db.db_path(), self.db_file)
        with closing(db.get_conn()) as first, closing(db.get_conn()) as second:
            self.assertIsNot(first, second)
            self.assertIs(first.row_factory, sqlite3.Row)
            self.assertEqual(first.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertTrue(self.db_file.is_file())

    def test_default_path_and_idempotent_schema_initialization(self):
        with patch.dict(os.environ, clear=True):
            self.assertEqual(db.db_path(), BACKEND / "data" / "planner.db")

        db.init_db()
        db.init_db()
        with closing(db.get_conn()) as conn:
            indexes = conn.execute("PRAGMA index_list(run_logs)")
            self.assertIn("idx_run_logs_session_id", [row["name"] for row in indexes])

    def test_empty_db_path_uses_default(self):
        with patch.dict(os.environ, {"PLANNER_DB_PATH": ""}):
            self.assertEqual(db.db_path(), BACKEND / "data" / "planner.db")


class LoggingAgentTests(StoreTestCase):
    def test_saves_events_in_isolated_sessions_and_stops(self):
        bus = EventBus()
        agent = LoggingAgent()
        agent.start(bus)
        agent.start(bus)
        collector = EventCollector("one")
        collector.attach(bus)
        self.addCleanup(collector.detach)
        call = {"tool": "get_movie_detail", "call_id": "detail-1", "provider": "KOBIS"}
        bus.publish("one", "tool_call", "execute", {**call, "arg_names": ["movie_cd"]})
        other = bus.publish("two", "stage_start", "fetch")
        bus.publish("one", "tool_result", "execute", {
            **call, "ok": False, "code": "NO_RUNTIME", "duration_ms": 15,
        }, duration_ms=15)
        collector.detach()
        agent.stop()
        agent.stop()
        bus.publish("one", "tool_call", "fetch")

        self.assertEqual(logs_repo.get_session_events("one"), collector.events)
        self.assertEqual(len(collector.events), 2)
        self.assertEqual(logs_repo.get_session_events("two"), [other])
        self.assertEqual(agent.saved_count, 3)
        self.assertEqual(agent.failed_count, 0)

    def test_append_failure_does_not_escape_to_bus_or_publisher(self):
        bus = EventBus()
        received = []

        def fail(_event):
            raise sqlite3.OperationalError("storage failed")

        agent = LoggingAgent(append=fail)
        agent.start(bus)
        bus.subscribe(received.append)
        event = bus.publish("one", "stage_start", "fetch")

        self.assertEqual(received, [event])
        self.assertEqual(agent.failed_count, 1)
        self.assertEqual(agent.saved_count, 0)
        self.assertEqual(bus.subscriber_errors, 0)

    def test_logging_sources_import_only_standard_library_and_allowed_local_modules(self):
        sources = (
            BACKEND / "app" / "logging_agent" / "agent.py",
            BACKEND / "app" / "logging_agent" / "__init__.py",
            BACKEND / "app" / "store" / "logs_repo.py",
        )
        # evaluator는 규칙만 실행하며, diagnoser의 LLM import는 이상 판정 후에만 실행된다.
        allowed_relative_imports = {
            "..config", "..events", "..events.bus", "..store", "..store.logs_repo",
            ".db", ".", ".diagnoser", ".evaluator",
        }
        for path in sources:
            for module in self._imported_modules(path.read_text(encoding="utf-8")):
                with self.subTest(path=path.relative_to(BACKEND), module=module):
                    if module.startswith("."):
                        self.assertIn(module, allowed_relative_imports)
                    else:
                        self.assertIn(module.split(".", 1)[0], sys.stdlib_module_names)

    @staticmethod
    def _imported_modules(source):
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                yield from (item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                yield "." * node.level + (node.module or "")


class LogsRepoTests(StoreTestCase):
    def test_session_aggregates_event_order_json_and_deletion(self):
        bus = EventBus()
        first = bus.publish("one", "stage_start", "fetch", {"메모": Path("movie")})
        second = bus.publish("one", "replan", "plan")
        third = bus.publish("one", "error", "plan")
        other = bus.publish("two", "tool_call", "fetch")
        first["ts"] = "2026-11-18T09:00:00+09:00"
        second["ts"] = "2026-11-18T09:01:00+09:00"
        third["ts"] = "2026-11-18T09:02:00+09:00"
        other["ts"] = "2026-11-18T09:03:00+09:00"
        for event in (third, other, first, second):
            logs_repo.append_event(event)

        sessions = logs_repo.list_sessions()
        self.assertEqual(sessions, [
            {"session_id": "two", "first_ts": other["ts"], "last_ts": other["ts"],
             "event_count": 1, "replan_count": 0, "error_count": 0},
            {"session_id": "one", "first_ts": first["ts"], "last_ts": third["ts"],
             "event_count": 3, "replan_count": 1, "error_count": 1},
        ])
        self.assertEqual(logs_repo.get_session_events("one"), [
            third, {**first, "payload": {"메모": "movie"}}, second,
        ])
        with closing(db.get_conn()) as conn:
            payload = conn.execute("SELECT payload FROM run_logs WHERE session_id = 'one' AND seq = 1")
            self.assertIn("메모", payload.fetchone()[0])

        self.assertEqual(logs_repo.delete_session("one"), 3)
        self.assertEqual(logs_repo.delete_session("one"), 0)
        self.assertEqual(logs_repo.get_session_events("one"), [])
        self.assertEqual(logs_repo.list_sessions(), [sessions[0]])

    def test_stored_event_order_survives_bus_restart_with_same_session(self):
        first_bus = EventBus()
        agent = LoggingAgent()
        agent.start(first_bus)
        first_run = [
            first_bus.publish("one", "tool_call", stage)
            for stage in ("A1", "A2", "A3")
        ]
        agent.stop()

        restarted_bus = EventBus()
        agent.start(restarted_bus)
        second_run = [
            restarted_bus.publish("one", "tool_call", stage)
            for stage in ("B1", "B2")
        ]
        agent.stop()

        stored = logs_repo.get_session_events("one")
        self.assertEqual(stored, first_run + second_run)
        self.assertEqual([event["seq"] for event in stored], [1, 2, 3, 1, 2])

    def test_no_public_log_editing_functions(self):
        functions = inspect.getmembers(logs_repo, inspect.isfunction)
        self.assertFalse(any(
            "update" in name or "edit" in name for name, _ in functions if not name.startswith("_")
        ))


class PlansRepoTests(StoreTestCase):
    base_plan = {
        "movie_nm": "관람작", "watch_date": "2026-11-18",
        "start_time": "10:00", "end_time": "12:00",
    }

    def create(self, **changes):
        return plans_repo.create_plan({**self.base_plan, **changes})

    def test_create_get_list_and_delete_include_plan_columns(self):
        plan, overlaps = self.create(movie_cd="123", tmdb_id=123, runtime_min=120)
        self.assertEqual(overlaps, [])
        self.assertEqual(set(plan), {
            "plan_id", "movie_nm", "movie_cd", "tmdb_id", "watch_date", "start_time", "end_time",
            "runtime_min", "theater_name", "theater_address", "memo", "session_id", "created_at", "updated_at",
        })
        self.assertEqual(datetime.fromisoformat(plan["created_at"]).utcoffset(), timedelta(hours=9))
        self.assertIsNone(plan["movie_cd"])
        self.assertIsNone(plan["runtime_min"])
        self.assertEqual(plan["tmdb_id"], 123)
        self.assertIsNone(plan["updated_at"])
        self.assertEqual(plans_repo.get_plan(plan["plan_id"]), plan)
        self.assertEqual(plans_repo.list_plans(), [plan])
        self.assertTrue(plans_repo.delete_plan(plan["plan_id"]))
        self.assertIsNone(plans_repo.get_plan(plan["plan_id"]))
        self.assertFalse(plans_repo.delete_plan(plan["plan_id"]))

    def test_overlaps_saved_and_excluded_for_other_days_separate_times_and_boundaries(self):
        other_day, overlaps = self.create(watch_date="2026-11-19")
        self.assertEqual(overlaps, [])
        boundary, overlaps = self.create(start_time="12:00", end_time="13:00")
        self.assertEqual(overlaps, [])
        first, overlaps = self.create()
        self.assertEqual(overlaps, [])
        second, overlaps = self.create(start_time="11:00", end_time="11:30")
        self.assertEqual(overlaps, [first])
        self.assertEqual(plans_repo.get_plan(second["plan_id"]), second)
        later, overlaps = self.create(start_time="14:00", end_time="15:00")
        self.assertEqual(overlaps, [])
        self.assertEqual(plans_repo.list_plans("2026-11-18"), [first, second, boundary, later])
        self.assertEqual(plans_repo.list_plans(), [first, second, boundary, later, other_day])

    def test_partial_update_revalidates_and_recalculates_overlaps(self):
        first, _ = self.create(memo="유지할 메모")
        second, _ = self.create(start_time="12:00", end_time="14:00")

        updated, overlaps = plans_repo.update_plan(first["plan_id"], {
            "end_time": "12:30", "movie_cd": "KOBIS-CODE-777", "runtime_min": 137,
        })
        self.assertEqual(overlaps, [second])
        self.assertEqual(updated["end_time"], "12:30")
        self.assertEqual(updated["memo"], first["memo"])
        self.assertEqual(updated["created_at"], first["created_at"])
        self.assertIsNone(updated["movie_cd"])
        self.assertIsNone(updated["runtime_min"])
        self.assertEqual(datetime.fromisoformat(updated["updated_at"]).utcoffset(), timedelta(hours=9))

        with self.assertRaisesRegex(ValueError, "종료 시각"):
            plans_repo.update_plan(first["plan_id"], {"start_time": "12:30"})
        self.assertEqual(plans_repo.get_plan(first["plan_id"]), updated)
        self.assertIsNone(plans_repo.update_plan(999, {"end_time": "12:30"}))

    def test_invalid_required_fields_dates_and_times_raise_value_error(self):
        for field in self.base_plan:
            with self.subTest(missing=field), self.assertRaisesRegex(ValueError, "필수"):
                plans_repo.create_plan({key: val for key, val in self.base_plan.items() if key != field})

        invalid_values = (
            ("watch_date", "2026-1-18", "관람 날짜"),
            ("watch_date", "2026-02-30", "관람 날짜"),
            ("start_time", "9:30", "시작 시각"),
            ("end_time", "24:00", "종료 시각"),
            ("end_time", "10:00", "종료 시각"),
            ("end_time", "09:59", "종료 시각"),
        )
        for field, value, message in invalid_values:
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, message):
                self.create(**{field: value})
        self.assertEqual(plans_repo.list_plans(), [])

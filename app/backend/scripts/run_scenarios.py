"""대표 요청을 합성 데이터 또는 실제 API로 실행하고 모드별 로그를 남긴다."""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

LOG_DIR = BACKEND_DIR.parent / "docs" / "logs_sample"
KEYS = (
    ("KOBIS_API_KEY", "KOBIS"),
    ("KAKAO_REST_API_KEY", "카카오"),
    ("TMDB_API_KEY", "TMDB"),
    ("GMS_KEY", "GMS"),
)
SCENARIOS = (
    (
        "01_first_pool",
        "S1 한 번에 충족",
        "이번 주 토요일 오후 3시 이후 강남에서 영화 보고 저녁 7시 전에 끝내고 싶어",
    ),
    (
        "02_replan",
        "S2 재계획",
        "같은 날 3시 이후에 시작해서 4시 35분 전에 끝나는 걸로",
    ),
    (
        "03_limit",
        "S3 상한 도달",
        "토요일 오후 3시 이후 시작해서 4시 전에 끝나는 영화",
    ),
    (
        "04_tool_failure",
        "S4 Tool 하나 실패",
        "토요일 3시 이후 마라도에서 영화 보고 7시 전에 끝내고 싶어",
    ),
    ("05_smalltalk", "S5 Tool 불필요", "안녕, 이거 어떻게 써?"),
    ("06_clarify", "S6 되묻기", "7시 전에 끝나는 영화 추천해 줘"),
)


def _payload(event: dict) -> dict:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _elapsed_seconds(events: list[dict]) -> float | None:
    if len(events) < 2:
        return None
    try:
        start = datetime.fromisoformat(events[0].get("ts"))
        end = datetime.fromisoformat(events[-1].get("ts"))
        return (end - start).total_seconds()
    except (TypeError, ValueError):
        return None


def _event_metrics(events: list[dict]) -> dict:
    status = None
    replan_count = 0
    tool_call_count = 0
    outcomes = Counter()
    for event in events:
        kind, payload = event.get("type"), _payload(event)
        if kind == "stage_end" and event.get("stage") == "respond":
            status = payload.get("status")
        elif kind == "replan":
            replan_count += 1
        elif kind == "tool_call":
            tool_call_count += 1
        elif kind == "tool_result":
            provider = payload.get("provider") or "미기록"
            ok = payload.get("ok")
            outcome = {True: "ok", False: "fail"}[ok] if isinstance(ok, bool) else "미기록"
            code = payload.get("code")
            if code:
                outcome = f"{outcome}/{code}"
            outcomes[f"{provider} {outcome}"] += 1
    return {
        "status": status,
        "replan_count": replan_count,
        "tool_call_count": tool_call_count,
        "elapsed_sec": _elapsed_seconds(events),
        "provider_outcomes": dict(outcomes),
    }


def _run_scenarios(mode: str, *, event_bus, run_options: dict, verify_fixture=None) -> int:
    from app.agent.detail_cache import DetailCache
    from app.agent.graph import run_agent
    from app.agent.memory import SessionMemory
    from app.events.bus import EventCollector

    memory = SessionMemory()
    cache = DetailCache()
    followup_session_id = uuid4().hex
    synthetic = mode == "fixture"
    output_dir = LOG_DIR / mode
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_column = " | 기대와 차이" if synthetic else ""
    summary = [
        "# 대표 시나리오 실행 요약",
        "",
        f"synthetic: {str(synthetic).lower()}",
        "",
        "| 시나리오 | synthetic | 상태 | 재계획 수 | Tool 호출 수 | 이벤트 경과(초) "
        f"| 제공자별 Tool 결과{fixture_column} | 실행 로그 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---" + (" | ---" if synthetic else "") + " | --- |",
    ]
    failures = 0

    for filename, scenario, message in SCENARIOS:
        is_followup_session = filename in {"01_first_pool", "02_replan"}
        session_id = followup_session_id if is_followup_session else uuid4().hex
        collector = EventCollector(session_id)
        collector.attach(event_bus)
        try:
            result = run_agent(
                session_id, message, event_bus=event_bus, memory=memory, detail_cache=cache, **run_options
            )
        finally:
            collector.detach()
        events = sorted(collector.events, key=lambda event: event["seq"])
        metrics = _event_metrics(events)
        log = {"synthetic": synthetic, "events": events}
        comparison_cell = ""
        if synthetic:
            comparison = verify_fixture(filename, session_id, result, events, metrics)
            differences = comparison["differences"]
            failures += bool(differences)
            log.update(scenario=scenario, message=message, answer=result.get("answer"), **comparison)
            comparison_cell = f" | 불일치 ({', '.join(differences)})" if differences else " | 일치"

        log_name = f"{filename}.json"
        (output_dir / log_name).write_text(
            json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        status = metrics["status"] or "미기록"
        replans, tool_calls = metrics["replan_count"], metrics["tool_call_count"]
        elapsed = metrics["elapsed_sec"]
        elapsed_text = "미기록" if elapsed is None else f"{elapsed:.3f}"
        providers = ", ".join(f"{outcome} {count}건" for outcome, count in metrics["provider_outcomes"].items())
        print(f"{scenario} | 상태 {status} | 재계획 {replans} | Tool 호출 {tool_calls}{comparison_cell}")
        summary.append(
            f"| {scenario} | {str(synthetic).lower()} | {status} | {replans} | {tool_calls} | {elapsed_text} "
            f"| {providers or '없음'}{comparison_cell} | [{log_name}]({log_name}) |"
        )

    (output_dir / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return int(failures > 0)


def _run_fixture() -> int:
    with TemporaryDirectory(prefix="movie-scenarios-") as temp_dir, patch.dict(os.environ, {
        "PYTHON_DOTENV_DISABLED": "1",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
        "AD_MINUTES": "10",
        "REPLAN_LIMIT": "3",
        "PLANNER_DB_PATH": str(Path(temp_dir) / "fixture.db"),
    }):
        from app.events.bus import EventBus
        from app.logging_agent.agent import LoggingAgent
        from app.store import db, logs_repo
        from scripts.fixtures.scenario_data import TODAY, FixtureLLM, compare, run_fixture_tool

        db.init_db()
        event_bus = EventBus()
        logger = LoggingAgent()
        session_events: dict[str, list[dict]] = {}

        def verify(filename, session_id, result, events, metrics):
            published = session_events.setdefault(session_id, [])
            published.extend(events)
            stored = logs_repo.get_session_events(session_id)
            return compare(filename, result, events, metrics, published == stored)

        logger.start(event_bus)
        try:
            return _run_scenarios(
                "fixture",
                event_bus=event_bus,
                run_options={
                    "llm": FixtureLLM({filename: message for filename, _, message in SCENARIOS}),
                    "tool_runner": run_fixture_tool,
                    "today": TODAY,
                },
                verify_fixture=verify,
            )
        finally:
            logger.stop()


def _run_live() -> int:
    from langsmith import tracing_context
    from langsmith.utils import get_env_var

    from app import config
    from app.events.bus import EventBus

    missing = [label for name, label in KEYS if not config.has_key(name)]
    if missing:
        print(", ".join(missing))
        return 2

    # 병렬 상세 조회 스레드는 호출자의 추적 컨텍스트를 상속하지 않는다.
    try:
        with patch.dict(os.environ, {
            "LANGSMITH_TRACING": "false",
            "LANGSMITH_TRACING_V2": "false",
            "LANGCHAIN_TRACING_V2": "false",
        }), tracing_context(enabled=False, parent=False):
            get_env_var.cache_clear()
            return _run_scenarios("live", event_bus=EventBus(), run_options={})
    finally:
        get_env_var.cache_clear()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("fixture", "live"))
    mode = parser.parse_args().mode
    return _run_fixture() if mode == "fixture" else _run_live()


if __name__ == "__main__":
    sys.exit(main())

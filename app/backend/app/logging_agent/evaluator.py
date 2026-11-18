"""실행 이벤트만으로 이상 징후를 판정한다."""


# 기존 replan 이벤트의 next_pool은 화면에 표시하는 이름이다.
_POOL_TOOLS = {
    "일별 박스오피스": "get_boxoffice",
    "주간 박스오피스": "get_boxoffice",
    "TMDB 현재 상영작 1페이지": "get_now_playing",
    "TMDB 현재 상영작 2페이지": "get_now_playing",
}


def split_runs(events: list[dict]) -> list[list[dict]]:
    runs = []
    current = []
    for event in events:
        if current and event["type"] == "stage_start" and event["stage"] == "analyze":
            runs.append(current)
            current = []
        current.append(event)
    if current:
        runs.append(current)
    return runs


def _plan_tools(event: dict) -> set[str] | None:
    plan = event["payload"].get("plan")
    if not isinstance(plan, list):
        return None
    tools = set()
    for step in plan:
        if not isinstance(step, dict) or not isinstance(step.get("tool"), str) or not step["tool"]:
            return None
        tools.add(step["tool"])
    return tools


def _finding(rule: str, level: str, evidence: list[int], detail: str) -> dict:
    return {"rule": rule, "level": level, "evidence_seqs": evidence, "detail": detail}


def evaluate_run(events: list[dict], replan_limit: int, *, run_index: int = 1) -> dict:
    event_kinds = {(event["type"], event["stage"]) for event in events}
    plan_end = next(
        (event for event in events if (event["type"], event["stage"]) == ("stage_end", "plan")), None,
    )
    planned_tools = None if plan_end is None else _plan_tools(plan_end)
    calls = [event for event in events if event["type"] == "tool_call"]
    results = {
        event["payload"].get("call_id") for event in events if event["type"] == "tool_result"
    }
    replans = [event for event in events if event["type"] == "replan"]
    missing = []
    if ("stage_start", "analyze") not in event_kinds:
        missing.append("요청 분석 시작 이벤트가 없습니다.")
    if ("stage_end", "respond") not in event_kinds:
        missing.append("응답 생성 종료 이벤트가 없습니다.")
    if planned_tools is None:
        missing.append("계획 Tool을 확인할 수 있는 작업 계획 종료 이벤트가 없습니다.")
    elif planned_tools and not calls:
        missing.append("계획에 대응하는 Tool 호출 이벤트가 없습니다.")
    for call in calls:
        call_id = call["payload"].get("call_id")
        if not call_id or call_id not in results:
            missing.append(f"이벤트 {call['seq']}의 Tool 호출에 대응하는 결과 이벤트가 없습니다.")

    allowed_tools = set(planned_tools or ())
    replan_counts = {event["payload"].get("replan_count") for event in replans}
    for event in replans:
        pool_tool = _POOL_TOOLS.get(event["payload"].get("next_pool"))
        if pool_tool:
            allowed_tools.add(pool_tool)
    for event in events:
        if (
            (event["type"], event["stage"]) == ("stage_end", "replan")
            and event["payload"].get("replan_count") in replan_counts
        ):
            allowed_tools.update(_plan_tools(event) or ())

    findings = []
    call_seqs = {}
    for call in calls:
        payload = call["payload"]
        tool = payload.get("tool") or "Tool"
        duplicate_of = payload.get("duplicate_of")
        if duplicate_of:
            evidence = [call_seqs[duplicate_of], call["seq"]] if duplicate_of in call_seqs else [call["seq"]]
            findings.append(_finding(
                "repeated_call", "anomaly", evidence, f"{tool} 호출이 같은 입력으로 반복됐습니다.",
            ))
        if planned_tools is not None and tool not in allowed_tools:
            findings.append(_finding(
                "unplanned_call", "anomaly", [call["seq"]], f"{tool} 호출이 계획에 없었습니다.",
            ))
        call_seqs[payload.get("call_id")] = call["seq"]

    replan_count = len(replans)
    if replan_count >= replan_limit:
        if replan_count == replan_limit:
            level = "warning"
            detail = f"재계획 {replan_count}회로 상한에 도달했습니다."
        else:
            level = "anomaly"
            detail = f"재계획 {replan_count}회로 상한 {replan_limit}회를 초과했습니다."
        findings.append(_finding(
            "excessive_replan", level, [event["seq"] for event in replans], detail,
        ))

    verdict = "normal"
    if missing:
        verdict = "undetermined"
    elif any(finding["level"] == "anomaly" for finding in findings):
        verdict = "anomaly"
    return {
        "run_index": run_index,
        "first_seq": events[0]["seq"] if events else None,
        "last_seq": events[-1]["seq"] if events else None,
        "verdict": verdict,
        "warning_count": sum(finding["level"] == "warning" for finding in findings),
        "findings": findings,
        "missing_evidence": missing,
    }

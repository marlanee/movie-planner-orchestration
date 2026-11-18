"""이상 판정에 한해 GMS로 실행 흐름을 진단한다."""

import json
import re

from .. import config


_NUMBERED_ITEM_PREFIX = re.compile(
    r"(?:^[ \t]*|(?<=[.!?。？！])[ \t]+)[0-9]+\.[ \t]+", re.MULTILINE,
)
_SENTENCE_BOUNDARY = re.compile(
    r"(?<=[.!?。？！])\s+(?=\S)|(?<=[!?。？！])(?=[^\s!?。？！])"
    r"|(?<=\.)(?=[가-힣])|(?<=[가-힣]\.)(?=[A-Za-z])|\n+"
)


def _event_context(event: dict) -> dict:
    # 과거 이벤트에도 입력과 자유문장이 있을 수 있어 실행 구조만 전달한다.
    context = {key: event[key] for key in ("seq", "type", "stage")}
    payload = event["payload"]
    if event["type"] in ("tool_call", "tool_result"):
        for key in ("tool", "provider", "ok", "code"):
            if key in payload:
                context[key] = payload[key]
    if event["type"] in ("replan", "stage_end") and isinstance(payload.get("plan"), list):
        context["tools"] = [
            step["tool"] for step in payload["plan"] if isinstance(step, dict) and "tool" in step
        ]
    if event["type"] == "replan" and isinstance(payload.get("replan_count"), int):
        context["replan_count"] = payload["replan_count"]
    return context


def diagnose(report: dict, events: list[dict], llm=None) -> dict:
    if report["verdict"] != "anomaly":
        return {"status": "skipped", "text": None}
    try:
        from langsmith import tracing_context

        with tracing_context(enabled=False, parent=False):
            if llm is None:
                key = config.get_key("GMS_KEY")
                if key is None:
                    return {"status": "failed", "text": None}
                from langchain_openai import ChatOpenAI

                llm = ChatOpenAI(
                    model=config.GMS_MODEL, api_key=key, base_url=config.GMS_BASE_URL,
                    temperature=0, timeout=20, max_retries=0,
                )
            evidence = {"events": [_event_context(event) for event in events], "findings": report["findings"]}
            response = llm.invoke([
                ("system", "영화 관람 플래너의 이상 원인을 목록이나 제목 없이 한국어 3문장 이내로 진단하세요. "
                 "어느 Agent의 어느 단계, Tool, 또는 로깅에서 문제가 생겼고 무엇을 고치면 되는지 "
                 "이벤트와 판정 근거만 사용해 설명하세요. 호출 결과나 입력값을 추정하지 마세요."),
                ("human", json.dumps(evidence, ensure_ascii=False)),
            ])
            content = _NUMBERED_ITEM_PREFIX.sub(" ", response.content)
            sentences = [sentence.strip() for sentence in _SENTENCE_BOUNDARY.split(content) if sentence.strip()]
            if sentences:
                return {"status": "ok", "text": " ".join(sentences[:3])}
    except Exception:
        return {"status": "failed", "text": None}
    return {"status": "failed", "text": None}

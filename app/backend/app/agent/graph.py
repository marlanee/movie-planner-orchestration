from datetime import date

from langgraph.graph import END, START, StateGraph

from app.agent.detail_cache import DetailCache, default_detail_cache
from app.agent.memory import default_memory
from app.agent.nodes import ERROR_ANSWER, AgentNodes, RunState, make_result
from app.agent.state import AgentState
from app.config import today_kst
from app.events.bus import bus
from app.store.anomaly_repo import latest_verdict
from app.tools.registry import run_tool


def _after_analysis(state: AgentState) -> str:
    return "respond" if state.get("status") in ("config_error", "error") else "plan"


def _after_plan(state: AgentState) -> str:
    return "execute" if state["plan"] else "respond"


def _after_execution(state: AgentState) -> str:
    if state.get("status") != "config_error" and state["analysis"].intent == "plan_movie":
        return "judge"
    return "respond"


def _after_judgment(state: AgentState) -> str:
    return state["next"]


def build_graph(
    llm=None, tool_runner=run_tool, event_bus=bus, memory=default_memory, *,
    detail_cache: DetailCache = default_detail_cache,
    read_previous_verdict=latest_verdict,
):
    nodes = AgentNodes(
        llm, tool_runner, event_bus, memory, detail_cache=detail_cache,
        read_previous_verdict=read_previous_verdict,
    )
    graph = StateGraph(RunState)
    graph.add_node("analyze", nodes.staged("analyze", nodes.analyze_node))
    graph.add_node("plan", nodes.staged("plan", nodes.plan_node))
    graph.add_node("execute", nodes.staged("execute", nodes.execute_node))
    graph.add_node("judge", nodes.staged("judge", nodes.judge_node))
    graph.add_node("replan", nodes.staged("replan", nodes.replan_node))
    graph.add_node("respond", nodes.staged("respond", nodes.respond_node))
    graph.add_edge(START, "analyze")
    graph.add_conditional_edges("analyze", _after_analysis, ["plan", "respond"])
    graph.add_conditional_edges("plan", _after_plan, ["execute", "respond"])
    graph.add_conditional_edges("execute", _after_execution, ["judge", "respond"])
    graph.add_conditional_edges("judge", _after_judgment, ["replan", "respond"])
    graph.add_edge("replan", "execute")
    graph.add_edge("respond", END)
    return graph.compile()


def run_agent(
    session_id: str, message: str, *, llm=None, tool_runner=None,
    event_bus=None, memory=None, today: date | None = None,
    detail_cache: DetailCache = default_detail_cache,
    read_previous_verdict=latest_verdict,
) -> dict:
    selected_bus = bus if event_bus is None else event_bus
    try:
        if not isinstance(session_id, str) or not isinstance(message, str):
            raise TypeError("session_id and message must be strings")
        graph = build_graph(
            llm=llm,
            tool_runner=run_tool if tool_runner is None else tool_runner,
            event_bus=selected_bus,
            memory=default_memory if memory is None else memory,
            detail_cache=detail_cache,
            read_previous_verdict=read_previous_verdict,
        )
        initial: AgentState = {
            "session_id": session_id, "message": message, "today": today_kst() if today is None else today,
        }
        return graph.invoke(initial, config={"recursion_limit": 50})["result"]
    except Exception as error:
        try:
            event_session = session_id if isinstance(session_id, str) else ""
            selected_bus.publish(event_session, "error", "agent", {"kind": type(error).__name__})
        except Exception:
            # 이벤트 발행에 실패해도 사용자에게는 구조화된 오류 결과를 반환한다.
            pass
        return make_result({}, "error", ERROR_ANSWER)

from datetime import date
from typing import Literal, TypedDict

from pydantic import BaseModel


class Conditions(BaseModel):
    date: str | None = None
    start_after: str | None = None
    end_by: str | None = None
    region: str | None = None
    genre: str | None = None
    movie_title: str | None = None


class Analysis(BaseModel):
    intent: Literal["plan_movie", "fact_query", "smalltalk"]
    reason: str
    conditions: Conditions
    fact_topic: Literal["boxoffice", "runtime", "theater", "other"] | None = None


Status = Literal["ok", "clarify", "limit", "config_error", "error", "smalltalk", "fact"]
LimitReason = Literal["title_not_found", "time", "genre", "mixed"]


class AgentState(TypedDict, total=False):
    session_id: str
    message: str
    today: date
    analysis: Analysis
    conditions: dict
    date_defaulted: bool
    plan: list[dict]
    pool_index: int
    replan_count: int
    candidates: list[dict]
    title_matches: int
    seen_ids: set[str]
    seen_titles: set[str]
    details_cache: dict[str, dict]
    tool_results: list[dict]
    failures: list[dict]
    evaluations: list[dict]
    feasible: list[dict]
    excluded: list[dict]
    theaters: dict | None
    estimated_note: str | None
    status: Status
    limit_reason: LimitReason
    answer: str
    next: Literal["replan", "respond"]
    result: dict

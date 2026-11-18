import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints, field_validator, model_validator


class ChatRequest(BaseModel):
    session_id: Annotated[str, StringConstraints(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$",
    )] | None = None
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("메시지를 입력해 주세요.")
        return value


class TraceEvent(BaseModel):
    session_id: str
    seq: int
    ts: str
    type: str
    stage: str | None = None
    payload: dict
    duration_ms: int | None = None


class PlanCandidate(BaseModel):
    movie_nm: str | None = None
    tmdb_id: int | None = None
    watch_date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    theater_name: str | None = None
    theater_address: str | None = None


class KeyStatus(BaseModel):
    kobis: bool
    kakao: bool
    tmdb: bool
    gms: bool


class ChatConfig(BaseModel):
    keys: KeyStatus
    missing_keys: list[str]


class ChatResponse(BaseModel):
    session_id: str
    status: Literal["ok", "clarify", "limit", "config_error", "error", "smalltalk", "fact"]
    answer: str
    recommendations: list[dict] = Field(default_factory=list)
    excluded: list[dict] = Field(default_factory=list)
    theaters: dict | None = None
    criteria: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)
    replan_count: int = 0
    limit_reason: Literal["time", "genre", "mixed", "title_not_found"] | None = None
    estimated_note: str | None = None
    conditions: dict = Field(default_factory=dict)
    plan_candidates: list[PlanCandidate] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    missing_keys: list[str] = Field(default_factory=list)

    @field_validator("evidence")
    @classmethod
    def hide_failure_details(cls, entries: list[dict]) -> list[dict]:
        for index, entry in enumerate(entries):
            if entry.get("ok") is False:
                entries[index] = {
                    "tool": entry.get("tool"), "args": entry.get("args", {}),
                    "ok": False, "code": entry.get("code"), "summary": "조회 실패",
                }
        return entries


def require_date_string(value: object) -> object:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("관람 날짜는 YYYY-MM-DD 형식이어야 합니다.")
    return value


WatchDate = Annotated[date, BeforeValidator(require_date_string)]
ClockTime = Annotated[str, StringConstraints(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
MovieName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PlanFields(BaseModel):
    tmdb_id: int | None = None
    theater_name: str | None = None
    theater_address: str | None = None
    memo: str | None = None
    session_id: str | None = None


class PlanIn(PlanFields):
    movie_nm: MovieName
    watch_date: WatchDate
    start_time: ClockTime
    end_time: ClockTime


class PlanUpdate(PlanFields):
    movie_nm: MovieName | None = None
    watch_date: WatchDate | None = None
    start_time: ClockTime | None = None
    end_time: ClockTime | None = None

    @model_validator(mode="after")
    def require_change(self) -> "PlanUpdate":
        if not self.model_fields_set:
            raise ValueError("수정할 항목을 하나 이상 입력해 주세요.")
        return self


class PlanOut(PlanFields):
    plan_id: int
    movie_nm: str
    watch_date: str
    start_time: str
    end_time: str
    created_at: str
    updated_at: str | None


class PlanSaveResult(BaseModel):
    plan: PlanOut
    overlaps: list[PlanOut]
    warning: str | None


class LogSession(BaseModel):
    session_id: str
    first_ts: str
    last_ts: str
    event_count: int
    replan_count: int
    error_count: int


class LogDetail(BaseModel):
    session_id: str
    events: list[TraceEvent]


class AnomalyFinding(BaseModel):
    rule: Literal["repeated_call", "excessive_replan", "unplanned_call"]
    level: Literal["warning", "anomaly"]
    evidence_seqs: list[int]
    detail: str


class AnomalyDiagnosis(BaseModel):
    status: Literal["ok", "failed", "skipped"]
    text: str | None


class AnomalyReport(BaseModel):
    run_index: int
    first_seq: int
    last_seq: int
    verdict: Literal["normal", "anomaly", "undetermined"]
    warning_count: int
    findings: list[AnomalyFinding]
    missing_evidence: list[str]
    created_at: str
    diagnosis: AnomalyDiagnosis | None

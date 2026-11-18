import re
from datetime import date, timedelta

from langchain_openai import ChatOpenAI

from app import config
from app.agent.state import Analysis, Conditions


_WEEKDAYS_KO = "월화수목금토일"
_RELATIVE_DATE_OFFSETS = {"오늘": 0, "내일": 1, "모레": 2}
_DATE_KO = re.compile(
    r"(?P<relative>오늘|내일|모레)"
    rf"|(?:(?P<week>이번|다음)\s*주\s*)?(?P<weekday>[{_WEEKDAYS_KO}])요일"
    r"|(?<!\d)(?P<month>\d{1,2})\s*월\s*(?P<day>\d{1,2})\s*일"
)
_AMBIGUOUS_DATE_KO = re.compile(r"말고|아니고|대신|지난|저번|다다음|내일모레")
_CLOCK_TIME_KO = re.compile(
    r"(?:(오전|아침|새벽|오후|저녁|밤)\s*)?(?<!\d)(\d{1,2})\s*시"
    r"(?:\s*(?:(\d{1,2})\s*분|(반)))?"
)
_TIME_CONDITION_KO = re.compile(
    r"(?P<gap>(?:\s|에서|으로|보다|[에은는이가을를도만의로])*?)"
    r"(?:(?P<start_after>이후|부터|넘어서|지나서|후에)|(?P<end_by>전에|전까지|까지|이전에|안에))"
)


class MissingKeyError(RuntimeError):
    """GMS 요청에 필요한 키가 비어 있다."""


def extract_date_ko(message: str, today: date) -> str | None:
    matches = list(_DATE_KO.finditer(message))
    if len(matches) != 1 or _AMBIGUOUS_DATE_KO.search(message):
        return None

    match = matches[0]
    relative = match.group("relative")
    if relative is not None:
        return (today + timedelta(days=_RELATIVE_DATE_OFFSETS[relative])).isoformat()

    weekday = match.group("weekday")
    if weekday is not None:
        days_until = _WEEKDAYS_KO.index(weekday) - today.weekday()
        week = match.group("week")
        if week == "이번" and days_until < 0:
            return None
        if week == "다음" or days_until < 0:
            days_until += 7
        return (today + timedelta(days=days_until)).isoformat()

    try:
        selected_date = date(today.year, int(match.group("month")), int(match.group("day")))
        if selected_date < today:
            selected_date = selected_date.replace(year=today.year + 1)
    except ValueError:
        return None
    return selected_date.isoformat()


def extract_times_ko(message: str) -> dict[str, str]:
    times: dict[str, str] = {}
    for clock in _CLOCK_TIME_KO.finditer(message):
        condition = _TIME_CONDITION_KO.match(message, clock.end())
        if condition is None:
            continue
        field = "start_after" if condition.group("start_after") else "end_by"
        if field == "start_after" and len(condition.group("gap")) > 6:
            continue

        period, hours, minutes, half = clock.groups()
        hour = int(hours)
        minute = 30 if half else int(minutes or 0)
        if hour > 23 or minute > 59:
            continue
        if (period in ("밤", "저녁") and hour == 12) or (period in ("밤", "새벽") and 1 <= hour <= 5):
            continue
        if minutes is not None and minute <= 20 and condition.group("end_by") in ("전에", "전까지"):
            continue
        if period in ("오전", "아침", "새벽"):
            if hour == 12:
                hour = 0
        elif 1 <= hour <= 11:
            hour += 12
        times.setdefault(field, f"{hour:02d}:{minute:02d}")
    return times


def normalize_conditions(conditions: Conditions) -> Conditions:
    values = conditions.model_dump()
    selected_date = values["date"]
    if selected_date is not None:
        try:
            if date.fromisoformat(selected_date).isoformat() != selected_date:
                values["date"] = None
        except ValueError:
            values["date"] = None
    for field in ("start_after", "end_by"):
        value = values[field]
        if value is not None and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            values[field] = None
    return Conditions(**values)


def analyze(
    message: str, today: date, previous: Conditions | None, llm=None
) -> Analysis:
    analysis, _ = analyze_with_time_overrides(message, today, previous, llm)
    return analysis


def analyze_with_time_overrides(
    message: str, today: date, previous: Conditions | None, llm=None
) -> tuple[Analysis, dict[str, dict[str, str | None]]]:
    if llm is None:
        key = config.get_key("GMS_KEY")
        if key is None:
            raise MissingKeyError("GMS_KEY")
        llm = ChatOpenAI(
            model=config.GMS_MODEL, api_key=key, base_url=config.GMS_BASE_URL, temperature=0
        )

    weekday = _WEEKDAYS_KO[today.weekday()]
    previous_json = previous.model_dump_json() if previous else "없음"
    prompt = f"""영화 관람 플래너의 요청을 분석하세요.
오늘은 한국 표준시로 {today.isoformat()} ({weekday}요일)입니다.
intent는 관람을 계획하는 plan_movie, 순위·러닝타임·영화관 위치 등 사실을 묻는
fact_query, 인사·사용법·그 밖의 요청인 smalltalk 중 하나입니다.
reason은 의도 판단 이유를 한 문장으로 작성하세요.
fact_query는 fact_topic에 boxoffice, runtime, theater, other 중 하나를 쓰고, 그 외에는 null을 쓰세요.
조건은 date(관람 날짜 YYYY-MM-DD), start_after(이 시각 이후 시작 HH:MM),
end_by(이 시각까지 종료 HH:MM), region(지역), genre(선호 장르), movie_title(영화 제목)입니다.
토요일 등 상대 날짜는 위의 오늘 날짜를 기준으로 절대 날짜로 바꾸세요.
오후 3시 또는 3시 이후는 15:00입니다. 영화 관람 맥락에서 오전·오후가 생략된 1~11시는 오후로 해석하세요.
말하지 않은 조건은 null로 두고 추측해서 채우지 마세요.
직전 조건은 참고만 하고 이번 메시지에서 새로 주거나 바꾼 값만 채우세요.
직전 조건 데이터: {previous_json}"""
    analysis = Analysis.model_validate(
        llm.with_structured_output(Analysis).invoke([("system", prompt), ("human", message)])
    )
    conditions = normalize_conditions(analysis.conditions)
    rule_values = extract_times_ko(message)
    rule_date = extract_date_ko(message, today)
    if rule_date is not None:
        rule_values["date"] = rule_date
    overrides = {}
    for field, rule_value in rule_values.items():
        llm_value = getattr(conditions, field)
        if llm_value != rule_value:
            overrides[field] = {"llm": llm_value, "rule": rule_value}
    conditions = conditions.model_copy(update=rule_values)
    return analysis.model_copy(update={"conditions": conditions}), overrides

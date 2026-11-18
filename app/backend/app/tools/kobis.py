from datetime import date, timedelta

from app.config import get_key, today_kst
from app.tools.base import (
    BAD_ARGUMENT,
    INVALID_KEY,
    NOT_FOUND,
    NO_DATA,
    NO_RUNTIME,
    UPSTREAM_ERROR,
    ToolResult,
    UpstreamError,
    as_object,
    as_text,
    fail,
    http_get_json,
    object_list,
    ok,
    positive_int,
)

SOURCE = "KOBIS 영화진흥위원회 오픈API"
BASE_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest"


def _request(path: str, params: dict[str, str]) -> ToolResult:
    key = get_key("KOBIS_API_KEY")
    if key is None:
        return fail(INVALID_KEY, "KOBIS_API_KEY가 설정되지 않았습니다.")

    try:
        status, body = http_get_json(f"{BASE_URL}/{path}", params={"key": key, **params})
    except UpstreamError:
        return fail(UPSTREAM_ERROR, "KOBIS API 응답을 읽지 못했습니다.")

    if not isinstance(body, dict):
        return fail(UPSTREAM_ERROR, "KOBIS API 응답 형식이 올바르지 않습니다.")
    if "faultInfo" in body:
        fault = as_object(body["faultInfo"])
        message = as_text(fault.get("message")) or ""
        if str(fault.get("errorCode")) in ("320010", "320011") or "키" in message:
            return fail(INVALID_KEY, "KOBIS API 키가 유효하지 않습니다.")
        return fail(UPSTREAM_ERROR, "KOBIS API에서 오류를 반환했습니다.")
    if status >= 400:
        return fail(UPSTREAM_ERROR, "KOBIS API에서 오류를 반환했습니다.")
    return ok(body)


def _iso_date(value) -> str | None:
    text = as_text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _week_range(week_end: date, show_range: object) -> tuple[str, str]:
    if isinstance(show_range, str):
        start, _, end = show_range.partition("~")
        if len(start) == len(end) == 8 and start.isdigit() and end.isdigit():
            first, last = _iso_date(start), _iso_date(end)
            if first is not None and last is not None and first <= last:
                return first, last
    return (week_end - timedelta(days=6)).isoformat(), week_end.isoformat()


def get_boxoffice(
    target_date: str | None = None, period: str = "daily", top_n: int = 10
) -> ToolResult:
    if period not in ("daily", "weekly"):
        return fail(BAD_ARGUMENT, "period는 daily 또는 weekly여야 합니다.")
    if type(top_n) is not int or not 1 <= top_n <= 10:
        return fail(BAD_ARGUMENT, "top_n은 1부터 10까지의 정수여야 합니다.")

    today = today_kst()
    if target_date is None:
        requested = today
    elif isinstance(target_date, str) and _iso_date(target_date) == target_date:
        requested = date.fromisoformat(target_date)
    else:
        return fail(BAD_ARGUMENT, "target_date는 YYYY-MM-DD 형식이어야 합니다.")

    params = {}
    if period == "weekly":
        last_sunday = today - timedelta(days=today.weekday() + 1)
        estimated = requested > last_sunday
        queried = last_sunday if estimated else requested + timedelta(days=6 - requested.weekday())
        params["weekGb"] = "0"
    else:
        queried = min(requested, today - timedelta(days=1))
        estimated = requested != queried
    params["targetDt"] = queried.isoformat().replace("-", "")

    period_name = period.capitalize()
    response = _request(f"boxoffice/search{period_name}BoxOfficeList.json", params)
    if not response["ok"]:
        return response
    result = as_object(response["data"].get("boxOfficeResult"))
    entries = object_list(result.get(f"{period}BoxOfficeList"))
    if not entries:
        return fail(NO_DATA, "조회일의 박스오피스 집계가 없습니다.")

    week_range = None
    note = None
    if period == "weekly":
        start, end = _week_range(queried, result.get("showRange"))
        week_range = {"start": start, "end": end}
        if estimated:
            note = f"요청일 {requested}이 속한 주는 아직 집계가 없어 {start}~{end} 주간 집계로 추정했습니다."
    elif estimated:
        note = f"요청일 {requested}의 박스오피스는 아직 없어 {queried} 기준 집계로 추정했습니다."
    return ok({
        "period": period,
        "requested_date": requested.isoformat(),
        "queried_date": queried.isoformat(),
        "estimated": estimated,
        "note": note,
        "week_range": week_range,
        "movies": [
            {
                "movie_cd": as_text(entry.get("movieCd")),
                "movie_nm": as_text(entry.get("movieNm")),
                "rank": positive_int(entry.get("rank")),
                "open_dt": _iso_date(entry.get("openDt")),
            }
            for entry in entries[:top_n]
        ],
        "source": SOURCE,
    })


def get_movie_detail_kobis(movie_cd: str) -> ToolResult:
    code = as_text(movie_cd)
    if code is None:
        return fail(BAD_ARGUMENT, "movie_cd를 입력해 주세요.")

    response = _request("movie/searchMovieInfo.json", {"movieCd": code})
    if not response["ok"]:
        return response
    result = as_object(response["data"].get("movieInfoResult"))
    movie = as_object(result.get("movieInfo"))
    name = as_text(movie.get("movieNm"))
    if name is None:
        return fail(NOT_FOUND, "KOBIS에서 영화 상세 정보를 찾지 못했습니다.")
    runtime = positive_int(movie.get("showTm"))
    if runtime is None:
        return fail(NO_RUNTIME, f"'{name}'의 러닝타임 정보가 없어 판단에서 제외합니다.")

    audits = movie.get("audits")
    first_audit = as_object(audits[0]) if isinstance(audits, list) and audits else {}
    genres = object_list(movie.get("genres"))
    return ok({
        "id_type": "kobis",
        "movie_cd": as_text(movie.get("movieCd")) or code,
        "tmdb_id": None,
        "movie_nm": name,
        "runtime_min": runtime,
        "watch_grade": as_text(first_audit.get("watchGradeNm")),
        "genres": [genre for item in genres if (genre := as_text(item.get("genreNm")))],
        "open_dt": _iso_date(movie.get("openDt")),
        "source": SOURCE,
    })

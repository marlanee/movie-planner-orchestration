from app.config import get_key
from app.tools.base import (
    BAD_ARGUMENT,
    INVALID_KEY,
    NOT_FOUND,
    NO_DATA,
    NO_RUNTIME,
    UPSTREAM_ERROR,
    ToolResult,
    UpstreamError,
    as_text,
    fail,
    http_get_json,
    object_list,
    ok,
    positive_int,
)

SOURCE = "TMDB (The Movie Database) API"
BASE_URL = "https://api.themoviedb.org/3"


def _request(path: str, **query: str | int) -> ToolResult:
    key = get_key("TMDB_API_KEY")
    if key is None:
        return fail(INVALID_KEY, "TMDB_API_KEY가 설정되지 않았습니다.")

    headers = {}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    else:
        query["api_key"] = key

    try:
        status, body = http_get_json(f"{BASE_URL}/{path}", params=query, headers=headers)
    except UpstreamError:
        return fail(UPSTREAM_ERROR, "TMDB API 응답을 읽지 못했습니다.")
    if status == 401:
        return fail(INVALID_KEY, "TMDB API 키가 유효하지 않습니다.")
    if status == 404:
        return fail(NOT_FOUND, "TMDB에서 요청한 정보를 찾지 못했습니다.")
    if status >= 400 or not isinstance(body, dict):
        return fail(UPSTREAM_ERROR, "TMDB API에서 올바른 응답을 받지 못했습니다.")
    return ok(body)


def get_now_playing(page: int = 1) -> ToolResult:
    if type(page) is not int or not 1 <= page <= 5:
        return fail(BAD_ARGUMENT, "page는 1부터 5까지의 정수여야 합니다.")

    response = _request("movie/now_playing", language="ko-KR", region="KR", page=page)
    if not response["ok"]:
        return response
    body = response["data"]
    entries = object_list(body.get("results"))
    if not entries:
        return fail(NO_DATA, "현재 상영작 목록이 비어 있습니다.")

    return ok({
        "page": page,
        "total_pages": positive_int(body.get("total_pages")),
        "movies": [
            {
                "tmdb_id": positive_int(entry.get("id")),
                "title": as_text(entry.get("title")),
                "release_date": as_text(entry.get("release_date")),
            }
            for entry in entries
        ],
        "source": SOURCE,
    })


def get_movie_detail_tmdb(tmdb_id: int) -> ToolResult:
    if type(tmdb_id) is not int or tmdb_id <= 0:
        return fail(BAD_ARGUMENT, "tmdb_id는 양의 정수여야 합니다.")

    response = _request(f"movie/{tmdb_id}", language="ko-KR")
    if not response["ok"]:
        return response
    movie = response["data"]
    title = as_text(movie.get("title"))
    runtime = positive_int(movie.get("runtime"))
    if runtime is None:
        name = title or f"영화 ID {tmdb_id}"
        return fail(NO_RUNTIME, f"'{name}'의 러닝타임 정보가 없어 판단에서 제외합니다.")

    genres = object_list(movie.get("genres"))
    return ok({
        "id_type": "tmdb",
        "movie_cd": None,
        "tmdb_id": tmdb_id,
        "movie_nm": title,
        "runtime_min": runtime,
        "watch_grade": None,
        "genres": [genre for item in genres if (genre := as_text(item.get("name")))],
        "open_dt": as_text(movie.get("release_date")),
        "source": SOURCE,
    })

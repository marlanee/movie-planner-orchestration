from typing import Any

from app.tools import kakao, kobis, tmdb
from app.tools.base import (
    BAD_ARGUMENT,
    INVALID_KEY,
    NOT_FOUND,
    NO_DATA,
    NO_RESULT,
    NO_RUNTIME,
    UPSTREAM_ERROR,
    Tool,
    ToolKind,
    ToolResult,
    fail,
)


def get_movie_detail(movie_cd: str | None = None, tmdb_id: int | None = None) -> ToolResult:
    if isinstance(movie_cd, str) and not movie_cd.strip():
        movie_cd = None
    if tmdb_id == "":
        tmdb_id = None
    if movie_cd is not None and tmdb_id is None:
        return kobis.get_movie_detail_kobis(movie_cd)
    if tmdb_id is not None and movie_cd is None:
        return tmdb.get_movie_detail_tmdb(tmdb_id)
    return fail(BAD_ARGUMENT, "movie_cd와 tmdb_id 중 하나만 입력해 주세요.")


TOOLS: dict[str, Tool] = {
    "get_boxoffice": Tool(
        name="get_boxoffice",
        description="볼 영화를 아직 정하지 않아 특정 날짜의 인기 영화 후보가 필요할 때 부른다. 날짜가 없으면 오늘 기준이다.",
        parameters={
            "type": "object",
            "properties": {
                "target_date": {
                    "type": ["string", "null"],
                    "description": "영화를 볼 날짜(YYYY-MM-DD). 생략하거나 null이면 오늘(KST).",
                },
                "period": {
                    "type": "string",
                    "enum": ["daily", "weekly"],
                    "description": "집계 단위. 생략하면 일별(daily).",
                },
                "top_n": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "가져올 상위 영화 수. 생략하면 10개.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        func=kobis.get_boxoffice,
        source=kobis.SOURCE,
        output={
            "data": {
                "period": "str. 일별(daily) 또는 주간(weekly) 집계.",
                "requested_date": "str. 요청 날짜(YYYY-MM-DD).",
                "queried_date": "str. API에 보낸 날짜(YYYY-MM-DD).",
                "estimated": "bool. 요청한 집계가 없어 이전 집계를 사용했는지 여부.",
                "note": "str | None. 추정에 사용한 집계 안내.",
                "week_range": "dict | None. 주간 집계 시작일(start)과 종료일(end), 일별은 None.",
                "movies": "list[dict]. 영화 코드(movie_cd), 이름(movie_nm), 순위(rank), 개봉일(open_dt).",
                "source": "str. 박스오피스 출처.",
            },
            "errors": [BAD_ARGUMENT, INVALID_KEY, NO_DATA, UPSTREAM_ERROR],
        },
    ),
    "get_now_playing": Tool(
        name="get_now_playing",
        description="국내에서 현재 상영 중인 영화 후보를 찾거나 후보를 더 보고 싶을 때 부른다.",
        parameters={
            "type": "object",
            "properties": {
                "page": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "조회할 상영작 목록 페이지. 생략하면 1페이지.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        func=tmdb.get_now_playing,
        source=tmdb.SOURCE,
        output={
            "data": {
                "page": "int. 조회한 페이지 번호.",
                "total_pages": "int | None. 전체 페이지 수.",
                "movies": "list[dict]. TMDB 영화 ID(tmdb_id), 제목(title), 개봉일(release_date).",
                "source": "str. 상영작 목록 출처.",
            },
            "errors": [BAD_ARGUMENT, INVALID_KEY, NOT_FOUND, NO_DATA, UPSTREAM_ERROR],
        },
    ),
    "get_movie_detail": Tool(
        name="get_movie_detail",
        description="후보 영화가 일정에 맞는지 판단할 러닝타임이나 장르가 필요할 때 부른다. 후보를 찾은 출처의 영화 ID 하나를 사용한다.",
        parameters={
            "type": "object",
            "properties": {
                "movie_cd": {
                    "type": ["string", "null"],
                    "description": "박스오피스 결과의 KOBIS 영화 코드. tmdb_id와 함께 보내지 않는다.",
                },
                "tmdb_id": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "description": "현재 상영작 결과의 TMDB 영화 ID. movie_cd와 함께 보내지 않는다.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        func=get_movie_detail,
        source=f"{kobis.SOURCE}, {tmdb.SOURCE}",
        output={
            "data": {
                "id_type": "str. 조회에 사용한 식별자 종류(kobis 또는 tmdb).",
                "movie_cd": "str | None. KOBIS 영화 코드.",
                "tmdb_id": "int | None. TMDB 영화 ID.",
                "movie_nm": "str | None. 영화 제목.",
                "runtime_min": "int. 영화 러닝타임(분).",
                "watch_grade": "str | None. 관람 등급.",
                "genres": "list[str]. 장르 이름 목록.",
                "open_dt": "str | None. 개봉일(YYYY-MM-DD).",
                "source": "str. 영화 상세 정보 출처.",
            },
            "errors": [BAD_ARGUMENT, INVALID_KEY, NOT_FOUND, NO_RUNTIME, UPSTREAM_ERROR],
        },
    ),
    "search_theaters": Tool(
        name="search_theaters",
        description="관람할 지역을 정했고 주변 영화관의 위치나 연락처가 필요할 때 부른다.",
        parameters={
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "검색할 지역 이름. 예: 강남."},
                "keyword": {
                    "type": ["string", "null"],
                    "description": "찾을 영화관이나 브랜드 이름. 생략하면 영화관으로 검색한다.",
                },
                "size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 15,
                    "description": "검색할 장소 수. 생략하면 10개.",
                },
            },
            "required": ["region"],
            "additionalProperties": False,
        },
        func=kakao.search_theaters,
        source=kakao.SOURCE,
        output={
            "data": {
                "region": "str. 영화관을 검색한 지역.",
                "theaters": "list[dict]. 영화관 이름(name), 주소(address, road_address), URL(url), 전화번호(phone).",
                "note": "str. 영화관 검색 범위 안내.",
                "source": "str. 영화관 검색 출처.",
            },
            "errors": [BAD_ARGUMENT, INVALID_KEY, NO_RESULT, UPSTREAM_ERROR],
        },
    ),
}


def catalog(kind: ToolKind | None = None) -> list[dict[str, Any]]:
    return [
        {"name": tool.name, "description": tool.description, "parameters": tool.parameters, "kind": tool.kind}
        for tool in TOOLS.values()
        if kind is None or tool.kind == kind
    ]


def describe_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "kind": tool.kind,
            "description": tool.description,
            "parameters": tool.parameters,
            "output": tool.output,
            "source": tool.source,
        }
        for tool in TOOLS.values()
    ]


def run_tool(name: str, /, **kwargs: Any) -> ToolResult:
    tool = TOOLS.get(name) if isinstance(name, str) else None
    if tool is None:
        return fail(BAD_ARGUMENT, "등록되지 않은 Tool입니다.")
    return tool.run(**kwargs)


def to_openai_tools() -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {key: value for key, value in entry.items() if key != "kind"}}
        for entry in catalog()
    ]

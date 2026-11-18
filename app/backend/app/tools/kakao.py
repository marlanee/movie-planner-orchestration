from app.config import get_key
from app.tools.base import (
    BAD_ARGUMENT,
    INVALID_KEY,
    NO_RESULT,
    UPSTREAM_ERROR,
    ToolResult,
    UpstreamError,
    as_text,
    fail,
    http_get_json,
    object_list,
    ok,
)

SOURCE = "카카오 로컬 API"
SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


def search_theaters(region: str, keyword: str | None = None, size: int = 10) -> ToolResult:
    region = as_text(region)
    if region is None:
        return fail(BAD_ARGUMENT, "region을 입력해 주세요.")
    if keyword is not None and not isinstance(keyword, str):
        return fail(BAD_ARGUMENT, "keyword는 문자열이어야 합니다.")
    if type(size) is not int or not 1 <= size <= 15:
        return fail(BAD_ARGUMENT, "size는 1부터 15까지의 정수여야 합니다.")

    key = get_key("KAKAO_REST_API_KEY")
    if key is None:
        return fail(INVALID_KEY, "KAKAO_REST_API_KEY가 설정되지 않았습니다.")
    try:
        status, body = http_get_json(
            SEARCH_URL,
            headers={"Authorization": f"KakaoAK {key}"},
            params={
                "query": f"{region} {as_text(keyword) or '영화관'}",
                "category_group_code": "CT1",
                "size": size,
            },
        )
    except UpstreamError:
        return fail(UPSTREAM_ERROR, "카카오 로컬 API 응답을 읽지 못했습니다.")
    if status in (401, 403):
        return fail(INVALID_KEY, "카카오 로컬 API 키 또는 권한이 유효하지 않습니다.")
    if status >= 400 or not isinstance(body, dict):
        return fail(UPSTREAM_ERROR, "카카오 로컬 API에서 올바른 응답을 받지 못했습니다.")

    theaters = [
        {
            "name": as_text(place.get("place_name")),
            "address": as_text(place.get("address_name")),
            "road_address": as_text(place.get("road_address_name")),
            "url": as_text(place.get("place_url")),
            "phone": as_text(place.get("phone")),
        }
        for place in object_list(body.get("documents"))
        if "영화관" in (as_text(place.get("category_name")) or "")
    ]
    if not theaters:
        return fail(NO_RESULT, f"'{region}' 주변에서 영화관을 찾지 못했습니다.")

    return ok({
        "region": region,
        "theaters": theaters,
        "note": "영화관 검색 결과는 상영 영화 정보를 포함하지 않습니다.",
        "source": SOURCE,
    })

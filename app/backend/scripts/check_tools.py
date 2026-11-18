"""환경에 설정된 키로 영화 플래너 Tool의 실제 응답을 확인한다."""

import json
import os
import sys
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import has_key, today_kst
from app.tools.registry import catalog, describe_tools, run_tool


@contextmanager
def without_key(name):
    original = os.environ.pop(name, None)
    try:
        yield
    finally:
        if original is not None:
            os.environ[name] = original


def trial(label, name, **kwargs):
    result = run_tool(name, **kwargs)
    if result["ok"]:
        summary = result["data"].copy()
        for field in ("movies", "theaters"):
            if field in summary:
                summary[field] = summary[field][:3]
        output = json.dumps(summary, ensure_ascii=False)
    else:
        output = result["error"]["code"]
    print(f"{label} | {name} | ok={result['ok']} | {output}", flush=True)
    return result


def check_detail(label, result, id_field):
    if result["ok"]:
        first_id = result["data"]["movies"][0].get(id_field)
        if first_id is not None:
            trial(label, "get_movie_detail", **{id_field: first_id})
            return
    print(f"{label} | get_movie_detail | 미실행 | 선행 조회에서 영화 ID를 얻지 못했습니다.", flush=True)


def main():
    print(json.dumps(catalog(), ensure_ascii=False, indent=2), flush=True)
    outputs = {entry["name"]: entry["output"] for entry in describe_tools()}
    print(json.dumps(outputs, ensure_ascii=False, indent=2), flush=True)
    for name in ("KOBIS_API_KEY", "TMDB_API_KEY", "KAKAO_REST_API_KEY"):
        print(f"{name} 설정={has_key(name)}", flush=True)

    boxoffice = trial("정상(날짜 기본값)", "get_boxoffice")
    future = (today_kst() + timedelta(days=5)).isoformat()
    trial("미래 날짜(estimated 확인)", "get_boxoffice", target_date=future)
    trial("정상(주간)", "get_boxoffice", period="weekly")
    trial("잘못된 날짜", "get_boxoffice", target_date="2026-99-01")
    with without_key("KOBIS_API_KEY"):
        trial("키 없음", "get_boxoffice")
        trial("키 없음(KOBIS)", "get_movie_detail", movie_cd="20260001")

    now_playing = trial("정상", "get_now_playing")
    trial("잘못된 페이지", "get_now_playing", page=0)
    with without_key("TMDB_API_KEY"):
        trial("키 없음", "get_now_playing")
        trial("키 없음(TMDB)", "get_movie_detail", tmdb_id=1)

    check_detail("정상(KOBIS 연쇄 호출)", boxoffice, "movie_cd")
    check_detail("정상(TMDB 연쇄 호출)", now_playing, "tmdb_id")
    trial("식별자 없음", "get_movie_detail")
    trial("식별자 두 개", "get_movie_detail", movie_cd="20260001", tmdb_id=1)

    trial("정상", "search_theaters", region="강남")
    trial("빈 지역", "search_theaters", region="")
    trial("결과 없음", "search_theaters", region="zzqqxx없는지역")
    with without_key("KAKAO_REST_API_KEY"):
        trial("키 없음", "search_theaters", region="강남")


if __name__ == "__main__":
    main()

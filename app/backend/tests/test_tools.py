import os
import unittest
from datetime import date
from unittest.mock import Mock, patch
from uuid import uuid4

import requests

from app.tools.base import (
    BAD_ARGUMENT,
    INVALID_KEY,
    NOT_FOUND,
    NO_DATA,
    NO_RESULT,
    NO_RUNTIME,
    UPSTREAM_ERROR,
    Tool,
    fail,
)
from app.tools.registry import catalog, describe_tools, run_tool, to_openai_tools


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.key = uuid4().hex
        self.set_up_patch(patch.dict(os.environ, {
            "KOBIS_API_KEY": self.key,
            "TMDB_API_KEY": self.key,
            "KAKAO_REST_API_KEY": self.key,
        }))
        self.get = self.set_up_patch(patch("app.tools.base.requests.get"))
        self.set_up_patch(patch("app.tools.kobis.today_kst", return_value=date(2026, 11, 18)))

    def set_up_patch(self, patcher):
        self.addCleanup(patcher.stop)
        return patcher.start()

    def respond(self, body, status=200):
        self.get.return_value = Mock(status_code=status)
        self.get.return_value.json.return_value = body

    def assert_failure(self, result, code):
        self.assertEqual(set(result), {"ok", "error"})
        self.assertIs(result["ok"], False)
        self.assertEqual(set(result["error"]), {"code", "message"})
        self.assertEqual(result["error"]["code"], code)
        self.assertIsInstance(result["error"]["message"], str)
        self.assertNotIn(self.key, result["error"]["message"])
        self.assertNotIn("key=", result["error"]["message"])

    def assert_success(self, result, keys):
        self.assertEqual(set(result), {"ok", "data"})
        self.assertIs(result["ok"], True)
        self.assertEqual(set(result["data"]), set(keys))
        return result["data"]

    def respond_boxoffice(self, period="daily", **fields):
        self.respond({"boxOfficeResult": {**fields, f"{period}BoxOfficeList": [
            {"movieCd": "20260001", "movieNm": "영화 하나", "rank": "1", "openDt": "2026-11-10", "audiCnt": "100"},
            {"movieCd": "20260002", "movieNm": "영화 둘", "rank": "2", "openDt": ""},
        ]}})

    def respond_kobis_detail(self, runtime):
        self.respond({"movieInfoResult": {"movieInfo": {
            "movieCd": "20260001", "movieNm": "영화 하나", "showTm": runtime,
            "audits": [{"watchGradeNm": "12세이상관람가"}],
            "genres": [{"genreNm": "드라마"}, {"genreNm": ""}],
            "openDt": "20261110", "actors": [],
        }}})

    def assert_boxoffice_data(self, result):
        return self.assert_success(result, {
            "period", "requested_date", "queried_date", "estimated", "note", "week_range", "movies", "source",
        })

    def assert_detail_data(self, result):
        return self.assert_success(result, {
            "id_type", "movie_cd", "tmdb_id", "movie_nm", "runtime_min", "watch_grade", "genres", "open_dt", "source",
        })

    def test_catalog_and_openai_schema(self):
        entries = catalog()
        self.assertEqual([entry["name"] for entry in entries], [
            "get_boxoffice", "get_now_playing", "get_movie_detail", "search_theaters",
        ])
        for entry, definition in zip(entries, to_openai_tools(), strict=True):
            self.assertEqual(set(entry), {"name", "description", "parameters", "kind"})
            self.assertEqual(entry["kind"], "external")
            self.assertEqual(entry["parameters"]["type"], "object")
            for parameter in entry["parameters"]["properties"].values():
                self.assertTrue(parameter["description"])
            self.assertEqual(set(definition), {"type", "function"})
            self.assertEqual(definition["type"], "function")
            self.assertEqual(set(definition["function"]), {"name", "description", "parameters"})
            self.assertEqual(definition["function"]["name"], entry["name"])
        self.assertEqual(catalog("external"), entries)
        self.assertEqual(catalog("local"), [])

    def test_registered_outputs_match_success_data(self):
        descriptions = describe_tools()
        self.assertEqual([entry["name"] for entry in descriptions], [entry["name"] for entry in catalog()])
        for entry in descriptions:
            self.assertEqual(set(entry), {"name", "kind", "description", "parameters", "output", "source"})
            self.assertEqual(set(entry["output"]), {"data", "errors"})
            self.assertTrue(entry["output"]["data"])
            self.assertTrue(entry["output"]["errors"])

        outputs = {entry["name"]: entry["output"]["data"] for entry in descriptions}
        for name, kwargs, body in (
            ("get_boxoffice", {}, {"boxOfficeResult": {"dailyBoxOfficeList": [{"movieCd": "20260001"}]}}),
            ("get_now_playing", {}, {"results": [{"id": 123}]}),
            ("get_movie_detail", {"movie_cd": "20260001"}, {
                "movieInfoResult": {"movieInfo": {"movieNm": "영화 하나", "showTm": "123"}},
            }),
            ("get_movie_detail", {"tmdb_id": 123}, {"title": "영화 하나", "runtime": 123}),
            ("search_theaters", {"region": "강남"}, {"documents": [{"category_name": "영화관"}]}),
        ):
            with self.subTest(name=name, kwargs=kwargs):
                self.respond(body)
                self.assert_success(run_tool(name, **kwargs), outputs[name])

    def test_unknown_tool_and_arguments(self):
        self.assert_failure(run_tool("missing_tool"), BAD_ARGUMENT)
        self.assert_failure(run_tool("search_theaters"), BAD_ARGUMENT)
        self.assert_failure(run_tool("get_boxoffice", unknown=True), BAD_ARGUMENT)
        self.assert_failure(run_tool("get_boxoffice", name="unexpected"), BAD_ARGUMENT)
        self.assert_failure(run_tool("get_boxoffice", self="unexpected"), BAD_ARGUMENT)
        self.get.assert_not_called()

    def test_tool_run_converts_exceptions(self):
        func = Mock()

        def broken():
            return func()

        tool = Tool("broken", "테스트", {"type": "object", "properties": {}, "required": []}, broken)
        for error_type in (RuntimeError, TypeError):
            with self.subTest(error_type=error_type):
                func.reset_mock()
                func.side_effect = error_type(self.key)
                result = tool.run()
                func.assert_called_once_with()
                self.assert_failure(result, UPSTREAM_ERROR)
                self.assertEqual(result, fail(UPSTREAM_ERROR, "Tool 실행 중 오류입니다."))

    def test_tool_run_rejects_arguments_that_do_not_bind(self):
        def lookup(movie_cd):
            self.fail("인자 바인딩 실패 시 함수를 호출하면 안 됩니다.")

        tool = Tool("lookup", "테스트", {"properties": {"tmdb_id": {}}, "required": []}, lookup)
        self.assert_failure(tool.run(tmdb_id=123), BAD_ARGUMENT)

    def test_boxoffice_uses_yesterday_by_default(self):
        self.respond_boxoffice()
        data = self.assert_boxoffice_data(run_tool("get_boxoffice", top_n=1))
        self.assertEqual(data["requested_date"], "2026-11-18")
        self.assertEqual(data["queried_date"], "2026-11-17")
        self.assertIs(data["estimated"], True)
        self.assertIsNone(data["week_range"])
        self.assertEqual(data["movies"], [
            {"movie_cd": "20260001", "movie_nm": "영화 하나", "rank": 1, "open_dt": "2026-11-10"},
        ])
        self.assertEqual(self.get.call_args.kwargs["params"], {"key": self.key, "targetDt": "20261117"})
        self.assertEqual(
            self.get.call_args.args[0],
            "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json",
        )

    def test_boxoffice_future_date_marks_estimate(self):
        self.respond_boxoffice()
        data = self.assert_boxoffice_data(run_tool("get_boxoffice", target_date="2026-11-23"))
        self.assertIs(data["estimated"], True)
        for part in ("추정", "2026-11-23", "2026-11-17"):
            self.assertIn(part, data["note"])

    def test_boxoffice_past_date_uses_requested_date(self):
        self.respond_boxoffice()
        data = self.assert_boxoffice_data(run_tool("get_boxoffice", target_date="2026-11-01"))
        self.assertEqual(data["queried_date"], "2026-11-01")
        self.assertIs(data["estimated"], False)
        self.assertIsNone(data["note"])
        self.assertIsNone(data["movies"][1]["open_dt"])

    def test_weekly_boxoffice_requests_completed_week(self):
        self.respond_boxoffice("weekly")
        for kwargs, requested, estimated in (
            ({"target_date": "2026-11-15"}, "2026-11-15", False),
            ({"target_date": "2026-11-12"}, "2026-11-12", False),
            ({}, "2026-11-18", True),
            ({"target_date": "2026-11-25"}, "2026-11-25", True),
        ):
            with self.subTest(requested=requested):
                data = self.assert_boxoffice_data(run_tool("get_boxoffice", period="weekly", **kwargs))
                self.assertEqual(data["period"], "weekly")
                self.assertEqual(data["requested_date"], requested)
                self.assertEqual(data["queried_date"], "2026-11-15")
                self.assertEqual(data["week_range"], {"start": "2026-11-09", "end": "2026-11-15"})
                self.assertIs(data["estimated"], estimated)
                if estimated:
                    for part in ("추정", requested, "2026-11-09~2026-11-15"):
                        self.assertIn(part, data["note"])
                else:
                    self.assertIsNone(data["note"])
                self.assertEqual(
                    self.get.call_args.args[0],
                    "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchWeeklyBoxOfficeList.json",
                )
                self.assertEqual(self.get.call_args.kwargs["params"], {
                    "key": self.key, "targetDt": "20261115", "weekGb": "0",
                })

    def test_weekly_boxoffice_prefers_valid_show_range(self):
        self.respond_boxoffice("weekly", showRange="20261102~20261108")
        data = self.assert_boxoffice_data(run_tool("get_boxoffice", period="weekly"))
        self.assertEqual(data["queried_date"], "2026-11-15")
        self.assertEqual(data["week_range"], {"start": "2026-11-02", "end": "2026-11-08"})
        self.assertIn("2026-11-02~2026-11-08", data["note"])

        self.respond_boxoffice("weekly", showRange="20261131~20261206")
        data = self.assert_boxoffice_data(run_tool("get_boxoffice", period="weekly"))
        self.assertEqual(data["week_range"], {"start": "2026-11-09", "end": "2026-11-15"})

    def test_weekly_boxoffice_handles_earliest_date(self):
        self.respond_boxoffice("weekly")
        data = self.assert_boxoffice_data(run_tool("get_boxoffice", target_date="0001-01-01", period="weekly"))
        self.assertEqual(self.get.call_args.kwargs["params"]["targetDt"], "00010107")
        self.assertEqual(data["queried_date"], "0001-01-07")
        self.assertEqual(data["week_range"], {"start": "0001-01-01", "end": "0001-01-07"})
        self.assertIs(data["estimated"], False)

    def test_boxoffice_invalid_arguments(self):
        invalid = [
            {"target_date": "20261117"}, {"target_date": "2026-2-1"}, {"target_date": "2026-02-30"},
            {"target_date": ""}, {"period": "monthly"}, {"top_n": 0}, {"top_n": 11}, {"top_n": True},
        ]
        for args in invalid:
            with self.subTest(args=args):
                self.assert_failure(run_tool("get_boxoffice", **args), BAD_ARGUMENT)
        self.get.assert_not_called()

    def test_missing_keys_stop_before_request(self):
        for key_name, tool, args in (
            ("KOBIS_API_KEY", "get_boxoffice", {}),
            ("KOBIS_API_KEY", "get_movie_detail", {"movie_cd": "20260001"}),
            ("TMDB_API_KEY", "get_now_playing", {}),
            ("TMDB_API_KEY", "get_movie_detail", {"tmdb_id": 123}),
            ("KAKAO_REST_API_KEY", "search_theaters", {"region": "강남"}),
        ):
            with self.subTest(tool=tool, key_name=key_name), patch.dict(os.environ, {key_name: ""}):
                self.assert_failure(run_tool(tool, **args), INVALID_KEY)
        self.get.assert_not_called()

    def test_kobis_faults(self):
        for error_code, message, code in (
            ("320010", "잘못된 요청", INVALID_KEY),
            ("320011", "잘못된 요청", INVALID_KEY),
            ("other", "인증 키를 확인하세요", INVALID_KEY),
            ("other", "서버 오류", UPSTREAM_ERROR),
        ):
            with self.subTest(error_code=error_code, message=message):
                self.respond({"faultInfo": {"errorCode": error_code, "message": message}})
                self.assert_failure(run_tool("get_boxoffice"), code)
                self.assert_failure(run_tool("get_movie_detail", movie_cd="20260001"), code)

    def test_boxoffice_empty_list(self):
        self.respond({"boxOfficeResult": {"dailyBoxOfficeList": []}})
        self.assert_failure(run_tool("get_boxoffice"), NO_DATA)

    def test_transient_request_errors_retry_once_and_recover(self):
        self.respond_boxoffice()
        for error_type in (requests.Timeout, requests.ConnectionError):
            with self.subTest(error_type=error_type), patch("app.tools.base.sleep") as sleep:
                self.get.reset_mock()
                self.get.side_effect = [error_type(), self.get.return_value]
                data = self.assert_boxoffice_data(run_tool("get_boxoffice"))
                self.assertEqual(len(data["movies"]), 2)
                self.assertEqual(self.get.call_count, 2)
                first_call, second_call = self.get.call_args_list
                self.assertEqual(first_call, second_call)
                self.assertEqual(first_call.kwargs["timeout"], 5)
                sleep.assert_called_once_with(0.3)

    def test_two_timeouts_return_sanitized_upstream_error(self):
        url = "https://provider.invalid/"
        self.get.side_effect = requests.Timeout(f"{url}?key={self.key}")
        with patch("app.tools.base.sleep") as sleep:
            result = run_tool("get_boxoffice")
        self.assert_failure(result, UPSTREAM_ERROR)
        self.assertNotIn(url, result["error"]["message"])
        self.assertEqual(self.get.call_count, 2)
        sleep.assert_called_once_with(0.3)

    def test_request_and_json_errors_hide_key(self):
        upstream_message = f"https://provider.invalid/?key={self.key}"
        self.get.side_effect = requests.RequestException(upstream_message)
        self.assert_failure(run_tool("get_boxoffice"), UPSTREAM_ERROR)
        self.get.assert_called_once()
        self.get.reset_mock(side_effect=True)
        self.respond({})
        self.get.return_value.json.side_effect = ValueError(upstream_message)
        self.assert_failure(run_tool("get_boxoffice"), UPSTREAM_ERROR)
        self.get.assert_called_once()

    def test_kobis_detail_missing_runtime(self):
        for runtime in ("", "0", 0):
            with self.subTest(runtime=runtime):
                self.respond_kobis_detail(runtime)
                result = run_tool("get_movie_detail", movie_cd="20260001")
                self.assert_failure(result, NO_RUNTIME)
                self.assertEqual(result["error"]["message"], "'영화 하나'의 러닝타임 정보가 없어 판단에서 제외합니다.")

    def test_kobis_detail_normalizes_fields(self):
        self.respond_kobis_detail("123")
        data = self.assert_detail_data(run_tool("get_movie_detail", movie_cd="20260001"))
        self.assertEqual(
            self.get.call_args.args[0],
            "https://www.kobis.or.kr/kobisopenapi/webservice/rest/movie/searchMovieInfo.json",
        )
        self.assertEqual(data["id_type"], "kobis")
        self.assertEqual(data["movie_cd"], "20260001")
        self.assertIsNone(data["tmdb_id"])
        self.assertEqual(data["runtime_min"], 123)
        self.assertEqual(data["watch_grade"], "12세이상관람가")
        self.assertEqual(data["genres"], ["드라마"])
        self.assertEqual(data["open_dt"], "2026-11-10")
        self.assertTrue(self.get.call_args.args[0].endswith("/movie/searchMovieInfo.json"))
        self.assertEqual(self.get.call_args.kwargs["params"], {"key": self.key, "movieCd": "20260001"})

    def test_kobis_detail_absent(self):
        self.respond({"movieInfoResult": {"movieInfo": {"movieNm": ""}}})
        self.assert_failure(run_tool("get_movie_detail", movie_cd="20260001"), NOT_FOUND)

    def test_movie_detail_requires_exactly_one_valid_id(self):
        self.assert_failure(run_tool("get_movie_detail"), BAD_ARGUMENT)
        self.assert_failure(run_tool("get_movie_detail", movie_cd="20260001", tmdb_id=123), BAD_ARGUMENT)
        self.assert_failure(run_tool("get_movie_detail", movie_cd=" "), BAD_ARGUMENT)
        self.assert_failure(run_tool("get_movie_detail", movie_cd="", tmdb_id=""), BAD_ARGUMENT)
        self.assert_failure(run_tool("get_movie_detail", tmdb_id=0), BAD_ARGUMENT)
        self.get.assert_not_called()

    def test_movie_detail_treats_empty_ids_as_missing(self):
        self.respond({"title": "영화 하나", "runtime": 123})
        for movie_cd in (None, "", " \t"):
            with self.subTest(movie_cd=movie_cd):
                data = self.assert_detail_data(run_tool("get_movie_detail", movie_cd=movie_cd, tmdb_id=123))
                self.assertEqual(data["tmdb_id"], 123)
                self.assertIsNone(data["movie_cd"])
        self.respond_kobis_detail("123")
        for tmdb_id in (None, ""):
            with self.subTest(tmdb_id=tmdb_id):
                data = self.assert_detail_data(run_tool("get_movie_detail", movie_cd="20260001", tmdb_id=tmdb_id))
                self.assertEqual(data["movie_cd"], "20260001")
                self.assertIsNone(data["tmdb_id"])

    def test_tmdb_auth_and_now_playing_response(self):
        self.respond({"results": [
            {"id": 123, "title": "영화 하나", "release_date": "", "vote_average": 1},
        ], "total_pages": 2, "dates": {}})
        expected = {"language": "ko-KR", "region": "KR", "page": 2}
        result = run_tool("get_now_playing", page=2)
        data = self.assert_success(result, {"page", "total_pages", "movies", "source"})
        self.assertEqual(data["movies"], [{"tmdb_id": 123, "title": "영화 하나", "release_date": None}])
        self.assertEqual(data["total_pages"], 2)
        self.assertEqual(self.get.call_args.kwargs["params"], {**expected, "api_key": self.key})
        self.assertNotIn("Authorization", self.get.call_args.kwargs["headers"])
        token = f"eyJ{self.key}"
        with patch.dict(os.environ, {"TMDB_API_KEY": token}):
            self.assertIs(run_tool("get_now_playing", page=2)["ok"], True)
        self.assertTrue(self.get.call_args.args[0].endswith("/movie/now_playing"))
        self.assertEqual(self.get.call_args.kwargs["params"], expected)
        self.assertEqual(self.get.call_args.kwargs["headers"], {"Authorization": f"Bearer {token}"})

    def test_tmdb_empty_list_and_invalid_page(self):
        self.assert_failure(run_tool("get_now_playing", page=6), BAD_ARGUMENT)
        self.get.assert_not_called()
        self.respond({"results": []})
        self.assert_failure(run_tool("get_now_playing"), NO_DATA)

    def test_tmdb_detail_missing_runtime(self):
        for runtime in (None, 0):
            with self.subTest(runtime=runtime):
                self.respond({"id": 123, "title": "영화 하나", "runtime": runtime})
                self.assert_failure(run_tool("get_movie_detail", tmdb_id=123), NO_RUNTIME)

    def test_tmdb_detail_normalizes_fields(self):
        self.respond({
            "id": 123, "title": "영화 하나", "runtime": 123, "genres": [{"name": "액션"}],
            "release_date": "2026-11-10", "overview": "줄거리",
        })
        data = self.assert_detail_data(run_tool("get_movie_detail", tmdb_id=123))
        self.assertEqual(data["id_type"], "tmdb")
        self.assertEqual(data["tmdb_id"], 123)
        self.assertIsNone(data["movie_cd"])
        self.assertIsNone(data["watch_grade"])
        self.assertEqual(data["runtime_min"], 123)
        self.assertEqual(data["genres"], ["액션"])
        self.assertTrue(self.get.call_args.args[0].endswith("/movie/123"))
        self.assertEqual(self.get.call_args.kwargs["params"], {"language": "ko-KR", "api_key": self.key})

    def test_tmdb_http_failures(self):
        for status, code in ((401, INVALID_KEY), (404, NOT_FOUND), (429, UPSTREAM_ERROR), (500, UPSTREAM_ERROR)):
            with self.subTest(status=status):
                self.get.reset_mock()
                self.respond({"status_message": self.key}, status=status)
                self.assert_failure(run_tool("get_movie_detail", tmdb_id=123), code)
                self.get.assert_called_once()

    def test_kakao_empty_and_non_theater_results(self):
        for documents in ([], [{"category_name": "문화시설 > 공연장"}]):
            self.respond({"documents": documents})
            result = run_tool("search_theaters", region="강남")
            self.assert_failure(result, NO_RESULT)
            self.assertEqual(result["error"]["message"], "'강남' 주변에서 영화관을 찾지 못했습니다.")

    def test_kakao_filters_and_normalizes_places(self):
        self.respond({"documents": [
            {"place_name": "공연장", "category_name": "문화시설 > 공연장"},
            {"place_name": "영화관 A", "category_name": "문화시설 > 영화관", "address_name": "서울 강남구",
             "road_address_name": "", "place_url": "", "phone": "", "id": "100"},
        ], "meta": {"total_count": 2}})
        result = run_tool("search_theaters", region="강남", size=5)
        data = self.assert_success(result, {"region", "theaters", "note", "source"})
        self.assertEqual(data["theaters"], [
            {"name": "영화관 A", "address": "서울 강남구", "road_address": None, "url": None, "phone": None},
        ])
        self.assertEqual(data["note"], "영화관 검색 결과는 상영 영화 정보를 포함하지 않습니다.")
        self.assertEqual(self.get.call_args.kwargs["headers"], {"Authorization": f"KakaoAK {self.key}"})
        self.assertEqual(self.get.call_args.kwargs["params"], {
            "query": "강남 영화관", "category_group_code": "CT1", "size": 5,
        })

    def test_kakao_http_failures_and_invalid_arguments(self):
        self.assert_failure(run_tool("search_theaters", region=" "), BAD_ARGUMENT)
        self.assert_failure(run_tool("search_theaters", region="강남", size=16), BAD_ARGUMENT)
        self.get.assert_not_called()
        for status, code in ((401, INVALID_KEY), (403, INVALID_KEY), (500, UPSTREAM_ERROR)):
            with self.subTest(status=status):
                self.respond({"message": self.key}, status=status)
                self.assert_failure(run_tool("search_theaters", region="강남"), code)

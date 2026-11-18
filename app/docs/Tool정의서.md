# Tool 정의서

KOBIS·TMDB·카카오 어댑터를 레지스트리에 등록해 Agent가 같은 형식으로 호출합니다. `describe_tools()`는 각 Tool의 `name`, `kind`, `description`, `parameters`, `output`, `source`를 반환합니다. [Tool 레지스트리](../backend/app/tools/registry.py) `output`은 성공 시 `data`의 필드 정의와 실패 코드 목록 `errors`를 담습니다. `catalog(kind=None)`의 항목은 `name`, `description`, `parameters`, `kind`이며, `to_openai_tools()`는 이를 OpenAI 함수 명세로 변환합니다. 현재 Agent는 코드에서 호출 계획을 구성한 뒤 `run_tool(name, **kwargs)`를 실행합니다.

## 공통 형식

```json
{"ok": true, "data": {}}
```

```json
{"ok": false, "error": {"code": "BAD_ARGUMENT", "message": "등록되지 않은 Tool입니다."}}
```

입력 스키마는 모두 JSON 객체이며 `additionalProperties: false`입니다. `Tool.run()`에서 필수 인수, 등록되지 않은 인수와 함수 시그니처를 검사하며, 각 Tool에서 타입·형식·값 범위를 검사합니다. 성공 응답에는 정규화한 필드만 담고, 비어 있는 선택값은 JSON `null`로 보존합니다.

`http_get_json()`의 기본 타임아웃은 요청당 5초입니다. HTTP 요청에서 `Timeout` 또는 `ConnectionError`가 발생하면 0.3초 대기한 뒤 같은 인수로 1회 재시도합니다. HTTP 오류 상태와 JSON 파싱 오류는 재시도하지 않습니다. 어댑터는 HTTP 상태를 Tool 실패 코드로 해석하고, 요청·파싱 예외는 `UpstreamError`를 거쳐 `UPSTREAM_ERROR`로 처리합니다. [공통 HTTP 구현](../backend/app/tools/base.py)

## 등록 Tool

| 이름 · 종류 | 사용 목적 | 출처 · 구현 |
| --- | --- | --- |
| `get_boxoffice` · `external` | 특정 날짜의 인기 영화 후보를 확보합니다. | KOBIS 영화진흥위원회 오픈API · [kobis.py](../backend/app/tools/kobis.py) |
| `get_now_playing` · `external` | 국내 현재 상영작에서 후보를 보충합니다. | TMDB (The Movie Database) API · [tmdb.py](../backend/app/tools/tmdb.py) |
| `get_movie_detail` · `external` | 후보 출처의 ID로 러닝타임과 장르 등을 확인합니다. | KOBIS 영화진흥위원회 오픈API, TMDB (The Movie Database) API · [registry.py](../backend/app/tools/registry.py), [kobis.py](../backend/app/tools/kobis.py), [tmdb.py](../backend/app/tools/tmdb.py) |
| `search_theaters` · `external` | 지역 영화관의 이름과 위치, 링크, 연락처를 확인합니다. | 카카오 로컬 API · [kakao.py](../backend/app/tools/kakao.py) |

레지스트리는 `external`과 `local`을 구분하며, 현재 등록된 네 Tool은 외부 API를 호출하는 `external`입니다. 관람 계획은 화면에서 FastAPI로 직접 관리합니다. [관람 계획 API](API명세서.md#관람-계획)

도구의 성공 `data`는 현재 요청에서 정규화해 사용합니다. 실행 로그에는 제공자·호출 ID·허용된 입력 또는 인자명·성공/실패·소요 시간을 남깁니다. 아래 실호출 근거는 구조화 이벤트에서 확인했습니다. [실호출 요약](logs_sample/live/summary.md)

### `get_boxoffice`

- KOBIS REST 기본 주소 · `https://www.kobis.or.kr/kobisopenapi/webservice/rest`.
- 입력 · `target_date`는 `YYYY-MM-DD` 문자열 또는 `null`이며 생략·`null`이면 KST 오늘을 요청 기준으로 사용합니다. `period`는 `daily`(기본) 또는 `weekly`입니다. `top_n`은 1~10 정수(기본 10)입니다. 필수 필드는 없습니다.
- 성공 `data` · `period`, `requested_date`, `queried_date`, `estimated`, `note`(문자열 또는 `null`), `week_range`(`{start, end}` 또는 일별이면 `null`), `movies`(각 항목 `movie_cd`, `movie_nm`, `rank`, `open_dt`), `source`.
- 일별 조회일은 `min(요청일, KST 어제)`입니다. 주간은 월~일 집계로, 요청일이 완료된 주에 속하면 그 주의 일요일을, 그 이후면 직전 완료 주의 일요일을 조회합니다. 응답의 유효한 `showRange`를 범위로 쓰고, 없으면 조회일에서 월~일을 계산합니다. 지난 집계로 대체한 경우 `estimated: true`와 `note`를 반환합니다.
- 등록 실패 코드 · `BAD_ARGUMENT`, `INVALID_KEY`, `NO_DATA`, `UPSTREAM_ERROR`.
- 실호출 확인 · S1의 일별 조회와 S2의 주간 재계획에서 KOBIS 호출이 성공했습니다. 이벤트의 `target_date`와 `period`는 요청 입력입니다. [S1 실호출 이벤트](logs_sample/live/01_first_pool.json), [S2 실호출 이벤트](logs_sample/live/02_replan.json)

### `get_now_playing`

- 입력 · `page`는 1~5 정수(기본 1)이며 필수 필드는 없습니다.
- 성공 `data` · `page`, `total_pages`(정수 또는 `null`), `movies`(각 항목 `tmdb_id`, `title`, `release_date`), `source`. TMDB 조회에는 `language=ko-KR`, `region=KR`을 사용합니다. 관람 날짜나 영화관을 입력으로 받지 않습니다.
- 등록 실패 코드 · `BAD_ARGUMENT`, `INVALID_KEY`, `NOT_FOUND`, `NO_DATA`, `UPSTREAM_ERROR`.
- 실호출 확인 · S3의 `get_now_playing` 요청에서 TMDB 1페이지와 2페이지를 조회했고 결과 이벤트는 모두 성공입니다. [S3 실호출 이벤트](logs_sample/live/03_limit.json)

### `get_movie_detail`

- 입력 · `movie_cd`(문자열 또는 `null`)와 `tmdb_id`(1 이상의 정수 또는 `null`) 중 유효한 값 하나를 넘깁니다. 스키마의 필수 목록은 비어 있으며, 레지스트리 함수가 두 입력 중 하나만 받도록 검사합니다.
- 성공 `data` · `id_type`(`kobis` 또는 `tmdb`), `movie_cd`, `tmdb_id`, `movie_nm`, `runtime_min`(양의 정수, 분), `watch_grade`(TMDB는 `null`, KOBIS는 값이 비어 있으면 `null`), `genres`(문자열 배열), `open_dt`, `source`. 조회에 사용하지 않은 ID 필드는 `null`입니다.
- 등록 실패 코드 · `BAD_ARGUMENT`, `INVALID_KEY`, `NOT_FOUND`, `NO_RUNTIME`, `UPSTREAM_ERROR`.
- 캐시 · Agent 계층은 같은 요청 안에서 상세 결과를 재사용합니다. 요청 간 6시간 서비스 캐시는 성공한 TMDB 상세에만 적용됩니다. `get_movie_detail` Tool 자체는 무상태입니다.
- 실호출 확인 · KOBIS·TMDB 상세의 성공 이벤트와 S3의 TMDB 상세 `NO_RUNTIME` 1건을 확인했습니다. 이벤트의 `arg_names`에는 `movie_cd` 또는 `tmdb_id`라는 이름만 기록합니다. [S3 실호출 이벤트](logs_sample/live/03_limit.json)

### `search_theaters`

- 입력 · `region`은 비어 있지 않은 필수 문자열입니다. `keyword`는 문자열 또는 `null`(생략·빈 문자열이면 `영화관`)이며, `size`는 1~15 정수(기본 10)입니다.
- 성공 `data` · `region`, `theaters`(각 항목 `name`, `address`, `road_address`, `url`, `phone`), `note`, `source`. 문화시설 카테고리 `CT1` 검색 결과 중 카테고리 이름에 `영화관`이 있는 장소를 반환합니다. 해당 장소의 상영작이나 시간표는 검색 범위 밖입니다.
- 등록 실패 코드 · `BAD_ARGUMENT`, `INVALID_KEY`, `NO_RESULT`, `UPSTREAM_ERROR`.
- 실호출 확인 · S1 강남 검색은 성공했고, S4 마라도 검색은 `NO_RESULT`를 반환했습니다. [S1 실호출 이벤트](logs_sample/live/01_first_pool.json), [S4 실호출 이벤트](logs_sample/live/04_tool_failure.json)

## 실패 케이스별 처리 방식

| 코드 | 발생 조건 | Agent 처리 |
| --- | --- | --- |
| `BAD_ARGUMENT` | 미등록 Tool, 누락·알 수 없는 인수, 날짜·범위 오류, 잘못된 ID 조합 등 | 실패를 기록합니다. ID가 없는 관람 후보는 상세 호출 없이 제외 사유에 `BAD_ARGUMENT`를 남깁니다. |
| `INVALID_KEY` | 키 미설정 또는 외부 API의 인증 오류. 카카오는 권한 오류도 포함합니다. | 후보 조회 Tool이면 `config_error`로 바로 응답합니다. 상세 조회 실패는 해당 후보를 제외하며, 영화관 검색 실패는 영화 추천을 유지하고 카카오 설정 안내를 덧붙입니다. |
| `NOT_FOUND` | KOBIS 상세의 영화 정보 없음, TMDB의 HTTP 404 | 후보 목록에 데이터가 없으면 제약 판단 후 다음 후보 풀로 갈 수 있습니다. 상세 조회 실패면 해당 영화만 제외합니다. |
| `NO_DATA` | 박스오피스나 현재 상영작 목록이 비어 있음 | 조회 실패를 기록합니다. 관람 요청에서 충족 후보가 0개이면 상한 내에서 다음 풀을 조회할 수 있습니다. |
| `UPSTREAM_ERROR` | 타임아웃·통신 장애, 예상 밖 HTTP 오류·응답 형식, 실행 예외 | 표준 실패 응답으로 변환하고 실패한 결과는 사용하지 않습니다. 응답에는 키나 키가 포함된 호출 URL을 싣지 않습니다. |
| `NO_RUNTIME` | 영화에 유효한 양의 러닝타임이 없음 | 러닝타임 확인 불가로 제외하고 나머지 후보를 판단합니다. |
| `NO_RESULT` | 카카오 검색 결과에서 영화관을 찾지 못함 | `tool_result`와 `evidence`에 실패 코드를 남깁니다. 관람 요청은 영화 후보 판단을 계속하고, 답변에 해당 지역에서 영화관을 찾지 못했다는 사유를 표시합니다. 영화관 사실 조회에도 실패를 안내합니다. |

## Tool 등록·확장 방법

1. `app/tools/`에 새 Tool 파일을 작성합니다. 입력을 검증하고 `base.py`의 `ok()`·`fail()`을 사용해 필요한 필드와 실패 코드를 반환합니다.
2. `registry.py`에서 함수를 가져온 뒤 `TOOLS`에 이름을 키로 `Tool(name=..., description=..., parameters=..., func=..., kind=..., source=..., output=...)`을 등록합니다. `output`에 `data` 필드 정의와 `errors`를 기록합니다.
3. 등록 항목은 `catalog()`와 `describe_tools()`에 자동 반영됩니다. 새 Tool을 Agent에서 호출할 때는 `agent/nodes.py`의 호출 계획과 데이터 전달을 연결하고 `tests/test_tools.py`에서 등록 정보와 입출력을 검사합니다.

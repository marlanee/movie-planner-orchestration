# API 명세서

브라우저는 대화와 실행 흐름을 한 응답으로 받고, 별도 경로에서 관람 계획과 실행 로그를 관리합니다. 백엔드 기본 주소는 `http://localhost:8000`이며 본문은 `Content-Type: application/json`으로 보냅니다. 아래 JSON은 필드 형식을 보여 주는 예시입니다. [FastAPI 문서](http://localhost:8000/docs), [OpenAPI JSON](http://localhost:8000/openapi.json)

라우트 구현은 [chat.py](../backend/app/api/chat.py), [plans.py](../backend/app/api/plans.py), [logs.py](../backend/app/api/logs.py), [main.py](../backend/app/main.py)에 있으며 모델은 [schemas.py](../backend/app/schemas.py)에 정의합니다.

## 경로

| 메서드 | 경로 | 입력 | 성공 응답 | 업무 오류 |
| --- | --- | --- | --- | --- |
| GET | `/api/health` | 없음 | 200 · 상태 객체 | — |
| GET | `/api/chat/config` | 없음 | 200 · `ChatConfig` | — |
| POST | `/api/chat` | `ChatRequest` | 200 · `ChatResponse` | 422 |
| GET | `/api/plans` | 선택 쿼리 `watch_date=YYYY-MM-DD` | 200 · `PlanOut[]` | 422 |
| POST | `/api/plans` | `PlanIn` | 201 · `PlanSaveResult` | 422 |
| GET | `/api/plans/{plan_id}` | 정수 `plan_id` | 200 · `PlanOut` | 404, 422 |
| PUT | `/api/plans/{plan_id}` | 정수 `plan_id`, `PlanUpdate` | 200 · `PlanSaveResult` | 404, 422 |
| DELETE | `/api/plans/{plan_id}` | 정수 `plan_id` | 204 · 본문 없음 | 404, 422 |
| GET | `/api/logs` | 없음 | 200 · `LogSession[]` | — |
| GET | `/api/logs/{session_id}` | 문자열 `session_id` | 200 · `LogDetail` | 404 |
| GET | `/api/logs/{session_id}/anomalies` | 문자열 `session_id` | 200 · `AnomalyReport[]` | 404 |
| DELETE | `/api/logs/{session_id}` | 문자열 `session_id` | 204 · 본문 없음 | 404 |

등록된 경로에 지원하지 않는 메서드를 보내면 405입니다. 처리되지 않은 서버 오류는 500입니다. 프런트엔드 CORS 허용 출처는 `http://localhost:5500`, `http://127.0.0.1:5500`입니다.

## 상태와 키 설정

`GET /api/health` 200 응답입니다.

```json
{"status": "ok"}
```

`GET /api/chat/config` 200 응답입니다. `keys`는 설정 유무를, `missing_keys`는 누락된 서비스의 표시명을 알려 줍니다. 유효성은 실제 호출에서 확인합니다.

```json
{
  "keys": {"kobis": false, "kakao": false, "tmdb": false, "gms": false},
  "missing_keys": ["KOBIS", "카카오", "TMDB", "GMS"]
}
```

## 대화

`POST /api/chat`의 `message`는 공백으로만 이루어질 수 없으며 1~1,000자입니다. `session_id`는 선택이며 영문자·숫자·밑줄·하이픈으로 이루어진 1~64자 문자열입니다. 생략하거나 `null`을 보내면 서버가 ID를 생성합니다. 응답의 `session_id`를 후속 요청에 보내면 직전 사용자 조건을 이어받습니다. 세션 Memory는 사용자 조건과 처리 상태만 보관합니다.

```json
{
  "session_id": null,
  "message": "이번 주 토요일 오후 3시 이후 강남에서 영화 보고 저녁 7시 전에 끝내고 싶어"
}
```

예를 들어 GMS 키가 없을 때의 200 응답 구조는 다음과 같습니다. 아래 `trace` 예시는 전체 다섯 이벤트 중 분석 오류 이벤트만 표시합니다.

```json
{
  "session_id": "0d325ecdc99346b1adae6088068cdcaa",
  "status": "config_error",
  "answer": "LLM 키(GMS_KEY)가 설정되지 않았습니다. backend/.env를 확인하세요.",
  "recommendations": [],
  "excluded": [],
  "theaters": null,
  "criteria": {
    "ad_minutes": 10,
    "start_after": null,
    "end_by": null,
    "date": null,
    "formula": "필요 시간 = 러닝타임 + 광고 시간. 희망 시작 시각 + 필요 시간이 제약 종료 시각 이내면 충족합니다."
  },
  "evidence": [],
  "replan_count": 0,
  "limit_reason": null,
  "estimated_note": null,
  "conditions": {},
  "plan_candidates": [],
  "trace": [
    {"session_id": "0d325ecdc99346b1adae6088068cdcaa", "seq": 2, "ts": "2026-11-18T10:00:00.000000+09:00", "type": "error", "stage": "analyze", "payload": {"kind": "MissingKeyError"}, "duration_ms": null}
  ],
  "missing_keys": ["KOBIS", "카카오", "TMDB", "GMS"]
}
```

| 응답 필드 | 의미 |
| --- | --- |
| `status` | `ok`(추천), `fact`(사실 조회), `smalltalk`(일반 대화), `clarify`(되묻기), `limit`(후보 탐색 종료), `config_error`(키 설정 안내), `error`(처리 실패) |
| `limit_reason` | `limit` 사유인 `time`(시간), `genre`(장르), `mixed`(시간과 장르), `title_not_found`(제목 미발견) 중 하나이며, 사유가 없으면 `null`입니다. |
| `recommendations` | 최대 3개. 각 항목의 필드는 `movie_nm`, `movie_cd`, `tmdb_id`, `runtime_min`, `required_min`, `latest_start`, `watch_grade`, `genres`입니다. |
| `excluded` | 제외 영화의 `movie_nm`, `reason` |
| `theaters` | 카카오 조회의 `region`, `theaters`, `note`, `source` 또는 `null` |
| `criteria`, `conditions` | 판단식·광고 시간·판단 기준 시각·날짜와 병합된 관람 조건입니다. 조건 필드는 `date`, `start_after`, `end_by`, `region`, `genre`, `movie_title`입니다. |
| `evidence` | 현재 요청의 조회 근거인 `tool`, `args`, `ok`, `code`, `summary`입니다. TMDB 상세 서비스 캐시 적중 시 `provider: "TMDB"`, `cached: true`가 함께 들어가고 요약에 ‘캐시’를 표시합니다. 실패 상세는 공통 요약으로 바꿉니다. |
| `plan_candidates` | 추천당 저장 후보 1건입니다. `movie_nm`은 병합한 사용자 제목 조건 또는 `null`, `watch_date`와 `start_time`은 요청 조건의 `date`와 `start_after`입니다. KOBIS 후보의 `tmdb_id`는 `null`이며 TMDB 후보에는 해당 ID가 들어갑니다. `end_time`, `theater_name`, `theater_address`는 `null`로 시작합니다. 화면에서 종료 시각을 입력하고 영화관을 선택할 수 있습니다. |
| `trace` | 현재 요청에서 발행한 구조화 `TraceEvent`를 `seq` 순으로 담습니다. 날짜·시각 규칙이 기존 분석값을 실제로 바꾼 경우 `llm_decision.payload.rule_overrides`에 필드별 `{llm, rule}`을 기록합니다. |
| `replan_count`, `estimated_note`, `missing_keys` | 재계획 횟수, 과거 집계로 대체했다는 안내(또는 `null`), 키 누락 표시명 |

Agent의 키 설정 안내나 처리 실패는 HTTP 200의 `status`로 전달합니다. 영화관 검색 등 개별 Tool이 실패해도 추천이 가능하면 `status`는 `ok`이며 `answer`에 조회 실패 사유를 포함합니다. 대화 화면은 `limit_reason`에 따라 시각·장르 조정 또는 제목 확인을 묻고, 제목 미발견 안내에는 입력 조건인 `conditions.movie_title`을 사용합니다. TMDB 서비스 캐시 적중은 별도의 Tool 호출·결과 이벤트를 발행하지 않습니다.

KOBIS 응답값은 현재 요청에서 판단하고 실시간으로 표시하는 데 사용합니다. `answer`·`recommendations`·`excluded`·`evidence`에는 화면 표시용 결과와 계산값이 포함될 수 있습니다. SQLite 로그에는 아래의 구조화 이벤트를 저장하고, 다음 요청의 GMS 분석에는 사용자 메시지·기준일·직전 조건을 전달합니다. 대화 API에서 외부 그래프 추적은 끕니다. 요청 간 서비스 캐시는 성공한 TMDB 상세만 보관합니다.

## 관람 계획

`POST /api/plans` 요청은 `movie_nm`, `watch_date`, `start_time`, `end_time`이 필수입니다. 날짜는 `YYYY-MM-DD`, 시각은 24시간제 `HH:MM`으로 받습니다. 서버는 종료 시각이 같은 날짜의 시작 시각보다 늦은지 검사합니다. 선택값은 `tmdb_id`, `theater_name`, `theater_address`, `memo`, `session_id`입니다. `movie_cd`·`runtime_min`은 생성·수정 모델과 응답 모델에 정의하지 않으며, 요청 본문에 추가해도 저장 필드로 반영되지 않습니다. 저장 폼은 사용자 제목 조건이 없으면 영화명을 빈칸으로 두고, 종료 시각은 사용자가 입력합니다. 생성·수정 시각은 서버에서 부여합니다.

```json
{
  "movie_nm": "테스트 영화",
  "watch_date": "2026-11-21",
  "start_time": "15:00",
  "end_time": "17:10"
}
```

201 응답은 `plan`, `overlaps`, `warning`을 반환합니다. 같은 날 시간이 겹쳐도 저장되며 `overlaps`와 `warning`을 받습니다. 한 계획의 종료가 다른 계획의 시작과 같으면 겹치지 않습니다.

```json
{
  "plan": {
    "plan_id": 1,
    "movie_nm": "테스트 영화",
    "tmdb_id": null,
    "watch_date": "2026-11-21",
    "start_time": "15:00",
    "end_time": "17:10",
    "theater_name": null,
    "theater_address": null,
    "memo": null,
    "session_id": null,
    "created_at": "2026-11-18T10:01:00.123456+09:00",
    "updated_at": null
  },
  "overlaps": [],
  "warning": null
}
```

`GET /api/plans`는 위 `plan` 형태의 배열을 날짜·시작 시각·ID 순으로 반환합니다. `GET /api/plans/1`은 `plan` 객체 하나를 반환합니다. `PUT /api/plans/1`에는 변경할 필드 한 개 이상을 보내며, 200 응답은 같은 `PlanSaveResult` 구조에 갱신 시각과 새 중복 검사 결과를 포함합니다. 갱신된 `updated_at`의 예는 `2026-11-18T10:04:00.654321+09:00`입니다.

```json
{"start_time": "18:00", "end_time": "20:00"}
```

`DELETE /api/plans/1` 성공은 본문 없는 204입니다.

## 실행 로그

`GET /api/logs`는 세션별 요약 배열을 마지막 이벤트 시각 내림차순으로 반환합니다. `error_count`는 `error` 유형을 집계하며 Tool 실패인 `tool_result`와는 별도입니다.

```json
[
  {
    "session_id": "example-session",
    "first_ts": "2026-11-18T10:00:00.000000+09:00",
    "last_ts": "2026-11-18T10:00:00.000000+09:00",
    "event_count": 1,
    "replan_count": 0,
    "error_count": 0
  }
]
```

`GET /api/logs/example-session`은 해당 세션의 `events`를 시각, 동일 시각에서는 `seq` 순으로 반환합니다. 대화의 `trace`와 로그의 `events`는 `session_id`, `seq`, `ts`, `type`, `stage`, `payload`, `duration_ms`를 담는 같은 `TraceEvent` 모델을 사용합니다. 로깅 Agent는 메인 Agent가 발행한 이벤트를 세션별로 그대로 저장합니다.

| 이벤트 | `payload` |
| --- | --- |
| `tool_call` | `call_id`, `tool`, `provider` 및 허용된 `args`(요청 날짜·기간, 페이지 또는 지역). 상세 조회에는 `arg_names`(예: `["movie_cd"]`)를 기록합니다. 같은 실행에서 Tool·입력이 같으면 반복 호출의 `duplicate_of`에 첫 호출의 ID를 기록합니다. |
| `tool_result` | 동일한 `call_id`, `tool`, `provider`, `ok`, 정규화 `code`, 실측 `duration_ms`. 성공 코드는 `null`이고 소요 시간은 이벤트 공통 필드에도 들어갑니다. `summary`는 실시간 `evidence` 필드입니다. |
| `llm_decision` | 의도·분류 이유·요청 조건·규칙 적용 내역을 기록합니다. 같은 세션에 이전 판정이 있으면 `previous_run_verdict`에 `run_index`, `verdict`, `warning_count`, `rules`를 기록합니다. |
| `replan` | 재계획 횟수·이유·다음 후보 풀·요청 내 상세 재사용 수를 기록합니다. |
| `stage_start`, `stage_end`, `error` | 단계 진입은 빈 객체, 종료는 단계별 구조화 필드, 오류는 `kind`(예외 종류)를 기록합니다. |

```json
{
  "session_id": "example-session",
  "events": [
    {
      "session_id": "example-session",
      "seq": 1,
      "ts": "2026-11-18T10:00:00.000000+09:00",
      "type": "stage_start",
      "stage": "analyze",
      "payload": {},
      "duration_ms": null
    }
  ]
}
```

`DELETE /api/logs/example-session`은 해당 세션의 이벤트와 그 실행들의 판정·진단을 함께 삭제하고 204를 반환합니다. 로그 상세 주소로 `PUT`, `PATCH`, `POST`를 보내면 405입니다.

### 실행별 이상 판정 조회

`GET /api/logs/{session_id}/anomalies`는 요청 본문과 쿼리 매개변수 없이 세션의 판정 보고서를 `run_index` 오름차순으로 반환합니다. 로그가 있는 세션에 저장된 보고서가 아직 없으면 200과 빈 배열을 반환합니다.

합성 데이터로 구성한 요청과 200 응답 예시입니다.

```http
GET /api/logs/example-session/anomalies HTTP/1.1
Host: localhost:8000
```

```json
[
  {
    "run_index": 1,
    "first_seq": 1,
    "last_seq": 14,
    "verdict": "anomaly",
    "warning_count": 0,
    "findings": [
      {
        "rule": "repeated_call",
        "level": "anomaly",
        "evidence_seqs": [5, 7],
        "detail": "get_boxoffice 호출이 같은 입력으로 반복됐습니다."
      }
    ],
    "missing_evidence": [],
    "created_at": "2026-11-18T10:00:00.000000+09:00",
    "diagnosis": {
      "status": "ok",
      "text": "실행 단계에서 같은 조회를 두 번 요청했습니다. 첫 조회 결과를 재사용하도록 호출 흐름을 수정하세요."
    }
  }
]
```

- `run_index`는 세션 내 실행 번호이며, `first_seq`와 `last_seq`는 그 실행의 첫 이벤트와 마지막 이벤트 번호입니다. `created_at`은 판정 저장 시각입니다.
- `verdict`는 `normal`, `anomaly`, `undetermined` 중 하나입니다. `findings`는 규칙, 심각도(`warning` 또는 `anomaly`), 근거 이벤트 번호, 설명을 담습니다. `warning_count`는 `warning`만 집계하며, `missing_evidence`에는 판정에 필요한 누락 근거가 들어갑니다.
- `diagnosis`는 저장된 진단이 없으면 `null`입니다. 성공한 진단은 `status: "ok"`와 최대 3문장인 `text`를, 실패한 진단은 `status: "failed"`와 `text: null`을 반환합니다.

로그가 없거나 세션이 삭제됐으면 404와 `{"detail":"그런 실행 로그가 없습니다."}`를 반환합니다. 이 주소는 조회 전용입니다. `PUT`, `PATCH`, `POST`, `DELETE`는 405와 `{"detail":"Method Not Allowed"}`를 반환합니다.

## 오류 응답

| 상태 | 예시 JSON 본문 |
| --- | --- |
| 404 · 계획 없음 | `{"detail":"그런 관람 계획이 없습니다."}` |
| 404 · 로그 없음 | `{"detail":"그런 실행 로그가 없습니다."}` |
| 405 · 지원하지 않는 메서드 | `{"detail":"Method Not Allowed"}` |
| 422 · 공통 입력 검증 | `{"detail":"입력값을 확인해 주세요."}` |
| 422 · 대화 세션 ID 검증 | `{"detail":"세션 ID는 영문자, 숫자, 밑줄, 하이픈을 사용해 1자 이상 64자 이하여야 합니다."}` |
| 422 · 대화 메시지 검증 | `{"detail":"메시지는 공백만으로 구성할 수 없으며 1자 이상 1000자 이하여야 합니다."}` |
| 422 · 계획 종료 시각 검증 | `{"detail":"종료 시각은 시작 시각보다 늦어야 합니다."}` |
| 500 · 서버 오류 | `{"detail":"서버 오류가 발생했습니다."}` |

처리되지 않은 예외의 traceback은 요청 메서드·경로와 함께 서버 로그에 기록합니다.

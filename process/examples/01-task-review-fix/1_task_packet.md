# TASK PACKET
- task_id: T2a-events-store
- 목표: 실행 이벤트 버스(발행·구독), LLM을 쓰지 않는 로깅 서브 Agent, SQLite 저장소(계획·실행 로그)를 구현하고 단위 테스트로 검증한다.
- 관련 요구사항 ID: F3012(발행 구조), F3013, F3014(저장소 계층), F3015, F3016(저장소 계층), NF3003, NF3006
- 관련 결정:
  - 프로젝트 루트 `3회차/movie_planner/`, 백엔드 `backend/`, 가상환경 `backend/.venv`(의존성 설치 완료). 패키지 루트는 `backend/app/` (다른 일꾼이 `app/__init__.py`, `app/config.py`, `app/tools/`를 동시에 만들고 있다 — 그 파일들은 만들거나 고치지 않는다. `app/__init__.py`가 아직 없으면 테스트에서 `sys.path`에 backend를 넣고 import 하되, `app/__init__.py`는 만들지 않는다. 단 `app/events/`, `app/store/`, `app/logging_agent/`의 `__init__.py`는 만든다).
  - 저장소: SQLite 파일 1개. 경로는 환경변수 `PLANNER_DB_PATH`가 있으면 그것, 없으면 `backend/data/planner.db`(디렉터리 없으면 생성). 표준 라이브러리 `sqlite3`만 사용.
  - 시각은 KST(UTC+9) ISO 8601 문자열. zoneinfo는 쓰지 않고 `timezone(timedelta(hours=9))`.
  - 로깅 Agent는 이벤트 수집·저장에 LLM을 쓰지 않는다. 저장 실패가 메인 흐름(발행자)에 예외로 번지면 안 된다. 실행 로그는 수정 기능이 없다(목록·상세·삭제만).
  - 계획: 날짜 필수. 같은 날짜에 시간이 겹치는 계획이 있어도 저장은 하고, 겹치는 계획 목록을 결과와 함께 돌려준다.
- 건드릴 파일 (모두 새로 만듦):
  - `3회차/movie_planner/backend/app/events/__init__.py`
  - `3회차/movie_planner/backend/app/events/bus.py`
  - `3회차/movie_planner/backend/app/store/__init__.py`
  - `3회차/movie_planner/backend/app/store/db.py`
  - `3회차/movie_planner/backend/app/store/plans_repo.py`
  - `3회차/movie_planner/backend/app/store/logs_repo.py`
  - `3회차/movie_planner/backend/app/logging_agent/__init__.py`
  - `3회차/movie_planner/backend/app/logging_agent/agent.py`
  - `3회차/movie_planner/backend/tests/test_events_store.py`
- 건드리지 말 파일: `backend/app/__init__.py`, `backend/app/config.py`, `backend/app/tools/**`, `backend/tests/__init__.py`, `backend/tests/test_tools.py`, `backend/scripts/**`, `backend/.env`(읽지 않음), `backend/.env.example`, `backend/requirements.txt`, `docs/**`, `3회차/1일차_FastAPI/**`, 그 밖 저장소의 모든 파일.
- 구현 지시:
  1. `app/events/bus.py`:
     - 이벤트 타입 상수 집합 `EVENT_TYPES = {"stage_start","stage_end","tool_call","tool_result","llm_decision","replan","error"}`.
     - `class EventBus`: `subscribe(callback) -> Callable[[], None]`(구독 해제 함수 반환), `publish(session_id: str, type: str, stage: str, payload: dict | None = None, duration_ms: int | None = None) -> dict`. 반환 이벤트 dict 필드: `session_id, seq(세션별 1부터 증가 int), ts(KST ISO), type, stage, payload(dict, 없으면 {}), duration_ms(int|None)`. type이 EVENT_TYPES 밖이면 ValueError가 아니라 type="error"로 바꾸지 말고 그대로 두되 payload에 `"unknown_type": True`를 더한다(발행이 실패하지 않게). seq 증가는 `threading.Lock`으로 보호. 각 구독자 호출은 try/except로 감싸 예외를 삼키고 `bus.subscriber_errors`(int)를 1 올린다. 발행자는 구독자가 누구인지 모른다(구독자 0명이어도 정상 반환). `reset_session(session_id)`로 seq 카운터 삭제 가능.
     - `class EventCollector`: 특정 session_id 이벤트만 리스트에 모으는 구독자(`attach(bus)` / `detach()` / `events` 속성). 대화 API가 응답에 실행 이력을 실을 때 쓴다.
     - 모듈 전역 `bus = EventBus()`.
  2. `app/store/db.py`: `db_path() -> Path`(호출 시점의 `PLANNER_DB_PATH` 반영), `get_conn() -> sqlite3.Connection`(row_factory=sqlite3.Row, 호출마다 새 연결, `PRAGMA foreign_keys=ON`), `init_db()` 멱등 테이블 생성:
     - `plans(plan_id INTEGER PRIMARY KEY AUTOINCREMENT, movie_nm TEXT NOT NULL, movie_cd TEXT, tmdb_id INTEGER, watch_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, runtime_min INTEGER, theater_name TEXT, theater_address TEXT, memo TEXT, session_id TEXT, created_at TEXT NOT NULL, updated_at TEXT)`
     - `run_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT NOT NULL, type TEXT NOT NULL, stage TEXT, payload TEXT NOT NULL, duration_ms INTEGER)` + `session_id` 인덱스.
     - `now_kst_iso() -> str` 헬퍼.
  3. `app/store/plans_repo.py` (함수 모듈, 매 호출 `get_conn()` 후 닫기; 처음 호출 전에 `init_db()` 보장):
     - 입력 dict 키: movie_nm, watch_date("YYYY-MM-DD"), start_time("HH:MM"), end_time("HH:MM"), 선택 movie_cd, tmdb_id, runtime_min, theater_name, theater_address, memo, session_id. 검증(형식·end_time > start_time·필수값)은 이 계층에서 `ValueError`(한국어 메시지)로 알린다 — 날짜·시각 형식 오류, 필수값 누락, 종료 ≤ 시작.
     - `create_plan(data) -> tuple[dict, list[dict]]`: 저장 후 (저장된 계획, 겹치는 계획 목록). 겹침 = 같은 watch_date이고 `a.start < b.end and b.start < a.end`(HH:MM 문자열 비교 가능), 자기 자신 제외.
     - `list_plans(watch_date: str | None = None) -> list[dict]`: watch_date, start_time 오름차순.
     - `get_plan(plan_id) -> dict | None`
     - `update_plan(plan_id, data) -> tuple[dict, list[dict]] | None`: 부분 수정 허용(전달된 키만 바꾼 뒤 전체 재검증), updated_at 갱신, 겹침 재계산. 없으면 None.
     - `delete_plan(plan_id) -> bool`
     - 반환 dict는 테이블 컬럼 전부(plan_id, created_at, updated_at 포함).
  4. `app/store/logs_repo.py`: `append_event(event: dict) -> None`(payload는 `json.dumps(ensure_ascii=False, default=str)`), `list_sessions() -> list[dict]`(각 `{session_id, first_ts, last_ts, event_count, replan_count, error_count}`, last_ts 내림차순), `get_session_events(session_id) -> list[dict]`(seq 오름차순, payload는 dict로 복원), `delete_session(session_id) -> int`(삭제 행 수). **수정(update) 함수는 만들지 않는다.**
  5. `app/logging_agent/agent.py`: `class LoggingAgent`: `__init__(self, append=logs_repo.append_event)`, `start(bus) -> None`(구독, 중복 start 무시), `stop() -> None`(구독 해제), `handle(event)`: append 호출을 try/except로 감싸 실패 시 `self.failed_count += 1`(예외 밖으로 안 냄), 성공 시 `self.saved_count += 1`. 이 모듈과 패키지는 langchain·openai를 import하지 않는다. 모듈 docstring에 "수집·저장에 LLM을 쓰지 않는다"를 적는다.
  6. `tests/test_events_store.py`(unittest, 각 테스트는 `tempfile` 임시 디렉터리의 DB를 `patch.dict(os.environ, {"PLANNER_DB_PATH": ...})`로 지정, 네트워크 없음). 상단에서 `sys.path.insert(0, backend 경로)`. 최소 케이스:
     - 구독자 0명 publish 정상 · seq가 세션별로 1,2,3 증가하고 세션끼리 독립 · 구독자가 예외를 던져도 publish가 정상 반환하고 다른 구독자는 받음 · unsubscribe 후 미수신 · EventCollector가 자기 세션만 모음
     - LoggingAgent: 발행 → DB에 순서대로 저장, 두 세션이 섞이지 않음 · append가 예외를 던지는 가짜로 바꿔도 publish 정상, failed_count 증가 · stop 후 저장 안 됨 · `agent.py` 소스 텍스트에 "langchain"/"openai" import 없음
     - logs_repo: list_sessions 집계(replan_count, error_count), get_session_events seq 순, delete_session 후 목록에서 사라짐, 모듈에 update/edit 이름의 공개 함수가 없음
     - plans_repo: create 후 get/list에 created_at 포함 · 같은 날 겹치는 계획 저장 시 저장되고 overlaps에 기존 계획 · 다른 날/안 겹치는 시간이면 overlaps 빈 리스트 · 경계(앞 계획 end == 뒤 계획 start)는 겹치지 않음 · update 부분 수정과 updated_at, 겹침 재계산 · delete 후 get None · 날짜 누락/형식 오류/종료≤시작 ValueError
- 완료 기준:
  1. `backend`에서 `.venv\Scripts\python -m unittest tests.test_events_store -v` 전부 통과, 실패·skip 0. (`tests/__init__.py`가 아직 없어 모듈 import가 안 되면 `.venv\Scripts\python -m unittest discover -s tests -p "test_events_store.py" -v`로 실행해 통과)
  2. 로깅 Agent·logs_repo에 LLM 호출·import 없음, 로그 수정 함수 없음.
  3. 구독자/저장 실패가 publish 호출자에게 예외로 전달되지 않음(테스트로 증명).
  4. TODO·stub·빈 함수·skip 테스트 없음. 지정 파일 외 변경 없음.
- 검증 명령 (작업 디렉터리 `3회차/movie_planner/backend`):
  - `.venv\Scripts\python -m unittest discover -s tests -p "test_events_store.py" -v`
- Astra 지시 반영: 없음
- 수정 회차: 0
- 이전 검토 지적: 없음

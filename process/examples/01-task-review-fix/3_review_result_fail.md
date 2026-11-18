파일은 수정하지 않았습니다. 지시대로 `review_result.md`도 만들지 않았으니, 아래 결과를 오케스트레이터가 해당 파일에 옮겨 주세요. 탐침 스크립트는 scratchpad의 임시 DB에서만 실행했습니다.

# REVIEW RESULT
- task_id: T2a-events-store
- 판정: **실패** (막는 사항은 발견 1 하나뿐입니다. 완료 기준 1~6은 모두 충족합니다.)
- 요구사항별:

  | ID | 판정 | 근거(실행 결과·코드 위치) | 근거 종류(실행/코드/추정) |
  |---|---|---|---|
  | F3012 | 통과 | 이벤트에 session_id·seq·ts·type·stage·payload·duration_ms가 들어감 (`app/events/bus.py:51-59`). 정해진 종류는 stage_start/end, tool_call/result, llm_decision, replan, error (`bus.py:8-11`). 구독자가 없어도 publish가 동작하는 것을 `test_publish_without_subscribers_and_session_sequences`로 확인 | 실행+코드 |
  | F3013 | **실패(부분)** | LLM 미사용, 저장 실패 격리, 세션별 저장은 충족. 그러나 같은 session_id의 seq가 1로 돌아가면 "발생 순서"가 깨짐. 탐침 P1에서 `reset_session` 뒤 결과가 `[(1,turn1-A),(1,turn2-A),(2,turn1-B),(2,turn2-B),(3,turn1-C)]`. 탐침 P2(새 EventBus = 서버 재시작)에서는 `after-restart`가 2번째 자리에 들어감 | 실행 |
  | F3014 | **실패(부분)** | 목록·상세·삭제가 있고 수정 함수는 없음 (`app/store/logs_repo.py:8-44`). 다만 "세션 상세(순서대로)"는 위 F3013과 같은 원인(`logs_repo.py:35` `ORDER BY seq, id`)으로 깨짐 | 실행+코드 |
  | F3015 | 통과 | 겹쳐도 저장하고 겹친 목록을 함께 돌려줌 (`app/store/plans_repo.py:76-88`). 겹침 조건 `start<:end AND :start<end`에서 자기 자신과 경계는 제외 (`plans_repo.py:60-68`). created_at은 KST. P7(정수 날짜, `10:00:00`, 전각 숫자, 공백 영화명, 종료 00:00)과 P8(빈 dict)은 모두 ValueError | 실행+코드 |
  | F3016 | 통과 | 부분 수정 후 다시 검증하고 겹침도 다시 계산 (`plans_repo.py:107-124`). 실패하면 롤백(테스트 266줄). P6: 날짜를 옮기면 겹침 [2]로 갱신되고, `plan_id`·`created_at` 덮어쓰기는 무시됨. 삭제도 동작 | 실행 |
  | NF3003 | 통과 | 구독자 예외를 삼키고 개수만 셈 (`bus.py:60-65`). LoggingAgent도 저장 실패를 삼킴 (`app/logging_agent/agent.py:29-37`). P5: 실제 저장 실패(DB 경로가 디렉터리)에서도 publish는 정상이고 failed_count=1 | 실행 |
  | NF3006 | 통과 | events / store / logging_agent 패키지로 나뉨. logging_agent는 `events.bus`와 `store.logs_repo`만 import | 코드 |

- 발견 사항:
  1. **F1 · 중 · `app/store/logs_repo.py:35`, `app/events/bus.py:45-47, 68-70`**
     - 근거: seq는 프로세스 메모리 안에서 세션별로 세는 값이고, `reset_session`이나 서버 재시작 때 1로 돌아갑니다. 상세 조회는 이 seq를 1순위로 정렬합니다. 그래서 같은 session_id가 계속 쓰이면 이전 실행과 새 실행의 이벤트가 섞입니다(P1·P2에서 재현). D34에 따르면 세션은 여러 턴에 걸쳐 이어집니다. 또 `reset_session`은 이번 작업에서 새로 만든 공개 함수이고, 테스트(45-47줄)가 "1로 돌아감"을 보장하고 있습니다.
     - 영향: 실행 로그 상세 화면의 순서가 틀립니다(F3013·F3014). 데이터가 사라지지는 않습니다.
     - 수정 제안 (아래 중 하나와 회귀 테스트):
       - (a) 정렬을 `ORDER BY ts, seq, id`로 바꿉니다. ts는 seq와 같은 잠금 안에서 찍히므로 한 버스 안에서는 seq와 같은 순서이고, 재시작이나 reset 뒤에는 더 늦은 값입니다. 섞인 순서로 저장해도 seq 순으로 나오는지 보는 기존 테스트(175-197줄)도 그대로 통과합니다.
       - (b) 호출하는 곳이 없는 `reset_session`을 지우거나, 로그 삭제와 함께 쓸 때만 허용한다고 적어 둡니다.
       - 회귀 테스트: "publish → reset_session(또는 새 EventBus) → publish → get_session_events가 발생 순서와 같은지"를 추가합니다.
  2. **F2 · 하 · `bus.py:51-62`**
     - 근거: 모든 구독자와 publish 호출자가 같은 event dict를 나눠 씁니다. payload도 얕은 복사라 안쪽 객체는 공유됩니다. P3에서 한 구독자가 `stage`를 바꾸자, 다른 구독자와 publish가 돌려준 값까지 `hijacked`로 바뀌었습니다.
     - 영향: 구독자 하나가 이벤트를 고치면 로깅 Agent가 저장하는 내용과 발행자가 받는 결과가 달라질 수 있습니다. 구독자 격리가 완전하지 않습니다.
     - 수정 제안: 구독자마다 `copy.deepcopy(event)`를 넘기거나, 구독자는 이벤트를 고치지 않는다고 문서로 정합니다.
  3. **F3 · 하 · `bus.py:41-43`**
     - 근거: payload에 dict가 아닌 값(예: list)을 넘기고 type이 정해진 종류가 아니면, publish가 TypeError를 그대로 던집니다(P4).
     - 영향: 타입 힌트(`dict | None`)를 어긴 호출에서만 생기지만, 발행자가 멈출 수 있습니다.
     - 수정 제안: dict가 아니면 `{"value": payload}`로 감싸거나 ValueError를 명시적으로 냅니다.
  4. **F4 · 하 · `tests/test_events_store.py:154-163`**
     - 근거: LLM 미사용 검사는 import 이름에 "langchain"이나 "openai"가 들어 있는지만 봅니다. 그래서 `from ..llm import …`나 다른 공급자 라이브러리를 통한 LLM 사용은 잡지 못합니다. 지금 코드에는 LLM import가 없다는 것을 직접 읽어 확인했습니다(완료 기준 2 충족).
     - 영향: 나중에 코드가 바뀌었을 때 이 검사가 회귀를 놓칠 수 있습니다.
     - 수정 제안: 허용할 import 목록(`collections.abc`, `threading`, `json`, `..events.bus`, `..store`, `.db`)을 정해 두고 그 밖은 실패시키는 검사로 바꿉니다.
  5. **F5 · 하 · `app/store/db.py:47`**
     - 근거: `PLANNER_DB_PATH=""`이면 `Path(".")`가 되어 기본 경로를 쓰지 않습니다(P9).
     - 영향: `.env`에 빈 값을 두면 DB 연결이 실패합니다.
     - 수정 제안: `os.environ.get("PLANNER_DB_PATH") or _DEFAULT_PATH`로 바꿉니다.

- 실행한 검증:
  - `.venv\Scripts\python -m unittest discover -s tests -p "test_events_store.py" -v`: Ran 16, **OK**, 실패·skip 0, EXIT=0. 환경은 Python 3.12.10, SQLite 3.49.1.
  - 완료 기준 4: 대상 파일에서 TODO/FIXME/NotImplemented/skip/.only/빈 `pass`/`...`를 Grep으로 찾았고 일치 0건. 빈 함수도 없습니다.
  - 경계 탐침은 scratchpad의 `probe/probe.py`로 임시 `PLANNER_DB_PATH`를 써서 돌렸고, 결과 P1~P9는 위에 적은 대로입니다.
  - 성능 측정(`probe/timing.py`): 로깅 Agent를 붙였을 때 publish 한 번에 약 3.5ms로, 문제 아님.

- 일꾼과 갈린 지점: 없음. WORK RESULT를 받지 않았기 때문입니다. 다만 F1은 기존 테스트(45-47줄, 175-197줄)에 들어 있는 "seq가 1로 돌아감 / seq 우선 정렬" 설계와 부딪칩니다. 일꾼이 이 설계를 의도한 것이라고 반론하면 Astra 호출 조건 2의 후보가 됩니다.

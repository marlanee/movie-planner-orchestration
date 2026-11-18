# ASTRA DIRECTIVE

- issue_id: `E1-kobis-storage`

- 진단: **KOBIS 결과는 현재 요청의 계산과 실시간 표시에 사용하고, 실행 로그에는 호출과 판단이 일어난 사실을 발행 시점부터 구조화해 저장한다.** F3012·F3013에서 원형을 보존할 대상은 메인 Agent가 발행한 이벤트다. 명세는 KOBIS 응답 본문을 DB에 복제하도록 요구하지 않는다. 반면 영화명·순위·러닝타임을 담은 `summary`와 `movie_cd`는 원본 JSON을 버려도 조회 결과의 일부를 보존한다. 약관 제6조 제2항은 결과의 저장과 제3자 서비스 재전송을 제한하고, 제3조 제5항과 제6조 제5항은 독립 표시와 출처·이용 명시를 요구한다. 교육용 제출에 대한 예외를 전제로 삼지 않는다. [KOBIS 오픈API 이용약관](https://kobis.or.kr/kobisopenapi/homepg/board/findProvisionInfo.do)

- 해결안:

  1. **실행 이벤트 — `backend/app/agent/nodes.py`, `backend/app/events/bus.py`, `frontend/js/api.js`.** `_publish_tool_call`과 `_record_tool_result`에서 호출마다 앱이 발급한 `call_id`를 요청·응답에 함께 싣는다. 병렬 상세 조회도 같은 ID로 짝이 맞아야 한다. 요청 이벤트에는 Tool명·제공자·요청한 날짜 등 독립적인 입력을, 응답 이벤트에는 실제 `ok`·정규화된 오류 코드·실측 `duration_ms`를 남긴다. KOBIS 응답에서 얻은 인자는 `movie_cd`라는 **인자명**까지만 기록한다. `get_movie_detail`은 KOBIS와 TMDB가 공유하므로 실제 호출 경로로 제공자를 식별한다. 단계 진입·종료, LLM이 내린 의도 판단과 실제 이유, 재계획 사유·회차·다음 후보 풀은 보존한다. `respond_node` 종료 이벤트는 현재 코드의 `status`와 `recommendation_count`면 충분하다. 로그 화면은 `summary` 없이도 요청·결과·시간을 읽을 수 있게 한다.

  2. **저장 경계 — `backend/app/logging_agent/agent.py`, `backend/app/store/logs_repo.py`, `backend/app/agent/memory.py`, `backend/app/agent/detail_cache.py`.** 제공자 결과가 섞일 수 있는 이벤트는 발행 전에 허용한 필드로 구성한다. 로깅 Agent는 이 이벤트를 세션별로 그대로 저장한다. 제목·코드·순위·러닝타임, 이를 역산할 수 있는 계산값, 자유문장 요약·오류 본문·URL은 KOBIS 결과에서 영속 로그로 옮기지 않는다. `result.evidence`와 현재 화면에 필요한 상세값은 요청 안에서만 다룬다. D40에 따라 재계획은 같은 요청의 작업 메모리를 재사용하고, 다음 요청에서는 다시 조회한다. 세션 Memory에는 사용자가 준 조건과 직전 처리 결과인 상태·재계획 횟수 등을 남겨 F3010 후속 요청을 지원한다. KOBIS 결과를 GMS 프롬프트, 외부 관측 서비스, 요청 간 캐시로 전달하는 경로도 차단한다.

  3. **계획 저장 — `backend/app/api/chat.py`, `backend/app/schemas.py`, `backend/app/api/plans.py`, `backend/app/store/plans_repo.py`, `frontend/js/chat.js`.** KOBIS 후보도 계획으로 저장할 수 있게 한다. 영화명은 사용자가 요청에 직접 명시한 제목이 있으면 그 입력을 쓰고, 없으면 저장 폼의 빈 영화명 칸에 사용자가 계획할 작품을 입력한다. 관람 날짜·시작·종료 시각은 사용자가 정한 값으로, 생성 일시는 서버 시각으로 저장한다. KOBIS 러닝타임에서 계산한 종료 시각을 저장 폼에 자동으로 채우지 않는다. 현재 `...candidate`로 후보 전체를 POST하는 부분을 명시적 필드 조립으로 바꾸고, API의 생성·수정 경로에도 같은 기준을 적용한다. 클라이언트가 붙인 출처 라벨만으로 KOBIS 코드나 러닝타임의 저장을 허용하지 않는다. 목록·상세·수정·삭제와 같은 날 시간 겹침 경고는 유지한다.

  4. **제출 샘플 — `backend/scripts/run_scenarios.py`, `docs/logs_sample/`, `docs/검증결과.md`.** 기존 S1~S6을 유지하고 `--mode fixture`와 `--mode live`를 구분한다. `fixture`는 실제 응답을 베끼거나 이름만 바꾸지 않고 직접 만든 영화 데이터와 테스트 대역을 사용한다. 실제 Agent 그래프·이벤트 버스·로깅 Agent·임시 SQLite를 실행해 **각 요청의 최종 답변, 발행 이벤트 전체, 기대와 실제의 차이**를 기록한다. S2에서는 첫 후보가 시간 조건에서 탈락하고 다음 후보가 충족하도록 입력하여, 실제 `replan` 이벤트와 다음 Tool 호출이 남게 한다. JSON과 요약표에 합성 입력 사용 여부를 표시한다. `live`는 별도 경로에 실제 제공자·호출 결과 상태·소요 시간 등 1번의 전체 이벤트만 기록한다. 현재의 `RESULT_FIELDS` 일괄 내보내기를 제거하고 라이브 답변·추천·제외 목록·`evidence`는 파일에 쓰지 않는다. 두 모드의 결과는 서로 덮어쓰지 않는다. 이 방식으로 4.7의 최종 답변과 재계획을 재현하면서 실제 API 연결 증빙도 제시할 수 있다.

  5. **기존 자료 — `backend/data/planner.db`, `docs/logs_sample/*`, 제출 문서와 화면 캡처`.** 현재 DB를 읽기 전용으로 조회한 결과 `run_logs`는 60행, `plans`는 0행이다. `movie_cd` 또는 `러닝타임` 문자열이 있는 행은 한 세션에서 24행이다. 이는 점검 후보 수이며 실제 KOBIS 값이 든 행 수를 확정한 수치는 아니다. 기존 샘플 JSON 6개와 문서 속 답변 인용·캡처까지 확인해 제출본을 새 실행 증빙으로 교체한다. 기존 DB는 제출에서 제외한다. 실제 결과가 있는 옛 세션은 F3014의 세션 삭제 경로로 정리하고, 삭제 전에는 해당 세션이 조회·내보내기로 다시 노출되지 않게 한다. 과거 이벤트를 `UPDATE`해 새 설계로 발행했던 것처럼 만들지 않는다.

  6. **출처 표시 — `frontend/index.html`, `frontend/plans.html`, `frontend/logs.html`, `frontend/js/chat.js`.** 대화 화면에서 KOBIS 제공 결과를 **“영화진흥위원회 영화관입장권통합전산망 · 통합전산망 오픈 API”**로 표시한 독립 영역에 둔다. 앱의 관람시간 계산과 TMDB·카카오 결과도 각각 출처를 식별할 수 있게 한다. 세 화면에서 접근 가능한 `출처·크레딧` 영역에는 공식 승인 TMDB 로고와 원문 고지 **“This product uses the TMDB API but is not endorsed or certified by TMDB.”**를 함께 표시한다. 공식 로고 자산의 색·비율을 유지하고 서비스 자체 표장보다 덜 두드러지게 배치한다. [KOBIS 이용약관](https://kobis.or.kr/kobisopenapi/homepg/board/findProvisionInfo.do) · [TMDB 출처 표시 기준](https://developer.themoviedb.org/docs/faq) · [TMDB 공식 로고](https://www.themoviedb.org/about/logos-attribution)

  7. **NF3007 문구 — `README.md`, `docs/요구사항정의서.md`, `docs/검증결과.md`.** 현 상태에는 세 문서 모두 다음 판정을 쓴다. **“NF3007 부분 적용. 세 화면에 출처 텍스트와 TMDB 고지 문구를 표시한다. TMDB 승인 로고는 미적용이다. KOBIS 결과가 포함될 수 있는 기존 실행 로그와 제출 샘플이 있어 저장·제출 기준 적용은 검증 전이다.”** 구현 뒤 README에는 제공자별 이용 조건·공식 링크·확인일·확인된 호출 한도를, 요구사항정의서에는 저장 및 표시 기준을, 검증결과에는 실제 실행 명령·샘플·크레딧 화면 근거와 남은 항목을 적는다. 계정별 한도 등 미확인 항목이 남으면 전체 판정은 `부분 적용`으로 유지한다.

- 바뀌는 결정: `docs/DECISIONS.md`에 다음 세 문장을 **“Astra 지시로 변경(E1-kobis-storage)”**과 함께 추가한다.
  - **D34·D40 보완.** “KOBIS 결과는 한 요청의 실시간 계산·표시에 사용한다. 세션 간에는 사용자 조건과 처리 상태만 이어받고, 실행 로그는 메인 Agent가 발행한 구조화 이벤트를 그대로 저장한다.”
  - **계획·제출 기준 추가.** “계획에는 독립적으로 입력된 영화명과 사용자가 정한 일정을 저장한다. 최종 답변을 포함하는 제출 샘플은 합성 데이터로 실행하고, 실제 API 호출 증빙에는 실행 이벤트만 남긴다.”
  - **D36 출처 표시 변경.** “KOBIS 제공 결과를 출처와 함께 독립 표시한다. 크레딧에는 TMDB 승인 로고와 공식 고지 문구를 함께 표시한다.”

- 검증 방법: 다음은 **일꾼이 구현 후 실행할 명령과 합격 기준**이다. 기준 디렉터리는 `3회차/movie_planner/backend`다.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` — `test_agent.py`, `test_events_store.py`, `test_api.py`에 회귀 검증을 추가한다. KOBIS 대역에 고유한 제목·코드·러닝타임을 주입해 실시간 응답에는 표시되고 DB 이벤트·세션 Memory·계획 자동 전송값·외부 LLM 입력에는 전파되지 않는지 검사한다. 숫자는 문자열 검색 대신 해당 필드와 파생값을 검사한다. 발행 이벤트와 DB 상세는 순서·건수·필드값이 일치하고, 병렬 호출의 `call_id`마다 요청·결과가 한 쌍이어야 한다. 로깅 비활성 시 대화 성공, 세션 목록·상세·삭제, 로그 수정 요청 `405`, 계획 저장·겹침 경고도 통과해야 한다.
  - `.\.venv\Scripts\python.exe scripts\run_scenarios.py --mode fixture` — 외부 키와 네트워크 없이 S1~S6을 실행한다. 대표 요청의 최종 답변과 전체 이벤트가 있고, S2에서 재계획 사유·다음 호출·최종 상태가 순서대로 확인되어야 한다. 기대와 다른 시나리오는 명령이 실패로 반환해야 한다.
  - `.\.venv\Scripts\python.exe scripts\run_scenarios.py --mode live` — 설정된 키로 실제 호출한 뒤 라이브 산출물의 허용 필드, 요청·응답 쌍, 실측 시간을 확인한다. 키가 없어 실행하지 못하면 `실호출 미검증`으로 기록한다. 마지막으로 세 화면에서 크레딧 접근·로고 표시·고지 원문을, 대화 화면에서 KOBIS 결과의 독립 표시를 확인한다.

- 사용자 확인 필요: 설계 적용에는 없음. D36은 결정 기록상 검토에서 정한 사항이다. 로고 미사용이 별도의 **사용자 명시 결정**으로 확인될 때만 “TMDB 이용을 유지하기 위해 크레딧에 공식 승인 로고를 추가해도 될까요?”라고 묻는다. 기존 DB에서 실제 결과를 담은 세션이 확인되면 삭제 직전에만 “기존 실행 로그에서 KOBIS 결과가 확인된 세션을 삭제해도 될까요?”라고 묻는다.

- 확신도: **상** — 과제 원문의 F3012~F3015·검증·제출 요구사항, 공식 약관, 현재 이벤트 발행·계획 저장·샘플 출력 경로를 대조했다. 요청 안에서의 일시 처리와 앱 자체의 실행 사실을 구분한 것은 교육 과제를 위한 보수적 설계 판단이다. 이번 회차는 읽기 전용 Astra 검토로, 지시서 파일 저장과 구현 후 테스트는 미실행이다.
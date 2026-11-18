# 영화 관람 플래너

SSAFY AI 코어 프로젝트 3회차 과제로 만든 개인 프로젝트이며, 과정 명세서 원문은 포함하지 않습니다.

관람하고 싶은 날짜와 지역, 영화 시작·종료 조건을 대화로 입력하면 박스오피스와 영화 상세정보를 조회해 시간에 맞는 후보를 제시합니다. 영화 길이에 광고 시간(기본 10분)을 더해 늦어도 몇 시에 시작해야 하는지 계산합니다. 카카오 검색 결과는 영화관 위치를 확인하는 데 쓰며, 실제 상영 시간과 상영 여부는 이용할 영화관에서 확인해 주세요.

## 폴더 구조

```text
movie_planner/
├── backend/
│   ├── app/             FastAPI, LangGraph Agent, Tool, 이벤트, SQLite 저장소
│   ├── scripts/         키·Tool·그래프 확인, 대표 시나리오 실행
│   ├── tests/           Tool, Agent, 이벤트·저장소, API, 시나리오 실행기 테스트
│   ├── data/            실행 시 planner.db 생성
│   ├── .env.example
│   └── requirements.txt
├── frontend/            대화(index.html), 계획(plans.html), 실행 로그(logs.html)
└── docs/                서비스 문서, 결정 기록, logs_sample/
```

## Windows에서 실행

저장소의 `backend/`에서 가상환경을 만들고 활성화합니다. CMD에서는 아래와 같이 실행합니다. 기존에 설정한 `.env`가 없는 경우에만 마지막 복사 명령을 실행합니다.

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

PowerShell에서는 `.\.venv\Scripts\Activate.ps1`로 활성화하고, `Copy-Item .env.example .env`로 복사합니다. 키는 백엔드의 `.env`나 프로세스 환경변수에 설정합니다. 프로세스 환경변수가 우선합니다. 저장소에는 변수명만 든 `.env.example`을 둡니다.

키 설정을 마친 뒤 같은 터미널의 `backend`에서 서버를 시작합니다.

```powershell
uvicorn app.main:app --reload
```

별도 터미널에서 `frontend/`로 이동해 화면 서버를 시작합니다.

```powershell
python -m http.server 5500
```

브라우저에서 [대화 화면](http://localhost:5500)과 [FastAPI 문서](http://localhost:8000/docs)를 엽니다. 대화 화면에서 키 누락을 확인할 수 있습니다.

## API 키 발급과 설정

| 서비스 | 발급·설정 절차 | 변수 |
| --- | --- | --- |
| 영화진흥위원회 KOBIS | 가입·로그인 후 사용 목적과 관리명을 등록하고 이용약관에 동의해 키를 발급합니다. [키 발급/관리](https://www.kobis.or.kr/kobisopenapi/homepg/apikey/ckUser/findApikeyList.do) | `KOBIS_API_KEY` |
| 카카오 로컬 | 로그인해 앱을 생성하고 카카오맵 API를 사용할 수 있도록 설정합니다. 앱 설정의 플랫폼 키에서 REST API 키를 확인합니다. [카카오 앱 관리](https://developers.kakao.com/console/app), [앱 설정 안내](https://developers.kakao.com/docs/ko/app-setting/app) | `KAKAO_REST_API_KEY` |
| TMDB v3 | 로그인 후 계정 설정의 API 메뉴에서 사용 목적을 입력하고 약관에 동의해 v3 API 키를 발급받습니다. [API 키 발급](https://www.themoviedb.org/settings/api), [v3 시작하기](https://developer.themoviedb.org/docs/getting-started) | `TMDB_API_KEY` |
| SSAFY GMS | GMS 접근 권한이 있는 계정으로 포털에 로그인해 키를 발급받습니다. 모델과 접속 주소는 `GMS_MODEL`, `GMS_BASE_URL`로 설정할 수 있습니다. [GMS 발급 포털](https://gms.ssafy.io/web/) | `GMS_KEY` |

## 외부 API 출처와 이용 조건 (NF3007)

**부분 적용 (확인일 2026-11-18).** 대화·계획 관리·실행 로그 화면 하단의 **출처·크레딧**에 KOBIS·카카오 출처 링크와 공식 TMDB SVG 로고 및 아래 고지 문구를 표시합니다. 로고는 원래 색·비율을 유지하며 높이는 14px입니다. 대화의 KOBIS 결과는 “영화진흥위원회 영화관입장권통합전산망 · 통합전산망 오픈 API” 영역에 표시하며, 앱 계산·TMDB·카카오 로컬 정보는 각각의 영역에 표시합니다.

| 제공자와 용도 | 이용 조건 | 확인된 호출 한도 | 공식 문서 |
| --- | --- | --- | --- |
| 영화진흥위원회 KOBIS 오픈API · 일별·주간 박스오피스와 영화 상세 | 제3조 제5항은 다른 정보와 섞지 않은 독립 표시 및 출처 명시를 요구합니다. 제6조 제2항은 실시간 사용을 요구하며 결과의 복제·저장·제3자 서비스 재전송을 제한합니다. 제6조 제5항은 통합전산망 오픈 API 이용 사실을 명시하도록 정합니다. | **키당 한도 미확인.** 제4조 제1항은 이용 가능 횟수를 지정할 수 있게 하고, 제2항은 네트워크 사용 초과 시도를 이용 정지 사유로 정합니다. 약관에 호출 횟수 수치는 없습니다. | [이용약관](https://kobis.or.kr/kobisopenapi/homepg/board/findProvisionInfo.do), [이용안내](https://www.kobis.or.kr/kobisopenapi/homepg/board/findServiceGuideList.do) |
| 카카오 로컬 API · 키워드로 영화관 위치 검색 | 카카오디벨로퍼스 운영 정책의 이용 절차와 보안 기준을 따릅니다. 쿼터는 앱 사용량을 기준으로 집계하며, 카카오맵 무료 쿼터는 개발자 계정에서 처음 활성화한 앱에 적용됩니다. | 키워드로 장소 검색 무료 일간 100,000건. 프로젝트 앱의 실제 적용 한도·소모량은 미확인. | [로컬 API](https://developers.kakao.com/docs/ko/local/dev-guide), [쿼터](https://developers.kakao.com/docs/ko/getting-started/quota), [운영 정책](https://developers.kakao.com/terms/ko/site-policies) |
| The Movie Database (TMDB) API · 국내 현재 상영작과 영화 상세 | 비상업용 이용에는 출처 표기가 필요합니다. 소개 또는 크레딧에 승인 로고와 공식 고지를 표시하고 로고의 색·비율을 유지합니다. | 공식 안내는 약 초당 40회 수준의 가변 상한과 HTTP 429 준수를 명시합니다. 이 프로젝트 키의 실제 상한은 미확인. | [API 이용약관](https://www.themoviedb.org/documentation/api/terms-of-use), [출처 표기](https://developer.themoviedb.org/docs/faq), [공식 로고](https://www.themoviedb.org/about/logos-attribution), [호출 제한](https://developer.themoviedb.org/docs/rate-limiting) |
| SSAFY GMS · 자연어 요청 분석 | SSAFY 교육용 포털에서 발급받은 키를 사용합니다. 계정별 세부 이용 조건은 미확인. | 계정별 사용 한도 미확인. | [GMS 포털](https://gms.ssafy.io/web/) |

> This product uses the TMDB API but is not endorsed or certified by TMDB.

KOBIS 응답은 현재 요청의 계산과 표시에 사용합니다. SQLite에 저장하는 실행 로그에는 호출과 판단의 구조화 이벤트를 남기며, 관람 계획에는 사용자가 입력한 영화명과 일정을 저장합니다. 관람 계획(ok·limit)과 출처가 섞인 사실 조회는 출처별 영역으로 표시합니다. KOBIS 단일 출처 사실 조회는 KOBIS 영역 안에 원문을 둡니다. 조회 결과가 없는 응답(smalltalk·clarify·error 등)은 원문을 표시합니다. limit 안내 문구는 앱이 만든 문장입니다. [검증 결과](docs/검증결과.md)

## 기능

- 박스오피스와 국내 현재 상영작에서 후보를 찾고, 시간과 장르 조건을 확인합니다. 같은 세션에서 날짜나 시간 조건을 이어서 바꿀 수 있습니다. 지역을 지정하면 영화관 위치도 안내합니다.
- 선택한 영화를 계획에 저장하고 조회·수정·삭제합니다. 같은 날 시간이 겹치면 경고를 표시합니다. 대화 화면의 실행 흐름과 실행 로그에서 조회 과정과 실패를 확인할 수 있습니다.
- 명확한 한국어 날짜·시각은 코드 규칙을 우선 적용하고 모호한 표현은 LLM 분석값을 유지합니다. 후보 상세를 최대 4건씩 병렬 조회합니다. 같은 요청에서 상세를 재사용하며, 요청 간 서비스 캐시는 성공한 TMDB 상세에만 6시간 적용합니다. 개별 조회가 실패하면 사유를 안내하고 가능한 영화는 계속 추천합니다.
- 로깅 Agent는 이벤트를 저장하고 규칙으로 실행별 이상 징후를 판정합니다. 이상 실행의 원인만 GMS로 진단합니다. 판정과 진단은 실행 로그 화면에서 확인할 수 있습니다.

## 테스트와 대표 시나리오

`backend`에서 실행합니다. 단위 테스트와 `fixture`는 테스트 대역과 임시 저장소를 쓰며 외부 키가 필요하지 않습니다. `live`와 외부 API·LLM 진단은 설정한 키를 사용합니다.

```powershell
python -m unittest discover -s tests -v
python scripts/run_scenarios.py --mode fixture
python scripts/run_scenarios.py --mode live
```

- `fixture`는 실제 Agent 그래프·이벤트 버스·로깅 Agent와 임시 SQLite를 사용합니다. 합성 데이터로 실행한 최종 답변, 발행 이벤트 전체, 기대와 실제 비교를 기록합니다. 대표 요청 전체는 S1, 재계획은 S2에서 확인할 수 있습니다. [합성 실행 요약](docs/logs_sample/fixture/summary.md), [S1 전체 로그](docs/logs_sample/fixture/01_first_pool.json), [S2 재계획 로그](docs/logs_sample/fixture/02_replan.json)
- `live`는 실제 API를 호출하고 구조화 이벤트를 기록합니다. 키가 누락되면 서비스 표시명을 출력하고 종료 코드 2를 반환합니다. S4는 카카오가 `NO_RESULT`를 반환한 뒤에도 `ok`로 종료했습니다. [실호출 요약](docs/logs_sample/live/summary.md), [S4 실호출 이벤트](docs/logs_sample/live/04_tool_failure.json)

2026-11-18 16:30 KST 단위 테스트 실행에서 121개가 모두 통과했습니다. 이상 판정·진단 테스트 17개를 포함하며, 실패와 건너뛴 테스트는 0개였습니다. 합성 시나리오 6건은 모두 기대 결과와 일치했습니다. 실호출 샘플의 첫 이벤트부터 마지막 이벤트까지는 시나리오별 1.350~16.100초였습니다. [검증 결과](docs/검증결과.md#실행-명령과-결과)

추가 진단 명령은 `python scripts/check_tools.py`, `python scripts/check_llm.py`, `python scripts/print_graph.py`입니다. Tool 진단은 조회 결과를 터미널에 출력하므로 KOBIS 결과를 담은 진단 원본은 저장·첨부하지 않습니다.

## 서비스 문서

- [요구사항 정의서](docs/요구사항정의서.md)
- [Agent 설계](docs/Agent설계.md)
- [Tool 정의서](docs/Tool정의서.md)
- [API 명세서](docs/API명세서.md)
- [검증 결과](docs/검증결과.md)
- [설계 결정 기록](docs/DECISIONS.md)

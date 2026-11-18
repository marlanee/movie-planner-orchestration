# 대표 시나리오 실행 요약

synthetic: true

| 시나리오 | synthetic | 상태 | 재계획 수 | Tool 호출 수 | 이벤트 경과(초) | 제공자별 Tool 결과 | 기대와 차이 | 실행 로그 |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |
| S1 한 번에 충족 | true | ok | 0 | 4 | 0.060 | KOBIS ok 3건, KAKAO ok 1건 | 일치 | [01_first_pool.json](01_first_pool.json) |
| S2 재계획 | true | ok | 1 | 6 | 0.109 | KOBIS ok 5건, KAKAO ok 1건 | 일치 | [02_replan.json](02_replan.json) |
| S3 상한 도달 | true | limit | 3 | 9 | 0.171 | KOBIS ok 5건, TMDB ok 4건 | 일치 | [03_limit.json](03_limit.json) |
| S4 Tool 하나 실패 | true | ok | 0 | 4 | 0.067 | KOBIS ok 3건, KAKAO fail/NO_RESULT 1건 | 일치 | [04_tool_failure.json](04_tool_failure.json) |
| S5 Tool 불필요 | true | smalltalk | 0 | 0 | 0.018 | 없음 | 일치 | [05_smalltalk.json](05_smalltalk.json) |
| S6 되묻기 | true | clarify | 0 | 0 | 0.020 | 없음 | 일치 | [06_clarify.json](06_clarify.json) |

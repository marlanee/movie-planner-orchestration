# 대표 시나리오 실행 요약

synthetic: false

| 시나리오 | synthetic | 상태 | 재계획 수 | Tool 호출 수 | 이벤트 경과(초) | 제공자별 Tool 결과 | 실행 로그 |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| S1 한 번에 충족 | false | ok | 0 | 12 | 6.299 | KOBIS ok 11건, KAKAO ok 1건 | [01_first_pool.json](01_first_pool.json) |
| S2 재계획 | false | ok | 1 | 16 | 7.655 | KOBIS ok 15건, KAKAO ok 1건 | [02_replan.json](02_replan.json) |
| S3 상한 도달 | false | limit | 3 | 52 | 16.100 | KOBIS ok 15건, TMDB ok 36건, TMDB fail/NO_RUNTIME 1건 | [03_limit.json](03_limit.json) |
| S4 Tool 하나 실패 | false | ok | 0 | 12 | 6.555 | KOBIS ok 11건, KAKAO fail/NO_RESULT 1건 | [04_tool_failure.json](04_tool_failure.json) |
| S5 Tool 불필요 | false | smalltalk | 0 | 0 | 1.600 | 없음 | [05_smalltalk.json](05_smalltalk.json) |
| S6 되묻기 | false | clarify | 0 | 0 | 1.350 | 없음 | [06_clarify.json](06_clarify.json) |

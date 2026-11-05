# mini-pjt — 출조 Agent

## 1. 무엇을 푸나
입문~중급 생활낚시인이 "어디로, 언제 나가도 되는지"를 한 번에 묻는 출조 Agent입니다.
- 출조지 추천: 어종·시즌·권역·형태 필터 → 불가 제외 → 가능>주의, 동률은 spot_id 순 상위 3곳
- 출조 가능 판정: `data/criteria.json` 임계값으로 풍속·파고·특보·사리를 결정적으로 계산(가능/주의/불가)
- 넓은 질문("내일 서해 날씨", "출조지 추천")은 되묻기 전에 먼저 조회해 보여준다: 권역 요약(가능·주의·불가 곳 수, 특보, 상위 포인트)
- 포인트 정보: 포인트별 도(광역시·도)·시군구·소개·편의시설·낚시 형태·대상어종과 시즌(이번 달 시즌 여부)·배편 안내
- 포인트 검색: 도·시군구(예: 강원도, 거제시)·권역·형태·어종·키워드(예: 주차, 섬)로 조건에 맞는 포인트 목록을 찾는다
- 사용자 선호(장기 기억): 추천에 저장된 선호를 쓰면 어떤 선호를 적용했는지 밝히고, "선호 초기화"로 지울 수 있다
- 규정 RAG: `data/regulations/` 4개 문서를 BM25+Chroma 하이브리드로 검색. 질문의 어종이 규정 제목과 일치하면 그 청크를 우선
- 가상 선상 예약(HITL): 예약번호만 발급하며 데이터는 바꾸지 않는다. 승인 없이는 실행하지 않는다
- 데이터가 없을 때: '없다'로 끝내지 않고 데이터에 있는 대안(비슷한·가까운 포인트, 조건을 푼 추천, 다른 날짜·권역 배편, 규정에 있는 어종)을 제안
- 날씨: 기본(`WEATHER_SOURCE=csv`)은 TODAY=2026-11-06 고정 스냅샷(2026-11-06~08). `WEATHER_SOURCE=kma`면 기상청 실시간 예보로 실제 오늘~글피(D+3)를 지원하고, '오늘'은 요청마다 다시 계산한다(요일 포함, "이번 주말" 해석)
- 물때: 천문 계산으로 구한 정확한 삭·망 시각 기준 음력일로 물때 이름(서해 7물때식, 그 밖 8물때식)과 사리/중물/조금을 정한다. `KHOA_SERVICE_KEY`가 있으면 국립해양조사원 조석예보로 만조·간조 시각도 보여준다
- 데이터 규모(모두 가상): 포인트 62곳, 선박 33척(매일 운항), 금어기 25종·체장 21항목
- 답변 형식: 결론 한 줄 → 데이터 표(추천·판정·배편·포인트 정보·규정·대안) → 짧은 조언·출처. 고정 문구는 표로 바꾸지 않고 그대로 쓴다

## 2. 활용 패턴
| 패턴 | 적용 |
|---|---|
| 단일 `create_agent` + 도구 9개 | recommend_spots, check_condition, search_regulation, find_boats, book_boat, spot_info(포인트 정보), search_spots(포인트 검색), region_summary(권역 요약), reset_preference(선호 초기화) |
| 하이브리드 RAG | BM25 + Chroma(Titan 임베딩 `amazon.titan-embed-text-v2:0`) |
| HITL | book_boat interrupt. `EVAL_AUTO_APPROVE=true`면 자동 승인, 기본은 `HITL_PENDING` 응답 |
| 가드레일 | 입력: 정규화(NFKC·제어/zero-width 문자 제거) 후 공격 패턴(SQL·명령어·경로·스크립트·템플릿 인젝션, 도구 호출 위조, 좌석·가격·판정·ID 변조 지시) 즉시 거절 → 규칙 우선(명백한 인젝션은 즉시 거절, 낚시 키워드가 분명하면 LLM 분류 생략) → 나머지만 LCEL 분류(정상/범위밖/인젝션), 불가 강행 결정적 감지. 출력: 규정 안내 문구 자동 부착, '포인트 없음' 고정 문구 코드로 보장, 규정·판정 질문인데 도구를 안 쓰면 1회 재시도, 도구 결과에 없는 포인트 언급 감지(트레이스 기록) |
| 미들웨어 | ToolCallLimit(전체 8, book_boat 1), 도구 인자 검증(허용 인자·형식·길이·공격 패턴, 위반 시 도구 실행 안 함), 이름·전화 마스킹 |
| 기억 | SQLite 체크포인터(thread_id, 단기), SQLite Store(user_id, 장기 선호) |
| 트레이싱 | LANGSMITH_API_KEY가 있으면 LangSmith, 없으면 `traces/*.jsonl`. 오류 시 사유 코드와 예외 요약(마스킹)을 남김 |
| 실시간 외부 데이터 | 기상청 단기예보(풍속·파고)·기상특보(발효 특보+예비특보), 물때는 월령 계산. 답변에 발표 시각 출처 표기 |
| 대안 추천 | 데이터가 없을 때 도구가 `suggestions`·`alternatives`를 함께 돌려주고 에이전트가 대안을 제안 |
| 표 형식 답변 | 시스템 프롬프트에 데이터 종류별 표 양식을 정의(`src/prompts.py`). 예약 승인 대기 요약도 표로 출력(`src/app.py`) |

## 3. 아키텍처
```
POST /query {question, thread_id?, user_id?}
  → 입력 검증(422: 알 수 없는 필드, thread_id·user_id 형식) → 입력 정규화·공격 패턴 차단 → 입력 가드레일(LCEL) → create_agent(Bedrock Claude Haiku 4.5, temperature=0)
       ├─ recommend_spots / region_summary / check_condition / spot_info / search_spots / find_boats / book_boat / reset_preference
       │     데이터: data/*.csv·criteria.json, 날씨: src/weather.py (forecast.csv 또는 기상청 API)
       │     데이터가 없으면 src/suggest.py 가 대안을 붙여 돌려줌
       └─ search_regulation → retriever(어종 제목 일치 우선 + BM25·Chroma 하이브리드)
  → 출력 가드레일 → 마스킹 → {answer, contexts, trace}
```
전체 처리 120초 초과나 도구·모델 예외는 `ERROR_FIXED`로 응답합니다.

소스 구성(파일 하나에 역할 하나):
```
src/agent.py       메인 에이전트 조립(make_llm, build_app) + 실행 진입점(app, __main__)
src/app.py         요청 처리 파이프라인(FishingApp)        src/server.py   FastAPI POST /query
src/cli.py         대화형 CLI·데모                          src/prompts.py  시스템 프롬프트
src/guardrails.py  입력 가드레일(정규화·공격 패턴·도구 인자 검증 규칙)  src/masking.py  개인정보 마스킹
src/middleware.py  도구 예외 분류·도구 인자 검증·예약 사전검증 미들웨어  src/tracing.py  트레이스 기록
src/constants.py   고정 문구·경로·날짜                      src/utils.py    공용 도우미
src/tools.py       에이전트 도구 9개(@tool)                 src/suggest.py  데이터 없을 때 대안 찾기
src/output_guard.py 출력 가드레일(고정 문구·근거 대조·재시도 판단)  src/tide.py   물때 계산·조석예보
src/data.py        CSV·JSON 읽기(매일 운항 배편 포함)        src/verdict.py  날짜 검증·출조 판정
src/spots.py       포인트명 해석                            src/booking.py  예약 사전검증
src/preferences.py 장기 기억 선호                           src/weather.py  날씨 소스(csv·기상청)
src/retriever.py   규정 하이브리드 검색                     src/chunker.py  규정 청크 나누기
src/vectorstore.py 임베딩·Chroma 생성
```

## 4. 실행 방법
1. `.env.example`을 `.env`로 복사해 값을 채웁니다(AWS 자격증명 포함. 비밀은 코드에 적지 않음).
2. 서버: `bash run.sh` (pip install → data/chroma 없으면 빌드 → `uvicorn src.agent:app --port 8000`). 규정 문서를 고치면 `data/chroma/`를 지우고 `python -m src.retriever`로 다시 만듭니다.
3. 호출: `curl -X POST localhost:8000/query -H "Content-Type: application/json" -d '{"question":"내일 지세포방파제 갈 수 있어?"}'` (Windows Git Bash에서는 한글이 깨지므로 질문을 UTF-8 파일에 담아 `--data-binary @q.json`으로 보냅니다. API 문서: http://localhost:8000/docs)
4. 대화형 CLI(질문 입력 → 답변, 예약은 사람 승인): `python -m src.agent` (새 대화는 `새 대화`, 마칠 때는 `종료` 입력). 고정 예약 데모는 `python -m src.agent --demo`
5. 평가: `python evaluation/run_eval.py --round <N>` (`evaluation/round<N>_report.md` 생성. 재현성을 위해 `WEATHER_SOURCE=csv`로 실행)
   단위 테스트(LLM·API 호출 없음): `python -m pytest tests -q`
6. Docker: `docker build -t fishing-agent .` → `docker run --env-file .env -p 8000:8000 fishing-agent`
7. 실시간 날씨: `.env`에 `WEATHER_SOURCE=kma`, `KMA_SERVICE_KEY=<공공데이터포털 키>`(단기예보·기상특보 조회서비스 활용신청)를 넣고 서버를 재시작합니다. 풍속·파고는 단기예보 06~18시 최댓값(파고가 없으면 주변 격자 탐색, 끝내 없으면 '주의' 이상), 특보는 발효 중 특보(다른 날짜에도 보수적으로 적용)와 해당 날짜 풍랑 예비특보(풍랑주의보로 반영)를 해상구역으로 매칭, 물때는 월령 계산 근사입니다. 실시간 모드의 '오늘'은 서버 시작 시점 기준이라 자정이 지나면 재시작합니다. 평가는 재현성을 위해 `csv`로 돌립니다.

## 5. RAGAS 결과
`evaluation/run_eval.py`가 Bedrock(`us.anthropic.claude-haiku-4-5-20251001-v1:0`, Titan v2 임베딩)으로 RAGAS를 계산했습니다. 계산 대상은 contexts가 있는 건만이고, reference는 expected_traits입니다. RAGAS 점수는 보고용이며 통과 판정에는 쓰지 않습니다.

| 지표 | 1차 (12건) | 2차 (13건) | 4차 (19건) |
|---|---|---|---|
| context_recall | 0.951 | 0.955 | 0.969 |
| context_precision | 0.833 | 0.846 | 0.868 |
| faithfulness | 0.604 | 0.613 | 0.701 |
| answer_relevancy | 0.203 | 0.199 | 0.210 |

answer_relevancy가 낮게 나온 이유는 reference로 쓴 expected_traits가 "추천 3곳 이하; 판정 표기"처럼 체크리스트 형식이어서, 실제 답변 문장과 의미가 잘 맞지 않기 때문으로 봅니다.

## 6. 1·2차 통과율
| 차수 | 통과 | 비고 |
|---|---|---|
| 1차 | **14/15** | 실패 1건은 #10(장기 기억)이다. 체크포인터와 Store가 SQLite 연결 하나를 같이 써서 트랜잭션 오류가 났다. `evaluation/round1_report.md` |
| 2차 | **15/15** | Store 연결을 분리했고, 평가 실행마다 thread·user ID를 격리했다. 목표는 13/15 이상이다. `evaluation/round2_report.md` |
| 3차 | **19/21** | 3차 변경 후 새 문항 6건(16~21번)을 더해 평가. 실패 2건은 회귀였다: #13은 조건 없는 배편 목록을 10척으로 줄인 탓에 B05가 빠져 '운항 정보 없음'으로 오답, #2는 '방파제 기준 판정' 미표기. `evaluation/round3_report.md` |
| 4차 | **21/21** | 예약 요청은 book_boat 를 바로 부르도록 프롬프트·출력 가드레일(재시도)을 보강, 추천 결과에 `basis` 문구 추가, 일부 목록임을 도구가 명시. 전체 21건을 처음부터 다시 1회 실행한 결과다. `evaluation/round4_report.md` |

Sonnet 4.5로 처음 시도했을 때는 Bedrock 일일 토큰 한도(`ThrottlingException`)에 걸려 평가를 마치지 못했습니다. Haiku 4.5로 바꾼 뒤에는 1·2차 모두 Throttling 없이 15건당 6~7분 안에 끝났습니다.

통과 기준은 expected_tools 호출 AND forbidden 위반 없음 AND LLM Judge가 expected_traits 전부 충족이며, 케이스당 1회 결과만 채점합니다. 1·2차 사이에는 `test_queries.csv`를 수정하지 않고 프롬프트·도구 로직·가드레일만 개선합니다.

> 1·2차 결과는 "9. 3차 변경" 이전 코드·데이터 기준이고, 3·4차는 변경 후 평가셋 21건(데이터 확장으로 #7·#9 질문을 독도·개복치로 교체, 16~21번 추가) 기준입니다. 평가는 모두 `WEATHER_SOURCE=csv`로 실행했습니다.

## 7. 회고
- 잘된 점: 판정·추천·예약 검증을 LLM이 아닌 결정적 코드로 구현해 재현성이 높고, 마스킹을 answer·contexts·trace에 일관되게 적용했습니다.
- 어려웠던 점: 한 파일(`src/agent.py`)에 앱·가드레일·미들웨어·트레이싱을 모아야 하는 제출 규약(3차에서 역할별 모듈로 분리)과 Bedrock 호출 한도입니다.
- 시도했다가 바꾼 점: Sonnet 4.5는 평가 1회에 드는 토큰이 일일 한도를 넘었다. 그래서 Haiku 4.5로 바꿨다. Haiku는 예약 정보가 다 갖춰져도 다시 묻는 경향이 있어서, "필수 정보 5개가 있으면 바로 book_boat 호출" 규칙을 시스템 프롬프트에 넣었다.
- 평가 함정: 체크포인터를 SQLite에 영속 저장하는데 thread_id를 고정해 두었더니, 이전 실행의 대화가 다음 평가에 섞였다. 이 상태로 돌린 2차 평가는 11/15였다. 실행마다 ID를 격리하도록 고쳤다.
- 한계: answer_relevancy가 낮고, 장기 기억은 추천을 요청할 때만 저장된다. 기억한 선호를 답변에서 근거로 밝히는 기능(PRD 요구사항)은 아직 구현하지 않았다.

## 8. 핵심 코드 위치
| 기능 | 위치 |
|---|---|
| 고정 문구 상수·경로 | `src/constants.py` (`src.agent` 에서도 같은 이름으로 참조 가능) |
| 마스킹 | `src/masking.py` `mask_text`, `mask_name` |
| 입력 가드레일 / 불가 강행 감지 | `src/guardrails.py` `GuardrailClassification`, `build_guard_chain`, `is_unsafe_push` |
| 입력 보안(정규화·공격 패턴·도구 인자 검증) | `src/guardrails.py` `normalize_input`, `ATTACK_PATTERNS`, `detect_attack`, `validate_tool_args` / `src/middleware.py` `ToolArgGuardMiddleware` / `src/server.py` `QueryRequest`(ID 형식·알 수 없는 필드 거부) |
| 시스템 프롬프트 | `src/prompts.py` `system_prompt` |
| 트레이싱 | `src/tracing.py` `write_trace` |
| 미들웨어·오류 사유 코드 | `src/middleware.py` `ToolFaultMiddleware`, `BookingPrecheckMiddleware`, `error_code` |
| 요청 처리 파이프라인 | `src/app.py` `FishingApp` |
| 에이전트 조립 | `src/agent.py` `make_llm`, `async build_app()` |
| FastAPI `/query` | `src/server.py` `create_api`, `QueryRequest` (`src.agent:app` 으로 실행) |
| CLI 대화형·데모 | `src/cli.py` `_chat`, `_demo` (`python -m src.agent` 로 실행) |
| 도구 정의 | `src/tools.py` `recommend_spots`, `check_condition`, `spot_info`, `search_spots`, `search_regulation`, `find_boats`, `book_boat` |
| 포인트 검색·지역명 해석(도·시군구 별칭) | `src/spots.py` `search_spot_list`, `place_keys`, `resolve_spot` |
| 데이터가 없을 때 대안 | `src/suggest.py` `spot_alternatives`, `relaxed_recommendations`, `boat_alternatives`, `regulation_gap` |
| 데이터 로딩(매일 운항 배편) / 판정 / 포인트 해석 / 선호 / 예약 검증 | `src/data.py` `boats_on`, `src/verdict.py`, `src/spots.py`, `src/preferences.py`, `src/booking.py` |
| 날씨 소스(csv·기상청, 포인트 좌표 `SPOT_GEO`) | `src/weather.py` `get_forecast` |
| 하이브리드 검색(어종 제목 우선) / 청크 / Chroma 빌드 | `src/retriever.py` `search`, `species_chunks`, `src/chunker.py` `load_chunks`, `src/vectorstore.py` `build_chroma` |
| 공용 도우미 | `src/utils.py` `get_text`, `env_true` |
| 권역 요약 / 선호 초기화 도구 | `src/tools.py` `region_summary`, `reset_preference` |
| 출력 가드레일 | `src/output_guard.py` `enforce_fixed_phrases`, `needs_tool_retry`, `unsupported_spots` |
| 물때 | `src/tide.py` `tide_info`, `new_moons` |
| 단위 테스트 | `tests/` |
| 평가·Judge·RAGAS | `evaluation/run_eval.py` |

## 9. 3차 변경 (1·2차 평가 이후)
| 영역 | 변경 |
|---|---|
| 모델 | `us.anthropic.claude-haiku-4-5-20251001-v1:0` (`.env`의 `BEDROCK_MODEL_ID`) |
| 실시간 날씨 | `src/weather.py` 신설, `WEATHER_SOURCE`(`csv`/`kma`) 전환. 기상청 단기예보·특보·예비특보, 월령 물때, 발표 시각 출처 표기 |
| 구조 | `agent.py`·`tools.py`·`retriever.py`를 역할별 모듈로 분리(진입점과 import 경로는 그대로 유지) |
| CLI | `python -m src.agent`가 대화형 모드(`새 대화`/`종료`), 고정 데모는 `--demo` |
| 데이터 | 포인트 29→62곳, 선박 10→33척(`매일` 운항), 규정 어종 확장. 원본 파일에 직접 추가(Seed 불변 조건과 다름) |
| 대안 추천 | 데이터가 없을 때 비슷한·가까운 포인트, 조건 완화 추천, 다른 날짜·권역 배편, 규정에 있는 어종 제안 |
| 도구 | `spot_info`(포인트 정보) 추가로 6개 |
| 답변 형식 | 추천·판정·배편·포인트 정보·규정·대안 데이터를 표로 정리해 답변 |
| 넓은 질문·선호 | `region_summary`로 먼저 조회, 적용한 저장 선호를 답변에 밝힘, `reset_preference`로 초기화 |
| 날짜 | 실시간 모드 D0~D+3, 요청마다 오늘·요일 재계산(동적 프롬프트), 날짜 범위 문구도 요청 시점 기준 |
| 출력 가드레일 | `src/output_guard.py`: 고정 문구 보장, 도구 미사용 재시도, 근거 없는 포인트 언급 감지 |
| 물때 | `src/tide.py`: 정밀 삭망 계산 기반 물때 이름, 국립해양조사원 만조·간조(키 있을 때) |
| 속도·비용 | 입력 가드레일 규칙 우선 처리로 LLM 분류 호출 생략, 시스템 프롬프트 정리 |
| 포인트 상세·검색 | `spots.csv`에 province(도)·city(시군구)·info(소개)·facilities(편의시설) 추가, `search_spots` 도구, 도·시군구 이름으로 포인트 해석 |
| 테스트·평가 | `tests/` pytest 22개(결정적 로직), 평가셋 21건으로 확장 → 3차 19/21, 회귀 수정 후 4차 21/21 |
| 안정성 | 규정 검색 병렬 호출 시 Chroma 초기화 충돌 수정(잠금), 어종 제목 일치 청크 우선 검색, 오류 트레이스에 예외 요약 기록 |
| 평가셋 | #7·#9 질문을 데이터에 없는 대상(독도·개복치)으로 교체 |
| 의존성 | `requirements.txt`에 `httpx`·`aiosqlite`·`pydantic`·`langgraph` 명시 |

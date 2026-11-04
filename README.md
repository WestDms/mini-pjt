# mini-pjt — 출조 Agent

## 1. 무엇을 푸나
입문~중급 생활낚시인이 "어디로, 언제 나가도 되는지"를 한 번에 묻는 출조 Agent입니다.
- 출조지 추천: 어종·시즌·권역·형태 필터 → 불가 제외 → 가능>주의, 동률은 spot_id 순 상위 3곳
- 출조 가능 판정: `data/criteria.json` 임계값으로 풍속·파고·특보·사리를 결정적으로 계산(가능/주의/불가)
- 규정 RAG: `data/regulations/` 4개 문서를 BM25+Chroma 하이브리드로 검색
- 가상 선상 예약(HITL): 예약번호만 발급하며 데이터는 바꾸지 않는다. 승인 없이는 실행하지 않는다
- 날짜는 TODAY=2026-11-06 고정, 2026-11-06~08 범위만 지원

## 2. 활용 패턴
| 패턴 | 적용 |
|---|---|
| 단일 `create_agent` + 도구 5개 | recommend_spots, check_condition, search_regulation, find_boats, book_boat |
| 하이브리드 RAG | BM25 + Chroma(Titan 임베딩 `amazon.titan-embed-text-v2:0`) |
| HITL | book_boat interrupt. `EVAL_AUTO_APPROVE=true`면 자동 승인, 기본은 `HITL_PENDING` 응답 |
| 가드레일 | 입력은 LCEL 분류(정상/범위밖/인젝션)와 불가 강행 결정적 감지, 출력은 규정 안내 문구 자동 부착 |
| 미들웨어 | ToolCallLimit(전체 8, book_boat 1), 이름·전화 마스킹 |
| 기억 | SQLite 체크포인터(thread_id, 단기), SQLite Store(user_id, 장기 선호) |
| 트레이싱 | LANGSMITH_API_KEY가 있으면 LangSmith, 없으면 `traces/*.jsonl` |

## 3. 아키텍처
```
POST /query {question, thread_id?, user_id?}
  → 입력 검증(422) → 입력 가드레일(LCEL) → create_agent(Bedrock Claude Haiku 4.5, temperature=0)
       ├─ recommend_spots / check_condition / find_boats / book_boat (CSV·JSON 데이터)
       └─ search_regulation → retriever(BM25 + Chroma)
  → 출력 가드레일 → 마스킹 → {answer, contexts, trace}
```
전체 처리 120초 초과나 도구·모델 예외는 `ERROR_FIXED`로 응답합니다.

## 4. 실행 방법
1. `.env.example`을 `.env`로 복사해 값을 채웁니다(AWS 자격증명 포함. 비밀은 코드에 적지 않음).
2. 서버: `bash run.sh` (pip install → data/chroma 없으면 빌드 → `uvicorn src.agent:app --port 8000`)
3. 호출: `curl -X POST localhost:8000/query -H "Content-Type: application/json" -d '{"question":"내일 지세포방파제 갈 수 있어?"}'`
4. 대화형 CLI(질문 입력 → 답변, 예약은 사람 승인): `python -m src.agent` (새 대화 `/new`, 종료 `종료`/`exit`). 고정 예약 데모는 `python -m src.agent --demo`
5. 평가: `python evaluation/run_eval.py --round 1` 또는 `--round 2` (`evaluation/round<N>_report.md` 생성)
6. Docker: `docker build -t fishing-agent .` → `docker run --env-file .env -p 8000:8000 fishing-agent`

## 5. RAGAS 결과
`evaluation/run_eval.py`가 Bedrock(`us.anthropic.claude-haiku-4-5-20251001-v1:0`, Titan v2 임베딩)으로 RAGAS를 계산했습니다. 계산 대상은 contexts가 있는 건만이고, reference는 expected_traits입니다. RAGAS 점수는 보고용이며 통과 판정에는 쓰지 않습니다.

| 지표 | 1차 (12건) | 2차 (13건) |
|---|---|---|
| context_recall | 0.951 | 0.955 |
| context_precision | 0.833 | 0.846 |
| faithfulness | 0.604 | 0.613 |
| answer_relevancy | 0.203 | 0.199 |

answer_relevancy가 낮게 나온 이유는 reference로 쓴 expected_traits가 "추천 3곳 이하; 판정 표기"처럼 체크리스트 형식이어서, 실제 답변 문장과 의미가 잘 맞지 않기 때문으로 봅니다.

## 6. 1·2차 통과율
| 차수 | 통과 | 비고 |
|---|---|---|
| 1차 | **14/15** | 실패 1건은 #10(장기 기억)이다. 체크포인터와 Store가 SQLite 연결 하나를 같이 써서 트랜잭션 오류가 났다. `evaluation/round1_report.md` |
| 2차 | **15/15** | Store 연결을 분리했고, 평가 실행마다 thread·user ID를 격리했다. 목표는 13/15 이상이다. `evaluation/round2_report.md` |

Sonnet 4.5로 처음 시도했을 때는 Bedrock 일일 토큰 한도(`ThrottlingException`)에 걸려 평가를 마치지 못했습니다. Haiku 4.5로 바꾼 뒤에는 1·2차 모두 Throttling 없이 15건당 6~7분 안에 끝났습니다.

통과 기준은 expected_tools 호출 AND forbidden 위반 없음 AND LLM Judge가 expected_traits 전부 충족이며, 케이스당 1회 결과만 채점합니다. 1·2차 사이에는 `test_queries.csv`를 수정하지 않고 프롬프트·도구 로직·가드레일만 개선합니다.

## 7. 회고
- 잘된 점: 판정·추천·예약 검증을 LLM이 아닌 결정적 코드로 구현해 재현성이 높고, 마스킹을 answer·contexts·trace에 일관되게 적용했습니다.
- 어려웠던 점: 한 파일(`src/agent.py`)에 앱·가드레일·미들웨어·트레이싱을 모아야 하는 제출 규약과 Bedrock 호출 한도입니다.
- 시도했다가 바꾼 점: Sonnet 4.5는 평가 1회에 드는 토큰이 일일 한도를 넘었다. 그래서 Haiku 4.5로 바꿨다. Haiku는 예약 정보가 다 갖춰져도 다시 묻는 경향이 있어서, "필수 정보 5개가 있으면 바로 book_boat 호출" 규칙을 시스템 프롬프트에 넣었다.
- 평가 함정: 체크포인터를 SQLite에 영속 저장하는데 thread_id를 고정해 두었더니, 이전 실행의 대화가 다음 평가에 섞였다. 이 상태로 돌린 2차 평가는 11/15였다. 실행마다 ID를 격리하도록 고쳤다.
- 한계: answer_relevancy가 낮고, 장기 기억은 추천을 요청할 때만 저장된다. 기억한 선호를 답변에서 근거로 밝히는 기능(PRD 요구사항)은 아직 구현하지 않았다.

## 8. 핵심 코드 위치
| 기능 | 위치 |
|---|---|
| 고정 문구 상수 | `src/agent.py` 51~59행 |
| 마스킹 | `src/agent.py` `mask_text`, `mask_name` |
| 입력 가드레일 / 불가 강행 감지 | `src/agent.py` `GuardrailClassification`, `build_guard_chain`, `is_unsafe_push` |
| 트레이싱 | `src/agent.py` `write_trace` |
| 에이전트 조립 | `src/agent.py` `FishingApp`, `async build_app()` |
| FastAPI `/query`, CLI 데모·대화형 | `src/agent.py` `query_endpoint`, `_demo`, `_chat` |
| 판정·추천·예약 도구 | `src/tools.py` `compute_verdict`, `recommend_spots`, `check_condition`, `book_boat` |
| 하이브리드 검색·Chroma 빌드 | `src/retriever.py` `search`, `build_chroma` |
| 평가·Judge·RAGAS | `evaluation/run_eval.py` |

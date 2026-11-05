# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

`mini-pjt`는 삼성SDS AX 교육(Day1~7)에서 배운 내용을 바탕으로 바이브코딩으로 Agent를 만드는 프로젝트입니다. 낚시 출조 Agent가 구현되어 있고(1·2차 평가 완료, 이후 3차 개선 진행), 기능·구조·실행 방법은 `README.md`, 서비스 정의·정책은 `SERVICE.md`에 있습니다.
https://ucanlabs.kr/classroom/sds-ax/lesson/1141/
교육 실습 레포 `C:\Users\SDS\Desktop\sds-ax-practice`에 검증된 구현이 있습니다. **새로 설계하기 전에 아래 "참고 구현"을 먼저 읽고 같은 패턴을 재사용하세요.**

## 기술 스택
- Python, LangChain, LangGraph
- 모델은 Amazon Bedrock (ChatBedrockConverse)
- Agent 생성은 langchain.agents 의 create_agent 를 쓴다

## 폴더 구조 (제출 규약. 바꾸지 않는다)
src/agent.py       메인 에이전트 그래프
src/tools.py       도메인 도구
src/retriever.py   RAG 파이프라인
data/              사용한 문서와 데이터
evaluation/        test_queries.csv 와 평가 리포트

위 세 파일은 진입점으로 유지하고, 세부 역할은 `src/` 아래 모듈로 나눠 둔다(목록은 `README.md` 3장 "소스 구성").
- 서버: `uvicorn src.agent:app --port 8000` / 대화형 CLI: `python -m src.agent` / Chroma 재생성: `data/chroma/` 삭제 후 `python -m src.retriever`
- 단위 테스트: `python -m pytest tests -q` (LLM·외부 API 호출 없음. 결정적 로직을 바꾸면 먼저 돌린다) / 평가: `WEATHER_SOURCE=csv python evaluation/run_eval.py --round <N>`
- 날씨 소스는 `.env`의 `WEATHER_SOURCE`(`csv` 기본·평가용, `kma` 실시간)로 고른다

## 주고받는 형식 (제출 규약)
- POST /query 로 받고 question 필드를 읽는다
- 답은 answer, contexts, trace 세 키로 돌려준다

## 코드 규칙
- 파일 하나에 한 가지 역할만 둔다
- 함수와 도구에는 한국어 docstring 을 쓴다
- 비밀 값은 .env 에서 읽고 코드에 적지 않는다

## 문서 최신화 규칙
- 기능·구조·데이터·실행 방법·정책이 바뀌는 작업은 md 문서(`README.md`, `SERVICE.md`, `CLAUDE.md`)를 먼저 최신화한 뒤 코드 작업을 한다
- 작업이 끝나면 문서와 실제 결과가 맞는지 다시 확인한다
- `evaluation/round*_report.md` 는 그 시점의 평가 기록이라 고치지 않는다. `data/regulations/*.md` 는 데이터다

## 하지 말 것
- 요청하지 않은 파일을 새로 만들지 않는다
- 기존 파일을 통째로 다시 쓰지 않는다. 바뀐 부분만 고친다

## 참고 구현 (`sds-ax-practice` 기준 경로)

| 용도 | 파일 |
|---|---|
| Supervisor + RAG + MCP 조립, `async build_app()` 팩토리 | `day7_practice/final_scenario.py` |
| MCP 서버(도구 정의) | `day7_practice/mcp_server.py` |
| `create_agent` + 미들웨어(HITL, ToolCallLimit, 가드레일) | `day5_practice/day5_final.py`, `day5_practice/guards.py` |
| 입력 가드레일 / 고정 거절 문구 | `day7_practice/guards_input.py`, `day7_practice/guards_refusal.py` |
| 출력 가드레일, 마스킹·로깅 미들웨어 | `day5_practice/guards_output.py`, `masking_middleware.py`, `logging_middleware.py` |
| Plan-and-Execute | `day7_practice/plan_and_execute.py` |
| 장기 기억(Store) | `day7_practice/long_term_memory.py` |
| JSONL 트레이싱 콜백 | `day7_practice/local_tracer.py` |
| 평가셋 실행 / LLM Judge | `day7_practice/run_eval.py`, `day7_practice/llm_judge.py` |
| 하이브리드 검색(BM25 + 벡터) | `day2_practice/hybrid.py` |
| 벡터DB 생성 | `day6_practice/build_chroma.py` |


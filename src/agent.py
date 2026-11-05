"""agent.py - 메인 에이전트 그래프: Bedrock 모델 + create_agent(도구·미들웨어·체크포인터·Store) 조립

실행: uvicorn src.agent:app --port 8000   /   python -m src.agent (대화형 CLI, HITL 사람 승인 지원)   /   python -m src.agent --demo (고정 예약 데모)
역할별 모듈: constants(고정 문구) · prompts · guardrails · masking · tracing · middleware · app(요청 파이프라인) · server(FastAPI) · cli
"""
import logging
import os
import sys
from pathlib import Path

import aiosqlite
from botocore.config import Config
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, ToolCallLimitMiddleware
from langchain_aws import ChatBedrockConverse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

if __package__ in (None, ""):  # `python src/agent.py` 직접 실행 시 프로젝트 루트를 import 경로에 추가
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app import FishingApp
from src.constants import *  # noqa: F401,F403  고정 문구를 agent.X 로도 참조할 수 있게 다시 내보낸다
from src.constants import STATE_DB
from src.guardrails import build_guard_chain
from src.masking import mask_text
from src.middleware import (AmbiguousSpotMiddleware, BookingPrecheckMiddleware, ToolArgGuardMiddleware,
                            ToolFaultMiddleware)
from src.prompts import dynamic_system_prompt
from src.server import create_api
from src.tools import ALL_TOOLS
from src.tracing import get_langsmith_client

load_dotenv()

logger = logging.getLogger("fishing_agent")


def make_llm() -> ChatBedrockConverse:
    """Bedrock Claude 모델을 만든다(temperature=0, read timeout 60초, 재시도 최대 2회)."""
    return ChatBedrockConverse(
        model=os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        temperature=0,
        config=Config(read_timeout=60, retries={"max_attempts": 2, "mode": "standard"}),
    )


async def build_app() -> FishingApp:
    """SQLite 체크포인터(thread_id)·Store(user_id)와 단일 create_agent 를 조립해 FishingApp 을 만든다."""
    STATE_DB.parent.mkdir(exist_ok=True)
    if os.getenv("LANGSMITH_API_KEY"):
        try:
            get_langsmith_client()  # 자동 트레이싱에도 마스킹 Client 적용
        except Exception as e:
            logger.warning("langsmith client init failed: %s", mask_text(repr(e)))
    # 체크포인터와 Store 가 한 연결을 공유하면 트랜잭션이 서로 겹쳐
    # 'cannot start a transaction within a transaction' 오류가 나므로 연결을 분리한다.
    conn = await aiosqlite.connect(str(STATE_DB))
    store_conn = await aiosqlite.connect(str(STATE_DB))
    checkpointer = AsyncSqliteSaver(conn)
    store = AsyncSqliteStore(store_conn)
    await checkpointer.setup()
    await store.setup()
    llm = make_llm()
    agent = create_agent(
        model=llm,
        tools=ALL_TOOLS,
        middleware=[
            dynamic_system_prompt,  # 모델 호출마다 오늘 날짜·요일을 다시 계산한 시스템 프롬프트
            ToolFaultMiddleware(),
            ToolArgGuardMiddleware(),  # 도구 인자 형식·길이·공격 패턴 검사(위반 시 실행 안 함)
            AmbiguousSpotMiddleware(),  # 지역으로 물었는데 사용자가 고르지 않은 후보 판정은 실행하지 않고 되묻게 함
            ToolCallLimitMiddleware(run_limit=8, exit_behavior="error"),
            ToolCallLimitMiddleware(tool_name="book_boat", run_limit=1, exit_behavior="error"),
            HumanInTheLoopMiddleware(interrupt_on={"book_boat": True}),
            BookingPrecheckMiddleware(),  # after_model 은 역순 실행 → HITL 보다 먼저 사전검증
        ],
        checkpointer=checkpointer,
        store=store,
    )
    return FishingApp(agent, build_guard_chain(llm), conn, store_conn)


app = create_api(build_app)  # uvicorn src.agent:app


if __name__ == "__main__":
    from src.cli import main
    main(build_app, sys.argv[1:])

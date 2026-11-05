"""server.py - FastAPI 서버: POST /query 로 question 을 받아 answer, contexts, trace 를 돌려준다"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictStr, field_validator

from src.masking import mask_text
from src.vectorstore import build_chroma

logger = logging.getLogger("fishing_agent")


class QueryRequest(BaseModel):
    """POST /query 본문. question 은 필수(공백 제외 1~500자 문자열)."""
    question: StrictStr
    thread_id: Optional[StrictStr] = None
    user_id: Optional[StrictStr] = None

    @field_validator("question")
    @classmethod
    def _check_question(cls, v: str) -> str:
        """공백 제외 길이가 1~500자인지 검사한다."""
        if not 1 <= len(v.strip()) <= 500:
            raise ValueError("question 은 공백 제외 1~500자여야 합니다")
        return v


def create_api(factory) -> FastAPI:
    """앱 팩토리(async build_app)를 받아 POST /query 를 제공하는 FastAPI 앱을 만든다."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """서버 시작 시 factory() 로 앱을 만들고 종료 시 닫는다."""
        try:
            await asyncio.to_thread(build_chroma)  # data/chroma/ 가 없으면 시작 시 빌드
        except Exception as e:  # 임베딩 실패해도 서버는 뜨고, 검색 시 오류 처리된다
            logger.warning("chroma build failed: %s", mask_text(repr(e)))
        app.state.fishing = await factory()
        yield
        await app.state.fishing.aclose()

    api = FastAPI(title="출조 Agent", lifespan=lifespan)

    @api.post("/query")
    async def query_endpoint(req: QueryRequest, request: Request) -> JSONResponse:
        """질문을 받아 answer, contexts, trace 를 돌려준다. 잘못된 본문은 FastAPI 가 422 로 응답한다."""
        result = await request.app.state.fishing.query(req.question, req.thread_id, req.user_id)
        return JSONResponse(result)

    return api

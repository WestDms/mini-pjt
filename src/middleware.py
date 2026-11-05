"""middleware.py - 에이전트 미들웨어(도구 예외 분류, 도구 인자 검증, 모호 포인트 되묻기, 예약 사전검증)와 오류 사유 코드"""
import asyncio
import json
import logging
import re

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp

from src.booking import precheck_booking
from src.guardrails import validate_tool_args
from src.spots import resolve_spot

logger = logging.getLogger("fishing_agent")
ARG_REJECTED = "요청 값이 올바르지 않아 실행하지 않았습니다({reason}). 값을 바로잡아 다시 요청해 달라고 안내하라."


class ToolFault(Exception):
    """도구 실행 중 발생한 예외를 모델 예외와 구분하기 위한 래퍼."""


class ToolFaultMiddleware(AgentMiddleware):
    """도구 예외를 ToolFault 로 감싸 사유 코드를 tool_exception 으로 분류하게 한다."""

    def wrap_tool_call(self, request, handler):
        """동기 도구 호출의 예외를 ToolFault 로 변환한다."""
        try:
            return handler(request)
        except GraphBubbleUp:
            raise
        except Exception as e:
            raise ToolFault(repr(e)) from e

    async def awrap_tool_call(self, request, handler):
        """비동기 도구 호출의 예외를 ToolFault 로 변환한다."""
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as e:
            raise ToolFault(repr(e)) from e


class ToolArgGuardMiddleware(AgentMiddleware):
    """도구 인자 검증 미들웨어. 허용 인자·형식·길이·공격 패턴을 실행 직전에 검사하고, 위반하면 도구를 실행하지 않는다."""

    def _reject(self, request):
        """인자가 잘못됐으면 거절 ToolMessage, 괜찮으면 None."""
        call = request.tool_call
        reason = validate_tool_args(call["name"], call.get("args") or {})
        if reason is None:
            return None
        logger.warning("tool arg rejected tool=%s: %s", call["name"], reason)
        return ToolMessage(content=ARG_REJECTED.format(reason=reason), name=call["name"],
                           tool_call_id=call["id"], status="error")

    def wrap_tool_call(self, request, handler):
        """동기 도구 호출 전에 인자를 검사한다."""
        return self._reject(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        """비동기 도구 호출 전에 인자를 검사한다."""
        return self._reject(request) or await handler(request)


ALL_SPOTS_RE = re.compile(r"모두|전부|전체|각각|비교|다\s*(알려|봐|확인|비교)")


def unchosen_candidate(question: str, spot_name: str) -> dict | None:
    """질문이 지역(도·시군구)이라 포인트가 여럿인데, 사용자가 말하지 않은 포인트를 판정하려 하면 후보 정보를 돌려준다.
    사용자가 그 포인트를 직접 말했거나 '모두·비교'를 원하면 None(그대로 실행)."""
    q = question or ""
    res = resolve_spot(spot_name)
    if res["status"] != "resolved" or ALL_SPOTS_RE.search(q):
        return None
    name = res["candidate_names"][0]
    if name in q:
        return None
    asked = resolve_spot(q)
    if asked["status"] != "ambiguous":
        return None
    return {"status": "ambiguous", "query": q, "candidate_names": asked["candidate_names"],
            "instruction": "사용자가 포인트를 정하지 않았다. 판정하지 말고 candidate_names 를 보여 주며 어느 포인트인지 되물어라."}


def _user_question(messages) -> str:
    """마지막 사용자 메시지에서 질문을 꺼낸다(출력 가드레일 재시도 메시지면 '원래 질문' 부분)."""
    for m in reversed(messages or []):
        if isinstance(m, HumanMessage):
            text = m.content if isinstance(m.content, str) else str(m.content)
            return text.split("원래 질문:", 1)[-1].strip()
    return ""


class AmbiguousSpotMiddleware(AgentMiddleware):
    """모호한 포인트 되묻기 보장 미들웨어. '거제 갯바위'처럼 지역으로 물었는데 사용자가 고르지 않은 후보를
    check_condition 으로 판정하려 하면 실행하지 않고 후보와 되묻기 지시를 돌려준다."""

    def _reject(self, request):
        """고르지 않은 후보 판정이면 ambiguous ToolMessage, 아니면 None."""
        call = request.tool_call
        if call["name"] != "check_condition":
            return None
        q = _user_question((request.state or {}).get("messages"))
        out = unchosen_candidate(q, (call.get("args") or {}).get("spot_name", ""))
        if out is None:
            return None
        logger.info("unchosen candidate blocked: %s", (call.get("args") or {}).get("spot_name"))
        return ToolMessage(content=json.dumps(out, ensure_ascii=False), name=call["name"], tool_call_id=call["id"])

    def wrap_tool_call(self, request, handler):
        """동기 도구 호출 전에 검사한다."""
        return self._reject(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        """비동기 도구 호출 전에 검사한다."""
        return self._reject(request) or await handler(request)


def error_code(e: BaseException) -> str:
    """예외를 사유 코드(tool_exception | model_exception | tool_call_limit | timeout)로 분류한다."""
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(e, ToolCallLimitExceededError):
        return "tool_call_limit"
    if isinstance(e, ToolFault):
        return "tool_exception"
    return "model_exception"


class BookingPrecheckMiddleware(AgentMiddleware):
    """book_boat 사전검증 미들웨어. HITL 보다 먼저 결정적으로 검증하고, 실패하면 interrupt 없이 거절한다."""

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        """마지막 AIMessage 의 book_boat 호출이 사전검증에 실패하면 거절 ToolMessage 를 넣고 모델로 되돌아간다."""
        msgs = state["messages"]
        last = msgs[-1] if msgs else None
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return None
        bad = []
        for c in last.tool_calls:
            if c["name"] == "book_boat":
                a = c["args"]
                reason = validate_tool_args("book_boat", a)  # 변조된 인자는 HITL 승인 요청 전에 거절
                if reason:
                    bad.append(ToolMessage(content=ARG_REJECTED.format(reason=reason), name="book_boat",
                                           tool_call_id=c["id"]))
                    continue
                pc = precheck_booking(a.get("boat_id"), a.get("date"), a.get("people"),
                                      a.get("name"), a.get("phone"))
                if pc["status"] != "ok":
                    bad.append(ToolMessage(content=pc["reason"], name="book_boat", tool_call_id=c["id"]))
        if not bad:
            return None
        # 같은 AIMessage 의 다른 호출에도 응답을 채워 메시지 쌍을 유지한다
        done = {m.tool_call_id for m in bad}
        for c in last.tool_calls:
            if c["id"] not in done:
                bad.append(ToolMessage(content="같은 턴에 예약이 거절되어 실행하지 않았습니다.",
                                       name=c["name"], tool_call_id=c["id"]))
        return {"messages": bad, "jump_to": "model"}

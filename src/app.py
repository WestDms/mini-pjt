"""app.py - 요청 처리 파이프라인(FishingApp): 입력 가드레일 → 에이전트(+HITL) → 출력 가드레일 → 마스킹·트레이스"""
import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from src.constants import (BOOKING_CANCELLED, ERROR_FIXED, HITL_PENDING, REFUSAL_INJECTION, REFUSAL_OUT_OF_SCOPE,
                           REGULATION_DISCLAIMER, TIMEOUT_SEC, UNSAFE_PUSH)
from src.guardrails import is_unsafe_push
from src.masking import PII_NAMES, mask_text, register_names
from src.middleware import error_code
from src.tracing import write_trace
from src.utils import env_true, get_text

logger = logging.getLogger("fishing_agent")


class FishingApp:
    """에이전트·체크포인터·Store·가드레일을 묶은 앱. query() 가 /query 한 건을 처리한다."""

    def __init__(self, agent, guard_chain, conn, store_conn=None):
        self.agent = agent
        self.guard_chain = guard_chain
        self.conn = conn
        self.store_conn = store_conn

    async def aclose(self) -> None:
        """체크포인터·Store 의 SQLite 연결을 닫는다."""
        await self.conn.close()
        if self.store_conn is not None:
            await self.store_conn.close()

    async def query(self, question: str, thread_id: Optional[str] = None,
                    user_id: Optional[str] = None) -> dict:
        """질문 1건을 처리해 {answer, contexts, trace} 를 돌려준다. 전체 120초 초과 시 ERROR_FIXED."""
        thread_id = thread_id or uuid.uuid4().hex
        PII_NAMES.set(set())
        register_names(question)
        trace: list[dict] = []
        try:
            res = await asyncio.wait_for(self._query(question, thread_id, user_id, trace), TIMEOUT_SEC)
        except BaseException as e:  # 시간 초과 포함 모든 오류는 HTTP 200 + 고정 문구로
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            code = error_code(e)
            logger.error("query error code=%s: %s", code, mask_text(repr(e))[:300])
            # 원인 추적을 위해 사유 코드 뒤에 예외 요약을 남긴다(사용자 답변은 고정 문구, 트레이스는 마스킹됨)
            trace.append({"step": "error", "input": mask_text(question),
                          "output": f"{code}: {mask_text(repr(e))[:300]}"})
            res = {"answer": ERROR_FIXED, "contexts": [], "trace": trace}
        return self._finish(question, thread_id, res)

    def _finish(self, question: str, thread_id: str, res: dict) -> dict:
        """응답 전체에 마스킹을 적용하고 트레이스를 traces/*.jsonl 에 남긴다."""
        out = {
            "answer": mask_text(res["answer"]),
            "contexts": [{"doc_id": c["doc_id"], "text": mask_text(c["text"])} for c in res["contexts"]],
            "trace": [{"step": t["step"], "input": mask_text(t["input"]), "output": mask_text(t["output"])}
                      for t in res["trace"]],
        }
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "thread_id": thread_id,
               "question": mask_text(question), **out}
        write_trace(rec)
        logger.info("query done thread=%s answer=%s", thread_id, out["answer"][:80])
        return out

    async def _history(self, config) -> list:
        """thread 의 저장된 메시지 목록(원문)을 읽는다."""
        state = await self.agent.aget_state(config)
        return list(state.values.get("messages", [])) if state and state.values else []

    async def _query(self, question, thread_id, user_id, trace) -> dict:
        config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}
        history = await self._history(config)

        # 1) 입력 가드레일
        if is_unsafe_push(question, history):
            trace.append({"step": "guardrail_input", "input": question, "output": "불가 강행 감지"})
            trace.append({"step": "final", "input": question, "output": UNSAFE_PUSH})
            return {"answer": UNSAFE_PUSH, "contexts": [], "trace": trace}
        recent = "\n".join(get_text(m) for m in history if isinstance(m, HumanMessage))[-600:] or "(없음)"
        cls = await self.guard_chain.ainvoke({"history": recent, "question": question})
        trace.append({"step": "guardrail_input", "input": question, "output": f"{cls.label}: {cls.reason}"})
        if cls.label == "범위밖":
            trace.append({"step": "final", "input": question, "output": REFUSAL_OUT_OF_SCOPE})
            return {"answer": REFUSAL_OUT_OF_SCOPE, "contexts": [], "trace": trace}
        if cls.label == "인젝션":
            trace.append({"step": "final", "input": question, "output": REFUSAL_INJECTION})
            return {"answer": REFUSAL_INJECTION, "contexts": [], "trace": trace}

        # 이전 요청이 승인 대기로 남아 있으면 먼저 취소해 thread 를 정리한다
        state = await self.agent.aget_state(config)
        if state.next:
            await self.agent.ainvoke(Command(resume={"decisions": [
                {"type": "reject", "message": "새 요청이 와서 이전 예약 요청은 취소되었습니다."}]}), config)
        before = len(await self._history(config))

        # 2) 에이전트 실행 (+ HITL)
        await self.agent.ainvoke({"messages": [HumanMessage(question)]}, config)
        pending = await self._pending(config)
        if pending and env_true("EVAL_AUTO_APPROVE"):
            await self.agent.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
            pending = await self._pending(config)
        return await self._collect(question, config, before, trace, pending)

    async def _pending(self, config):
        """처리 대기 중인 interrupt 값을 돌려준다(없으면 None)."""
        state = await self.agent.aget_state(config)
        for t in state.tasks:
            for i in t.interrupts:
                return i.value
        return None

    async def resume(self, thread_id: str, approve: bool, user_id: Optional[str] = None) -> dict:
        """(CLI 데모 전용) 승인 대기 중인 book_boat 를 한 번만 승인·거절해 재개한다."""
        PII_NAMES.set(set())
        config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}
        if await self._pending(config) is None:
            return self._finish("resume", thread_id, {"answer": ERROR_FIXED, "contexts": [], "trace": [
                {"step": "error", "input": "resume", "output": "승인 대기 중인 요청이 없습니다."}]})
        before = len(await self._history(config))
        decision = {"type": "approve"} if approve else {"type": "reject", "message": BOOKING_CANCELLED}
        await self.agent.ainvoke(Command(resume={"decisions": [decision]}), config)
        trace: list[dict] = []
        res = await self._collect("resume", config, before, trace, None)
        if not approve:  # 거절은 빈 응답 여부와 상관없이 고정 취소 문구로 확정한다
            res["answer"] = BOOKING_CANCELLED
            res["trace"] = [t for t in res["trace"] if t["step"] != "error"]
            res["trace"].append({"step": "final", "input": "resume", "output": BOOKING_CANCELLED})
        return self._finish("resume", thread_id, res)

    async def _collect(self, question, config, before, trace, pending) -> dict:
        """이번 턴의 메시지에서 trace·contexts·answer 를 뽑고 출력 가드레일을 적용한다."""
        full = await self._history(config)
        msgs = full[before:]
        for m in full:  # 재개 턴에서도 이전 턴 book_boat 인자의 이름을 마스킹 대상에 등록한다
            if isinstance(m, AIMessage):
                for c in m.tool_calls:
                    if c["name"] == "book_boat":
                        PII_NAMES.get().add(str(c["args"].get("name", "")))
        calls = {}
        contexts: list[dict] = []
        used_regulation = False
        for m in msgs:
            if isinstance(m, AIMessage):
                for c in m.tool_calls:
                    calls[c["id"]] = c
                    if c["name"] == "book_boat":
                        register_names(str(c["args"].get("name", "")))
                        PII_NAMES.get().add(str(c["args"].get("name", "")))
        for m in msgs:
            if isinstance(m, ToolMessage):
                call = calls.get(m.tool_call_id, {})
                name = m.name or call.get("name", "")
                content = get_text(m)
                trace.append({"step": f"tool:{name}", "input": json.dumps(call.get("args", {}), ensure_ascii=False),
                              "output": content})
                if name == "search_regulation":
                    used_regulation = True
                    for ch in (m.artifact or []):
                        contexts.append({"doc_id": ch["doc_id"], "text": ch["text"]})
                else:
                    contexts.append({"doc_id": f"tool:{name}", "text": content})
        if pending is not None:
            reqs = pending.get("action_requests", []) if isinstance(pending, dict) else []
            a = reqs[0]["args"] if reqs else {}
            PII_NAMES.get().add(str(a.get("name", "")))
            summary = (f"배ID {a.get('boat_id')} / 날짜 {a.get('date')} / 인원 {a.get('people')}명 / "
                       f"예약자 {a.get('name')} / 연락처 {a.get('phone')}")
            answer = f"{HITL_PENDING}\n{summary}"
            trace.append({"step": "hitl_pending", "input": json.dumps(a, ensure_ascii=False), "output": answer})
            return {"answer": answer, "contexts": contexts, "trace": trace}
        ai = [m for m in msgs if isinstance(m, AIMessage) and get_text(m).strip()]
        answer = get_text(ai[-1]).strip() if ai else ""
        if not answer:
            trace.append({"step": "error", "input": question, "output": "빈 응답"})
            return {"answer": ERROR_FIXED, "contexts": contexts, "trace": trace}
        if used_regulation and REGULATION_DISCLAIMER not in answer:  # 출력 가드레일
            answer = f"{answer}\n\n{REGULATION_DISCLAIMER}"
            trace.append({"step": "guardrail_output", "input": question, "output": "참고용 안내 문구 추가"})
        trace.append({"step": "final", "input": question, "output": answer})
        return {"answer": answer, "contexts": contexts, "trace": trace}

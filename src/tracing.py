"""tracing.py - 요청별 트레이스 기록. LANGSMITH_API_KEY 가 있으면 LangSmith, 없으면 traces/*.jsonl"""
import json
import logging
import os
from datetime import datetime

from src.constants import TRACE_DIR
from src.masking import mask_text

logger = logging.getLogger("fishing_agent")


def _mask_obj(obj):
    """dict·list·str 안의 모든 문자열에 마스킹을 재귀 적용한다(LangSmith hide 함수용)."""
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, dict):
        return {k: _mask_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_mask_obj(v) for v in obj]
    return obj


_LS_CLIENT = None


def get_langsmith_client():
    """hide_inputs/hide_outputs 에 마스킹 함수를 건 LangSmith Client 를 만들고 자동 트레이싱 기본 Client 로도 등록한다."""
    global _LS_CLIENT
    if _LS_CLIENT is None:
        from langsmith import Client
        _LS_CLIENT = Client(api_key=os.environ["LANGSMITH_API_KEY"],
                            hide_inputs=_mask_obj, hide_outputs=_mask_obj)
        try:  # 자동 트레이싱(LangChainTracer)도 같은 마스킹 Client 를 쓰게 한다
            import langchain_core.tracers.langchain as _lt
            _lt._CLIENT = _LS_CLIENT
        except Exception:  # pragma: no cover
            pass
    return _LS_CLIENT


def _write_langsmith(rec: dict) -> None:
    """마스킹된 요청 레코드를 LangSmith 프로젝트에 run 으로 기록한다."""
    client = get_langsmith_client()
    now = datetime.utcnow()
    client.create_run(
        name="fishing_query", run_type="chain",
        project_name=os.getenv("LANGSMITH_PROJECT", "fishing-agent"),
        inputs={"question": rec["question"], "thread_id": rec["thread_id"]},
        outputs={"answer": rec["answer"], "contexts": rec["contexts"], "trace": rec["trace"]},
        start_time=now, end_time=now,
    )
    client.flush()


def _write_jsonl(rec: dict) -> None:
    """마스킹된 요청 레코드를 traces/YYYYMMDD.jsonl 에 한 줄 추가한다."""
    TRACE_DIR.mkdir(exist_ok=True)
    with open(TRACE_DIR / f"{datetime.now():%Y%m%d}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_trace(rec: dict) -> None:
    """LANGSMITH_API_KEY 가 있으면 LangSmith 에, 없거나 실패하면 로컬 JSONL 에 요청별 레코드를 남긴다."""
    if os.getenv("LANGSMITH_API_KEY"):
        try:
            _write_langsmith(rec)
            return
        except Exception as e:
            logger.warning("langsmith trace failed, JSONL fallback: %s", mask_text(repr(e)))
    try:
        _write_jsonl(rec)
    except OSError as e:
        logger.warning("trace write failed: %s", e)

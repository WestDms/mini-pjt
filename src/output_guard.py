"""output_guard.py - 출력 가드레일: 고정 문구 보장, 도구를 안 쓴 답변의 재시도 판단, 근거 없는 포인트 언급 감지

프롬프트 지시만으로는 모델이 고정 문구를 바꿔 말하거나 도구 없이 답하는 일이 생겨서, 지켜야 할 것을 코드로 확인한다.
"""
import json
import re

from src.data import load_spots
from src.suggest import regulation_species

DATE_RANGE_RE = re.compile(r"죄송합니다\. \d{4}-\d{2}-\d{2} ~ \d{4}-\d{2}-\d{2} 범위의 날씨·물때 정보만 제공할 수 있어요\.")
REG_RE = re.compile(r"금어기|체장|포획|몇\s*cm|몇\s*센티|잡아도\s*(돼|되)|가져가도")
COND_RE = re.compile(r"날씨|바람|파도|파고|출조|나가도|갈\s*수|가도\s*(돼|될)|괜찮|물때|특보|낚시\s*(돼|될|어때)")
WHEN_RE = re.compile(r"\d{1,2}월\s*\d{1,2}일|\d{4}-\d{2}-\d{2}|오늘|내일|모레|글피|주말")
REGIONS = ("서해", "남해", "동해", "제주")
BOOK_RE = re.compile(r"\bB\d{2}\b", re.I)
PEOPLE_RE = re.compile(r"\d+\s*명")
PHONE_RE = re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}")
RETRY_MESSAGE = ("[자동 확인] 방금 답변은 도구 결과 없이 작성되었습니다. 이 질문은 도구로 확인해야 합니다. "
                 "알맞은 도구를 호출하고 그 결과로 다시 답하세요. 원래 질문: {question}")


def enforce_fixed_phrases(answer: str, tool_outputs: list[str]) -> tuple[str, list[str]]:
    """도구가 돌려준 고정 문구(포인트 없음 message, 날짜 범위 안내)가 답변에 그대로 없으면 맨 앞에 넣는다.
    날짜 범위 문구는 이번 턴의 도구 결과가 전부 그 문구일 때만 넣는다(다른 날짜 결과와 섞이면 혼란스럽다)."""
    added = []
    outs = [o.strip() for o in tool_outputs if o and o.strip()]
    if outs and all(DATE_RANGE_RE.fullmatch(o) for o in outs) and outs[0] not in answer:
        added.append(outs[0])
    for o in outs:
        try:
            obj = json.loads(o)
        except ValueError:
            continue
        msg = obj.get("message") if isinstance(obj, dict) and obj.get("status") == "not_found" else None
        if msg and msg not in answer and msg not in added:
            added.append(msg)
    if added:
        answer = "\n\n".join(added) + "\n\n" + answer
    return answer, added


def needs_tool_retry(question: str, called_tools: list[str]) -> str | None:
    """도구를 하나도 안 쓴 답변인데 도구로 확인해야 하는 질문이면 사유를 돌려준다(아니면 None)."""
    if called_tools:
        return None
    q = question or ""
    if BOOK_RE.search(q) and PEOPLE_RE.search(q) and PHONE_RE.search(q) and "예약" in q:
        return "배ID·인원·전화번호가 있는 예약 요청인데 book_boat 를 쓰지 않음"
    if REG_RE.search(q) and any(n in q for n in regulation_species()):
        return "규정 질문인데 search_regulation 을 쓰지 않음"
    names = [s["name"] for s in load_spots()]
    if COND_RE.search(q) and (WHEN_RE.search(q) or any(r in q for r in REGIONS) or any(n in q for n in names)):
        return "날씨·출조 질문인데 도구를 쓰지 않음"
    return None


def unsupported_spots(answer: str, evidence: str) -> list[str]:
    """답변에 나온 포인트 이름 중 대화의 도구 결과·사용자 질문(evidence)에 없는 것을 돌려준다."""
    return [s["name"] for s in load_spots() if s["name"] in answer and s["name"] not in evidence]

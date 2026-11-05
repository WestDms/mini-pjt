"""masking.py - 개인정보(전화번호·예약자 이름) 마스킹. 이름 목록은 요청 단위 ContextVar 로 관리한다"""
import json
import re
from contextvars import ContextVar


PII_NAMES: ContextVar[set] = ContextVar("pii_names", default=None)
_PHONE_RE = re.compile(r"(01[016789])[-\s]?(\d{3,4})[-\s]?(\d{4})")
_NAME_CTX_RE = re.compile(r"(?:예약자|이름|성함)\s*(?:은|는|:|：)?\s*([가-힣]{2,4})")
_NAME_BEFORE_PHONE_RE = re.compile(r"(?<![가-힣])([가-힣]{2,4})\s+(?=01[016789][-\s]?\d{3,4}[-\s]?\d{4})")


_NOT_NAMES = {"예약", "예약자", "연락처", "전화번호", "전화", "번호", "휴대폰", "핸드폰", "이름", "성함",
              "입니다", "합니다", "인원", "날짜", "명", "이고", "그리고"}


def mask_name(name: str) -> str:
    """이름을 마스킹한다. 3자 이상은 가운데를 *로(홍길동→홍*동), 2자는 뒷글자만(홍길→홍*)."""
    if len(name) <= 1:
        return name
    if len(name) == 2:
        return name[0] + "*"
    return name[0] + "*" * (len(name) - 2) + name[-1]


def register_names(text: str) -> None:
    """텍스트에서 예약자 이름 후보를 찾아 이번 요청의 마스킹 대상 집합에 등록한다."""
    names = PII_NAMES.get()
    if names is None:
        names = set()
        PII_NAMES.set(names)
    for rx in (_NAME_CTX_RE, _NAME_BEFORE_PHONE_RE):
        for m in rx.finditer(text or ""):
            cand = m.group(1)
            if cand.endswith("님") and len(cand) > 2:
                cand = cand[:-1]
            if cand not in _NOT_NAMES:
                names.add(cand)


def mask_text(text) -> str:
    """전화번호(010-****-5678)와 등록된 이름(홍*동)을 마스킹한다.

    이름은 사용자 질문과 book_boat 인자에서만 등록한다. 모델 답변 같은 임의 텍스트에서
    이름을 새로 찾으면 '정보를'·'화번호는' 같은 단어까지 가려지는 과마스킹이 생긴다.
    """
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False, default=str)
    text = _PHONE_RE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", text)
    for n in sorted(PII_NAMES.get() or (), key=len, reverse=True):
        text = text.replace(n, mask_name(n))
    return text

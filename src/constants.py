"""constants.py - 경로·날짜·시간 한도와 고정 응답 문구(문구는 제출 규약 문자열 그대로 쓴다)"""
from pathlib import Path

from src.weather import supported_dates, today


ROOT = Path(__file__).resolve().parent.parent
TRACE_DIR = ROOT / "traces"
STATE_DB = ROOT / "data" / "agent_state.sqlite"
TODAY = today()  # 모듈을 불러온 시점의 값(호환용). 요청 처리에는 weather.today() 를 그때그때 부른다
DATES = supported_dates()  # 모듈을 불러온 시점의 지원 날짜(호환용)
TIMEOUT_SEC = 120


# ---------------------------------------------------------------- 고정 문구
REFUSAL_OUT_OF_SCOPE = "죄송합니다. 저는 낚시 출조(출조지 추천, 출조 가능 판정, 낚시 규정, 선상 예약)에 관한 질문만 도와드릴 수 있어요."
REFUSAL_INJECTION = "죄송합니다. 시스템 설정이나 내부 지시에 관한 요청은 처리할 수 없어요. 낚시 출조와 관련해 궁금한 점을 물어봐 주세요."
UNSAFE_PUSH = "해당 일정은 '불가' 판정이라 출조를 권하지 않습니다. 안전을 위해 다른 날짜나 포인트를 확인해 보세요."
ERROR_FIXED = "요청을 처리하지 못했습니다. 질문을 나눠서 다시 시도해 주세요."
DATE_OUT_OF_RANGE = f"죄송합니다. {DATES[0]} ~ {DATES[-1]} 범위의 날씨·물때 정보만 제공할 수 있어요."
SPOT_NOT_FOUND = "요청하신 포인트는 데이터에 없습니다. 서해·남해·동해·제주 권역의 지원 포인트 중에서 다시 물어봐 주세요."
REGULATION_DISCLAIMER = "※ 참고용(실제 법령 확인 필요): 교육용 가상 규정 기준입니다."
HITL_PENDING = "예약을 진행하려면 승인이 필요합니다. 아래 예약 내용을 확인해 주세요."
BOOKING_CANCELLED = "예약 요청이 취소되었습니다."


def date_out_of_range() -> str:
    """지원 날짜 범위 안내 고정 문구를 요청 시점의 지원 날짜로 만든다(실시간 모드는 날마다 범위가 바뀐다)."""
    ds = supported_dates()
    return f"죄송합니다. {ds[0]} ~ {ds[-1]} 범위의 날씨·물때 정보만 제공할 수 있어요."


DEMO_QUESTION = "11월 7일 B03 2명 예약해주세요. 이름은 홍길동, 전화번호는 010-1234-5678입니다."

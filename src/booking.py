"""booking.py - 선상 예약 사전검증(결정적). 도구와 HITL 미들웨어가 함께 쓴다"""
from src.constants import DATE_OUT_OF_RANGE
from src.data import boats_on, load_spots
from src.verdict import compute_verdict, valid_date


def precheck_booking(boat_id: str, date: str, people, name: str = "x", phone: str = "x") -> dict:
    """예약 사전검증(결정적). HITL 승인보다 먼저 호출한다.
    status: ok | missing_fields | date_out_of_range | boat_not_found | seats_insufficient | verdict_impossible"""
    if not (str(boat_id or "").strip() and str(date or "").strip() and people not in (None, "")
            and str(name or "").strip() and str(phone or "").strip()):
        return {"status": "missing_fields", "reason": "배ID·날짜·인원·이름·전화번호가 모두 필요합니다."}
    d = valid_date(date)
    if d is None:
        return {"status": "date_out_of_range", "reason": DATE_OUT_OF_RANGE}
    boat = next((b for b in boats_on(d) if b["boat_id"] == str(boat_id).strip().upper()), None)
    if boat is None:
        return {"status": "boat_not_found", "reason": f"해당 날짜에 {boat_id} 배편이 없어 예약할 수 없습니다."}
    try:
        n = int(people)
    except (TypeError, ValueError):
        return {"status": "missing_fields", "reason": "인원은 숫자여야 합니다."}
    if n < 1:
        return {"status": "missing_fields", "reason": "인원은 1명 이상이어야 합니다."}
    seats = int(boat["seats_left"])
    if n > seats:
        return {"status": "seats_insufficient",
                "reason": f"남은 자리({seats}석)보다 인원({n}명)이 많아 예약할 수 없습니다. 인원을 조정해 주세요."}
    spot = next(s for s in load_spots() if s["spot_id"] == boat["spot_id"])
    v = compute_verdict(spot, d, "선상")
    if v["verdict"] == "불가":
        return {"status": "verdict_impossible",
                "reason": f"{d} {spot['name']} 선상 판정이 '불가'라 예약할 수 없습니다."}
    return {"status": "ok", "reason": "", "boat": boat, "date": d, "people": n, "verdict": v["verdict"]}

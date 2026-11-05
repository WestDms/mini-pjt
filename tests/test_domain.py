"""test_domain.py - 판정·날짜·배편·예약 검증·포인트 해석(결정적 로직) 테스트. 기대값은 평가셋 메모와 같은 csv 기준이다."""
from src.booking import precheck_booking
from src.data import boats_on, load_spots
from src.spots import resolve_spot
from src.verdict import compute_verdict, valid_date


def spot(sid):
    """spot_id 로 포인트를 찾는다."""
    return next(s for s in load_spots() if s["spot_id"] == sid)


def test_valid_date_range():
    """지원 날짜는 csv 스냅샷의 11-06~08 이고, 비어 있으면 오늘, 범위 밖은 None."""
    assert valid_date("") == "2026-11-06"
    assert valid_date("2026-11-07") == "2026-11-07"
    assert valid_date("2026-11-09") is None


def test_verdicts_match_eval_notes():
    """평가셋 메모: 남해 11-07 방파제 3곳 가능, 학동갯바위 불가, 동명항방파제 주의."""
    for sid in ("S03", "S06", "S08"):
        assert compute_verdict(spot(sid), "2026-11-07", "방파제")["verdict"] == "가능"
    assert compute_verdict(spot("S01"), "2026-11-07", "갯바위")["verdict"] == "불가"
    assert compute_verdict(spot("E01"), "2026-11-07", "방파제")["verdict"] == "주의"


def test_missing_forecast_is_impossible():
    """예보가 없는 날짜는 안전하게 '불가'로 본다."""
    assert compute_verdict(spot("S03"), "2026-11-09", "방파제")["verdict"] == "불가"


def test_boats_dated_row_wins_over_daily():
    """같은 배에 날짜 지정 행이 있으면 그것을 쓰고(B04 11-07 남은 자리 0), 없으면 '매일' 행을 그 날짜로 쓴다."""
    b04 = [b for b in boats_on("2026-11-07") if b["boat_id"] == "B04"]
    assert len(b04) == 1 and b04[0]["seats_left"] == "0"
    b25 = [b for b in boats_on("2026-11-07") if b["boat_id"] == "B25"]
    assert b25 and b25[0]["date"] == "2026-11-07"


def test_precheck_booking_statuses():
    """예약 사전검증: 정상, 좌석 부족(평가 13번), 범위 밖 날짜, 필수 정보 누락."""
    assert precheck_booking("B03", "2026-11-07", 2, "홍길동", "010-1234-5678")["status"] == "ok"
    assert precheck_booking("B05", "2026-11-07", 10, "김철수", "010-9876-5432")["status"] == "seats_insufficient"
    assert precheck_booking("B03", "2026-11-20", 2, "a", "b")["status"] == "date_out_of_range"
    assert precheck_booking("B03", "2026-11-07", 2, "", "b")["status"] == "missing_fields"


def test_resolve_spot():
    """포인트 해석: 정확 일치, 지역 모호(거제 3곳), 없는 포인트."""
    assert resolve_spot("지세포방파제")["candidates"] == ["S03"]
    r = resolve_spot("거제")
    assert r["status"] == "ambiguous" and len(r["candidates"]) == 3
    assert resolve_spot("독도 동도 선착장")["status"] == "not_found"

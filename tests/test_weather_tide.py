"""test_weather_tide.py - 기상청 격자 변환·특보 문안 파싱·물때 계산 테스트(외부 API 호출 없음)."""
from datetime import date, datetime, timezone

from src import weather
from src.tide import lunar_day, phase_time, tide_name, tide_stage


def test_latlon_to_grid_seoul():
    """기상청 공식 예시: 서울(37.5665, 126.978) → 격자 (60, 127)."""
    assert weather.latlon_to_grid(37.5665, 126.978) == (60, 127)


def test_advisory_parsing(monkeypatch):
    """발효 특보(t6)는 세부·광역·'전해상' 이름으로, 예비특보(t7)는 해당 날짜만 풍랑주의보로 반영한다."""
    t6 = "o 풍랑주의보 : 서해중부앞바다, 제주도전해상\r\no 강풍주의보 : 강원북부앞바다"
    t7 = "(1) 풍랑 예비특보\r\no 11월 07일 오전(06시~12시) : 동해중부앞바다\r\n(2) 강풍 예비특보\r\no 11월 07일 : 경북북부앞바다"
    monkeypatch.setattr(weather, "_warnings", lambda: (t6, t7))
    monkeypatch.setattr(weather, "today", lambda: "2026-11-06")
    adv = lambda sid, d: weather._advisory({"spot_id": sid}, d)
    assert adv("W01", "2026-11-06")[0] == "풍랑주의보"          # 서해중부앞바다(광역)
    assert adv("J01", "2026-11-06")[0] == "풍랑주의보"          # 제주도전해상
    assert adv("E01", "2026-11-06")[0] == "없음"                # 강풍주의보는 판정에 쓰지 않음
    assert "예비특보" in adv("E02", "2026-11-07")[1]             # 동해중부앞바다 11-07 예비특보
    assert adv("E05", "2026-11-07")[0] == "없음"                # 강풍 예비특보는 반영하지 않음


def test_phase_times_match_eclipses():
    """삭·망 시각이 실제 일식·월식 시각과 5분 이내로 맞는다(날짜는 KST 기준)."""
    cases = [(date(2024, 4, 9), False, datetime(2024, 4, 8, 18, 21, tzinfo=timezone.utc)),
             (date(2025, 3, 14), True, datetime(2025, 3, 14, 6, 55, tzinfo=timezone.utc)),
             (date(2026, 8, 13), False, datetime(2026, 8, 12, 17, 37, tzinfo=timezone.utc))]
    for d, full, ref in cases:
        assert abs((phase_time(d, full) - ref).total_seconds()) < 300


def test_tide_names_match_real_table():
    """바다타임 남양항(울릉도) 2026년 7월 물때표(8물때식)와 일치한다."""
    ref = {1: "9물", 6: "14물", 7: "조금", 8: "1물", 14: "8물", 21: "조금", 28: "7물", 29: "8물"}
    for day, name in ref.items():
        assert tide_name(lunar_day(date(2026, 7, day)), 8) == name
    assert lunar_day(date(2026, 7, 14)) == 1


def test_tide_system_and_stage():
    """서해 7물때식은 조금 다음이 무시, 사리·조금 단계는 대조기·소조기 음력일로 정한다."""
    assert tide_name(8, 7) == "조금" and tide_name(9, 7) == "무시" and tide_name(1, 7) == "7물"
    assert tide_stage(1) == "사리" and tide_stage(16) == "사리"
    assert tide_stage(8) == "조금" and tide_stage(12) == "중물"

"""verdict.py - 날짜 검증과 출조 판정(criteria.json 임계값으로 결정적으로 계산)"""
import re

from src.data import LEVEL_RANK, load_criteria
from src.weather import get_forecast, supported_dates, today


def valid_date(date: str | None) -> str | None:
    """날짜 문자열을 검증한다. 비어 있으면 D0(TODAY), 지원 범위 밖이면 None. 기준일·범위는 weather 소스를 따른다."""
    if not date:
        return today()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(date))
    d = m.group(0) if m else str(date).strip()
    return d if d in supported_dates() else None


def compute_verdict(spot: dict, date: str, ftype: str) -> dict:
    """(포인트, 날짜, 낚시형태)의 판정을 criteria.json 으로 결정적으로 계산한다. 값은 weather 소스에서 받는다."""
    crit = load_criteria()
    f = get_forecast(spot, date)
    if f is None:
        return {"verdict": "불가", "reason": "예보 데이터 없음"}
    wind = float(f["wind_ms"])
    wave = None if f["wave_m"] is None else float(f["wave_m"])
    wave_cmp = 0.0 if wave is None else wave  # 파고를 못 구하면 풍속만 보고 아래에서 '주의' 이상으로 둔다
    th = crit["thresholds"][ftype]
    if wind <= th["가능"]["wind_max"] and wave_cmp <= th["가능"]["wave_max"]:
        level = "가능"
    elif wind <= th["주의"]["wind_max"] and wave_cmp <= th["주의"]["wave_max"]:
        level = "주의"
    else:
        level = "불가"
    notes = list(f.get("notes", []))
    if wave is None and level == "가능":
        level = "주의"
        notes.append("파고 정보 없음: 풍속만으로 판정해 '주의'로 둠")
    adv = crit["advisory_rules"].get(f["advisory"], {}).get(ftype)
    if adv and LEVEL_RANK[adv] > LEVEL_RANK[level]:
        level = adv
        notes.append(f"{f['advisory']} 적용")
    rule = crit["tide_rules"].get(f["tide"], {})
    if rule and ftype in rule.get("apply_to", []):
        down = rule["downgrade"].get(level)
        if down:
            level = down
            notes.append("사리 물때로 한 단계 하향")
    extra = {k: f[k] for k in ("advisory_label", "tide_name", "lunar_day", "tide_times", "source", "issued", "tide_basis")
             if k in f}  # kma 모드에서만 붙는 정보
    return {"verdict": level, "wind_ms": wind, "wave_m": wave, "advisory": f["advisory"],
            "tide": f["tide"], "notes": notes, **extra}

"""tide.py - 물때 계산과 조석예보

- 삭(신월)·망(보름) 시각을 천문 알고리즘(Meeus, Astronomical Algorithms 49장 주요 보정항)으로 구한다(오차 수 분 이내).
- 음력일은 '삭이 든 KST 날짜 = 1일'로 센다.
- 물때 이름은 서해 7물때식, 남해·동해·제주 8물때식으로 붙이고, 판정용 단계(사리/중물/조금)도 정한다.
- KHOA_SERVICE_KEY 가 있으면 국립해양조사원 조석예보(고조·저조 시각)를 가까운 조위관측소에서 가져온다(실패하면 생략).
"""
import math
import os
import threading
import time
from datetime import date as Date, datetime, timedelta, timezone

import httpx

KST = timezone(timedelta(hours=9))
KHOA_URL = "http://www.khoa.go.kr/api/oceangrid/tideObsPreTab/search.do"
# 조위관측소(코드, 이름, 위도, 경도) - 대략적 위치. 가장 가까운 곳을 쓴다
TIDE_STATIONS = [
    ("DT_0001", "인천", 37.451, 126.592), ("DT_0002", "평택", 36.967, 126.822), ("DT_0050", "태안", 36.913, 126.238),
    ("DT_0067", "안흥", 36.674, 126.129), ("DT_0025", "보령", 36.406, 126.486), ("DT_0051", "서천마량", 36.128, 126.495),
    ("DT_0018", "군산", 35.975, 126.563), ("DT_0068", "위도", 35.618, 126.301), ("DT_0007", "목포", 34.779, 126.375),
    ("DT_0035", "흑산도", 34.684, 125.435), ("DT_0027", "완도", 34.315, 126.759), ("DT_0026", "고흥발포", 34.481, 127.342),
    ("DT_0016", "여수", 34.747, 127.765), ("DT_0031", "거문도", 34.028, 127.308), ("DT_0014", "통영", 34.827, 128.434),
    ("DT_0029", "거제도", 34.801, 128.699), ("DT_0005", "부산", 35.096, 129.035), ("DT_0020", "울산", 35.501, 129.387),
    ("DT_0091", "포항", 36.051, 129.376), ("DT_0011", "후포", 36.677, 129.453), ("DT_0006", "묵호", 37.550, 129.116),
    ("DT_0012", "속초", 38.207, 128.594), ("DT_0013", "울릉도", 37.491, 130.913), ("DT_0004", "제주", 33.527, 126.543),
    ("DT_0010", "서귀포", 33.240, 126.561), ("DT_0022", "성산포", 33.474, 126.927), ("DT_0023", "모슬포", 33.214, 126.251),
    ("DT_0021", "추자도", 33.962, 126.300),
]
_cache: dict = {}
_lock = threading.Lock()


# ---------------------------------------------------------------- 삭·망 시각
def _phase_jde(k: float) -> float:
    """k(정수=삭, +0.5=망)에 해당하는 위상 시각(JDE, 역학시)을 구한다."""
    t = k / 1236.85
    jde = (2451550.09766 + 29.530588861 * k + 0.00015437 * t ** 2 - 0.000000150 * t ** 3
           + 0.00000000073 * t ** 4)
    e = 1 - 0.002516 * t - 0.0000074 * t ** 2
    r = math.radians
    m = r(2.5534 + 29.10535670 * k - 0.0000014 * t ** 2 - 0.00000011 * t ** 3)
    mp = r(201.5643 + 385.81693528 * k + 0.0107582 * t ** 2 + 0.00001238 * t ** 3 - 0.000000058 * t ** 4)
    f = r(160.7108 + 390.67050284 * k - 0.0016118 * t ** 2 - 0.00000227 * t ** 3 + 0.000000011 * t ** 4)
    om = r(124.7746 - 1.56375588 * k + 0.0020672 * t ** 2 + 0.00000215 * t ** 3)
    full = abs(k - math.floor(k) - 0.5) < 1e-9
    c = [-0.40614, 0.17302, 0.01614, 0.01043, 0.00734, -0.00515, 0.00209] if full else \
        [-0.40720, 0.17241, 0.01608, 0.01039, 0.00739, -0.00514, 0.00208]
    corr = (c[0] * math.sin(mp) + c[1] * e * math.sin(m) + c[2] * math.sin(2 * mp) + c[3] * math.sin(2 * f)
            + c[4] * e * math.sin(mp - m) + c[5] * e * math.sin(mp + m) + c[6] * e * e * math.sin(2 * m)
            - 0.00111 * math.sin(mp - 2 * f) - 0.00057 * math.sin(mp + 2 * f) + 0.00056 * e * math.sin(2 * mp + m)
            - 0.00042 * math.sin(3 * mp) + 0.00042 * e * math.sin(m + 2 * f) + 0.00038 * e * math.sin(m - 2 * f)
            - 0.00024 * e * math.sin(2 * mp - m) - 0.00017 * math.sin(om))
    return jde + corr


def _jde_to_utc(jde: float) -> datetime:
    """JDE 를 UTC 시각으로 바꾼다(ΔT 약 69초 보정)."""
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(days=jde - 2440587.5, seconds=-69)


def phase_time(d: Date, full: bool = False) -> datetime:
    """d 이전(포함)에 가장 가까운 삭(full=False) 또는 망(full=True)의 UTC 시각."""
    k = math.floor((d.year + (d.timetuple().tm_yday - 1) / 365.25 - 2000) * 12.3685) + (0.5 if full else 0)
    end = datetime(d.year, d.month, d.day, 23, 59, tzinfo=KST)
    while _jde_to_utc(_phase_jde(k)) > end:
        k -= 1
    while _jde_to_utc(_phase_jde(k + 1)) <= end:
        k += 1
    return _jde_to_utc(_phase_jde(k))


def new_moons(year: int) -> list[datetime]:
    """그 해의 삭 시각(UTC) 목록(검증·테스트용)."""
    out, k = [], math.floor((year - 2000) * 12.3685) - 1
    while True:
        t = _jde_to_utc(_phase_jde(k))
        if t.year > year:
            return out
        if t.year == year:
            out.append(t)
        k += 1


# ---------------------------------------------------------------- 음력일·물때
def lunar_day(d: Date) -> int:
    """음력 날짜의 '일'(1~30). 삭이 든 KST 날짜를 1일로 센다."""
    nm = phase_time(d).astimezone(KST).date()
    return (d - nm).days + 1


def tide_name(lday: int, system: int) -> str:
    """음력일 → 물때 이름. 두 방식 모두 조금은 음력 8·23일이다.
    7물때식(서해): 조금→무시→1물(10·25일)…7물(1·16일)…13물(7·22일).
    8물때식(남해·동해·제주): 조금→1물(9·24일)…8물(1·16일)…14물(7·22일). (바다타임 물때표로 대조함)"""
    off = (lday - 8) % 15  # 0=조금
    if off == 0:
        return "조금"
    if system == 7:
        return "무시" if off == 1 else f"{off - 1}물"
    return f"{off}물"


def tide_stage(lday: int) -> str:
    """판정용 물때 단계. 대조기(음력 30·1~3일, 15~18일)=사리, 소조기(7~10일, 22~25일)=조금, 그 밖=중물."""
    if lday in (30, 1, 2, 3, 15, 16, 17, 18):
        return "사리"
    if lday in (7, 8, 9, 10, 22, 23, 24, 25):
        return "조금"
    return "중물"


# ---------------------------------------------------------------- 조석예보(국립해양조사원, 선택)
def nearest_station(lat: float, lon: float) -> tuple[str, str]:
    """가장 가까운 조위관측소 (코드, 이름)."""
    best = min(TIDE_STATIONS, key=lambda s: math.hypot(s[2] - lat, (s[3] - lon) * math.cos(math.radians(lat))))
    return best[0], best[1]


def tide_times(lat: float, lon: float, d: str) -> dict | None:
    """KHOA_SERVICE_KEY 가 있으면 가까운 관측소의 그날 고조·저조 시각을 돌려준다. 키가 없거나 실패하면 None."""
    key = os.getenv("KHOA_SERVICE_KEY", "").strip()
    if not key:
        return None
    code, name = nearest_station(lat, lon)
    ck = (code, d)
    with _lock:
        hit = _cache.get(ck)
        if hit and time.time() - hit[0] < 6 * 3600:
            return hit[1]
    try:
        r = httpx.get(KHOA_URL, params={"ServiceKey": key, "ObsCode": code, "Date": d.replace("-", ""),
                                        "ResultType": "json"}, timeout=float(os.getenv("KHOA_TIMEOUT_SEC", "5")))
        r.raise_for_status()
        data = r.json().get("result", {}).get("data") or []
        times = [{"type": x.get("hl_code"), "time": str(x.get("tph_time", ""))[11:16], "level_cm": x.get("tph_level")}
                 for x in data]
        val = {"station": name, "times": times} if times else None
    except Exception:  # 키 오류·응답 형식 차이 등은 물때 이름만으로 안내한다
        val = None
    with _lock:
        _cache[ck] = (time.time(), val)
    return val


def tide_info(region: str, lat: float, lon: float, d: str) -> dict:
    """판정·안내용 물때 정보: tide(사리/중물/조금), tide_name(예: 7물), lunar_day, tide_basis, (있으면) tide_times."""
    day = datetime.strptime(d, "%Y-%m-%d").date()
    lday = lunar_day(day)
    system = 7 if region == "서해" else 8
    out = {"tide": tide_stage(lday), "tide_name": tide_name(lday, system), "lunar_day": lday,
           "tide_basis": f"천문 계산(삭망 시각) 기반 음력 {lday}일·{system}물때식"}
    tt = tide_times(lat, lon, d)
    if tt:
        out["tide_times"] = tt
        out["tide_basis"] += f", 만조·간조는 국립해양조사원 {tt['station']} 조석예보"
    return out

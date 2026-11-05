"""weather.py - 날씨·물때 데이터 소스. 판정 규칙(criteria.json)은 모르고, 판정에 쓸 값만 돌려준다.

WEATHER_SOURCE=csv (기본) : data/forecast.csv 고정 스냅샷. 평가·시연 재현용
WEATHER_SOURCE=kma        : 기상청 공공데이터 API 실시간 조회
  - 풍속(WSD)·파고(WAV) : 단기예보 getVilageFcst (격자 nx, ny)
  - 특보                : 기상특보 getPwnStatus 의 발효 중 특보(t6)와 해당 날짜 예비특보(t7)를 SPOT_GEO 해상구역 이름으로 매칭
  - 물때                : src/tide.py (정밀 삭망 계산 기반 물때 이름·단계, 선택적으로 국립해양조사원 만조·간조)
"""
import csv
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

import httpx
from dotenv import load_dotenv

from src.tide import tide_info

load_dotenv()

DATA = Path(__file__).resolve().parent.parent / "data"
KST = timezone(timedelta(hours=9))
FCST_URL = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
WRN_URL = "http://apis.data.go.kr/1360000/WthrWrnInfoService/getPwnStatus"
DAY_HOURS = range(6, 19)  # 하루 대표값은 06~18시 최댓값(낮 출조 기준)
WAVE_OFFSETS = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
# 기상청 특보명 → criteria.json 특보 키. 앞에 있을수록 나쁜 단계
WARN_MAP = {"태풍경보": "태풍경보", "풍랑경보": "풍랑경보", "태풍주의보": "풍랑주의보", "풍랑주의보": "풍랑주의보"}
WARN_ORDER = ["태풍경보", "풍랑경보", "풍랑주의보"]
# 포인트 위치(대략적 위경도)와 특보 해상구역. spots.csv 는 불변 파일이라 여기에 둔다. 구역은 세부→광역 순
SPOT_GEO = {
    "W01": (36.676, 126.135, "충남북부앞바다;서해중부앞바다"),
    "W02": (36.630, 126.280, "충남북부앞바다;서해중부앞바다"),
    "W03": (36.437, 126.518, "충남남부앞바다;서해중부앞바다"),
    "W04": (36.331, 126.509, "충남남부앞바다;서해중부앞바다"),
    "W05": (35.622, 126.468, "전북남부앞바다;서해남부앞바다"),
    "W06": (37.250, 126.470, "인천·경기남부앞바다;서해중부앞바다"),
    "W07": (35.615, 126.300, "전북남부앞바다;서해남부앞바다"),
    "S01": (34.770, 128.640, "거제시동부앞바다;남해동부앞바다"),
    "S02": (34.716, 128.596, "거제시동부앞바다;남해동부앞바다"),
    "S03": (34.832, 128.710, "거제시동부앞바다;남해동부앞바다"),
    "S04": (34.770, 128.390, "경남중부남해앞바다;남해동부앞바다"),
    "S05": (34.630, 128.380, "경남중부남해앞바다;남해동부앞바다"),
    "S06": (34.713, 128.047, "경남서부남해앞바다;남해동부앞바다"),
    "S07": (34.595, 127.800, "전남동부남해앞바다;남해서부앞바다"),
    "S08": (35.047, 128.963, "부산앞바다;남해동부앞바다"),
    "E01": (38.211, 128.600, "강원북부앞바다;동해중부앞바다"),
    "E02": (37.893, 128.830, "강원중부앞바다;동해중부앞바다"),
    "E03": (37.837, 128.877, "강원중부앞바다;동해중부앞바다"),
    "E04": (37.287, 129.315, "강원남부앞바다;동해중부앞바다"),
    "E05": (36.680, 129.455, "경북북부앞바다;동해남부앞바다"),
    "E06": (35.990, 129.557, "경북남부앞바다;동해남부앞바다"),
    "E07": (35.806, 129.506, "경북남부앞바다;동해남부앞바다"),
    "J01": (33.465, 126.310, "제주도북부앞바다;제주도앞바다"),
    "J02": (33.310, 126.145, "제주도서부앞바다;제주도앞바다"),
    "J03": (33.217, 126.664, "제주도남부앞바다;제주도앞바다"),
    "J04": (33.475, 126.932, "제주도동부앞바다;제주도앞바다"),
    "J05": (33.230, 126.596, "제주도남부앞바다;제주도앞바다"),
    "J06": (33.416, 126.255, "제주도서부앞바다;제주도앞바다"),
    "J07": (33.214, 126.252, "제주도서부앞바다;제주도앞바다"),
    "W08": (37.447, 126.372, "인천·경기남부앞바다;서해중부앞바다"),
    "W09": (37.310, 126.610, "인천·경기남부앞바다;서해중부앞바다"),
    "W10": (37.116, 126.680, "인천·경기남부앞바다;서해중부앞바다"),
    "W11": (36.130, 126.500, "충남남부앞바다;서해중부앞바다"),
    "W12": (36.460, 126.330, "충남북부앞바다;서해중부앞바다"),
    "W13": (35.940, 126.530, "전북북부앞바다;서해남부앞바다"),
    "W14": (35.810, 126.410, "전북북부앞바다;서해남부앞바다"),
    "W15": (34.810, 126.370, "전남북부서해앞바다;서해남부앞바다"),
    "W16": (34.070, 125.120, "전남남부서해앞바다;서해남부앞바다"),
    "S09": (34.030, 127.310, "전남동부남해앞바다;남해서부앞바다"),
    "S10": (34.310, 126.760, "전남중부남해앞바다;남해서부앞바다"),
    "S11": (34.460, 127.450, "전남동부남해앞바다;남해서부앞바다"),
    "S12": (34.925, 128.070, "경남서부남해앞바다;남해동부앞바다"),
    "S13": (34.630, 128.260, "경남중부남해앞바다;남해동부앞바다"),
    "S14": (34.640, 128.550, "경남중부남해앞바다;남해동부앞바다"),
    "S15": (35.020, 128.830, "부산앞바다;남해동부앞바다"),
    "S16": (35.230, 129.230, "부산앞바다;남해동부앞바다"),
    "S17": (35.100, 129.120, "부산앞바다;남해동부앞바다"),
    "E08": (37.500, 130.910, "동해중부안쪽먼바다;동해중부바깥먼바다;동해중부먼바다"),
    "E09": (37.480, 130.880, "동해중부안쪽먼바다;동해중부바깥먼바다;동해중부먼바다"),
    "E10": (38.270, 128.560, "강원북부앞바다;동해중부앞바다"),
    "E11": (37.950, 128.790, "강원중부앞바다;동해중부앞바다"),
    "E12": (37.550, 129.120, "강원남부앞바다;동해중부앞바다"),
    "E13": (37.060, 129.420, "경북북부앞바다;동해남부앞바다"),
    "E14": (36.360, 129.390, "경북북부앞바다;동해남부앞바다"),
    "E15": (35.880, 129.530, "경북남부앞바다;동해남부앞바다"),
    "E16": (35.360, 129.360, "울산앞바다;동해남부앞바다"),
    "J08": (33.560, 126.750, "제주도북부앞바다;제주도앞바다"),
    "J09": (33.500, 126.950, "제주도동부앞바다;제주도앞바다"),
    "J10": (33.120, 126.270, "제주도남부앞바다;제주도앞바다"),
    "J11": (33.270, 126.660, "제주도남부앞바다;제주도앞바다"),
    "J12": (33.960, 126.300, "제주도북부앞바다;제주도앞바다"),
    "J13": (33.510, 126.470, "제주도북부앞바다;제주도앞바다"),
}


class WeatherError(Exception):
    """실시간 날씨 조회 실패. 도구 예외로 올라가 ERROR_FIXED 로 응답된다."""


# ---------------------------------------------------------------- 소스·날짜
def source() -> str:
    """현재 날씨 소스(csv | kma)를 돌려준다. .env 를 읽은 뒤 판단하도록 매번 환경변수를 본다."""
    return os.getenv("WEATHER_SOURCE", "csv").strip().lower()


@lru_cache(maxsize=1)
def _criteria() -> dict:
    """criteria.json 을 읽는다(csv 모드의 기준일·지원 날짜용)."""
    return json.load(open(DATA / "criteria.json", encoding="utf-8"))


def today() -> str:
    """오늘(D0) 날짜. csv 는 TODAY 환경변수 또는 criteria.json 의 today, kma 는 실제 KST 오늘."""
    if source() == "kma":
        return datetime.now(KST).strftime("%Y-%m-%d")
    return os.getenv("TODAY") or _criteria()["today"]


def supported_dates() -> list[str]:
    """지원 날짜 목록. csv 는 criteria.json 의 supported_dates, kma 는 오늘~글피(D0~D+3, 단기예보 제공 범위)."""
    if source() == "kma":
        d0 = datetime.strptime(today(), "%Y-%m-%d")
        return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)]
    return list(_criteria()["supported_dates"])


# ---------------------------------------------------------------- 공용 진입점
def get_forecast(spot: dict, date: str) -> dict | None:
    """(포인트, 날짜)의 wind_ms, wave_m, advisory, tide 를 돌려준다. 데이터가 없으면 None.
    kma 모드는 source, issued, tide_basis, notes 를 더 담고, 파고를 못 구하면 wave_m 이 None 이다."""
    if source() == "kma":
        return _kma_forecast(spot, date)
    row = _csv_forecast().get((spot["spot_id"], date))
    if row is None:
        return None
    return {"wind_ms": float(row["wind_ms"]), "wave_m": float(row["wave_m"]),
            "advisory": row["advisory"], "tide": row["tide"]}


def prefetch(spots: list[dict], date: str) -> None:
    """kma 모드에서 여러 포인트를 병렬로 미리 조회해 캐시에 채운다. 실패는 무시한다(본 조회에서 다시 드러난다)."""
    if source() != "kma" or not spots:
        return

    def one(s):
        try:
            _kma_forecast(s, date)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, spots))


# ---------------------------------------------------------------- csv 소스
@lru_cache(maxsize=1)
def _csv_forecast() -> dict:
    """forecast.csv 를 {(spot_id, date): 행} 으로 읽는다."""
    rows = csv.DictReader(open(DATA / "forecast.csv", encoding="utf-8-sig"))
    return {(r["spot_id"], r["date"]): r for r in rows}


# ---------------------------------------------------------------- kma 소스
_cache: dict = {}
_cache_lock = threading.Lock()
_wave_offset: dict = {}  # spot_id → 파고가 나온 격자 오프셋(재탐색 방지)


def _cached(key, ttl: int, load):
    """키별로 ttl 초 동안 결과를 캐시한다."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = load()
    with _cache_lock:
        _cache[key] = (now, val)
    return val


def _kma_get(url: str, params: dict) -> list[dict]:
    """공공데이터포털 기상청 API 를 호출해 item 목록을 돌려준다. NO_DATA 는 빈 목록."""
    key = os.getenv("KMA_SERVICE_KEY", "").strip()
    if not key:
        raise WeatherError("KMA_SERVICE_KEY 가 .env 에 없습니다")
    # 포털의 Encoding 키를 넣어도 이중 인코딩되지 않도록 한 번 풀어서 넘긴다
    q = {"serviceKey": unquote(key), "dataType": "JSON", "pageNo": 1, **params}
    try:
        r = httpx.get(url, params=q, timeout=float(os.getenv("KMA_TIMEOUT_SEC", "5")))
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise WeatherError(f"기상청 API 호출 실패: {e!r}") from e
    try:
        resp = r.json()["response"]
    except (ValueError, KeyError) as e:  # 인증 오류 등은 XML 로 온다
        raise WeatherError(f"기상청 API 응답 오류: {r.text[:200]}") from e
    code = resp["header"]["resultCode"]
    if code == "03":
        return []
    if code != "00":
        raise WeatherError(f"기상청 API 오류 {code}: {resp['header'].get('resultMsg')}")
    items = resp.get("body", {}).get("items")
    return items.get("item", []) if isinstance(items, dict) else []


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도를 기상청 단기예보 격자(nx, ny)로 바꾼다(람베르트 정각원추도법, 기상청 공식 파라미터)."""
    re_, grid, slat1, slat2, olon, olat, xo, yo = 6371.00877, 5.0, 30.0, 60.0, 126.0, 38.0, 43, 136
    rad = math.pi / 180.0
    re_ /= grid
    slat1, slat2, olon, olat = slat1 * rad, slat2 * rad, olon * rad, olat * rad
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(
        math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5))
    sf = math.pow(math.tan(math.pi * 0.25 + slat1 * 0.5), sn) * math.cos(slat1) / sn
    ro = re_ * sf / math.pow(math.tan(math.pi * 0.25 + olat * 0.5), sn)
    ra = re_ * sf / math.pow(math.tan(math.pi * 0.25 + lat * rad * 0.5), sn)
    theta = lon * rad - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn
    return int(math.floor(ra * math.sin(theta) + xo + 0.5)), int(math.floor(ro - ra * math.cos(theta) + yo + 0.5))


def _latest_base(now: datetime) -> tuple[str, str]:
    """지금 받을 수 있는 가장 최근 단기예보 발표(02·05·…·23시, 발표 후 약 10분 뒤 제공)를 고른다."""
    t = now - timedelta(minutes=15)
    for h in (23, 20, 17, 14, 11, 8, 5, 2):
        if t.hour >= h:
            return t.strftime("%Y%m%d"), f"{h:02d}00"
    return (t - timedelta(days=1)).strftime("%Y%m%d"), "2300"


def _grid_fcst(nx: int, ny: int) -> dict:
    """한 격자의 단기예보를 {날짜: {WSD|WAV: {시: 값}}} 으로 돌려준다. 발표 회차별로 캐시한다."""
    base_date, base_time = _latest_base(datetime.now(KST))

    def load():
        items = _kma_get(FCST_URL, {"numOfRows": 1500, "base_date": base_date, "base_time": base_time,
                                    "nx": nx, "ny": ny})
        by_date: dict = {}
        for it in items:
            if it.get("category") not in ("WSD", "WAV"):
                continue
            try:
                v = float(it["fcstValue"])
            except (TypeError, ValueError):
                continue
            if v <= -90:  # 결측(-999 등)
                continue
            fd = it["fcstDate"]
            d = f"{fd[:4]}-{fd[4:6]}-{fd[6:8]}"
            by_date.setdefault(d, {}).setdefault(it["category"], {})[int(it["fcstTime"][:2])] = v
        return {"by_date": by_date, "issued": f"{base_date} {base_time}"}

    return _cached(("fcst", nx, ny, base_date, base_time), int(os.getenv("KMA_CACHE_TTL_SEC", "1800")), load)


def _daily_max(g: dict, date: str, cat: str) -> float | None:
    """해당 날짜 06~18시 최댓값. 그 시간대가 이미 지나 없으면 남은 시간 전체의 최댓값."""
    hours = g["by_date"].get(date, {}).get(cat, {})
    day = [v for h, v in hours.items() if h in DAY_HOURS]
    vals = day or list(hours.values())
    return max(vals) if vals else None


def _wave(spot: dict, nx: int, ny: int, date: str) -> float | None:
    """파고. WAV 는 바다 격자에만 있으므로 포인트 격자에 없으면 주변 8격자를 차례로 찾는다."""
    sid = spot["spot_id"]
    offsets = [_wave_offset[sid]] if sid in _wave_offset else WAVE_OFFSETS
    for dx, dy in offsets:
        g = _grid_fcst(nx + dx, ny + dy)
        if any(day.get("WAV") for day in g["by_date"].values()):
            _wave_offset[sid] = (dx, dy)
            return _daily_max(g, date, "WAV")
    return None


def _norm(s: str) -> str:
    """특보 구역명 비교용 정규화(공백·가운뎃점 제거)."""
    return re.sub(r"[\s·ㆍ.]", "", s)


def _warnings() -> tuple[str, str]:
    """특보 문안을 10분 캐시로 가져온다. (발효 중 특보 t6, 예비특보 t7)"""
    def load():
        items = _kma_get(WRN_URL, {"numOfRows": 10})
        return ("\n".join(str(it.get("t6") or "") for it in items),
                "\n".join(str(it.get("t7") or "") for it in items))
    return _cached(("wrn",), 600, load)


def _spot_zones(spot: dict) -> list[str]:
    """포인트의 해상구역 이름 목록(정규화). '제주도전해상'처럼 광역 전체를 묶어 쓰는 표현도 포함한다."""
    zones = [_norm(z) for z in SPOT_GEO[spot["spot_id"]][2].split(";") if z]
    return zones + [z.replace("앞바다", "전해상") for z in zones if z.endswith("앞바다")]


def _preliminary(t7: str, zones: list[str], date: str) -> bool:
    """예비특보(t7) 중 풍랑·태풍 항목이 해당 날짜에 포인트 구역을 포함하는지 본다.
    t7 형식: '(1) 풍랑 예비특보\\no 11월 06일 오전(06시~12시) : 구역, 구역\\n(2) 강풍 예비특보 ...'"""
    month, day = int(date[5:7]), int(date[8:10])
    for sec in re.split(r"\(\d+\)", t7):
        title = sec.strip().split("\n", 1)[0]
        if "예비특보" not in title or not ("풍랑" in title or "태풍" in title):
            continue
        for line in re.split(r"(?:^|\s)o\s+", sec):
            head, _, body = line.partition(":")
            m = re.search(r"(\d{1,2})월\s*(\d{1,2})일", head)
            if m and (int(m.group(1)), int(m.group(2))) == (month, day) and any(z in _norm(body) for z in zones):
                return True
    return False


def _advisory(spot: dict, date: str) -> tuple[str, str, list[str]]:
    """포인트에 적용할 특보(criteria 키, 없으면 '없음'), 사용자에게 보여줄 특보 상태 문구, 근거 메모를 돌려준다.
    발효 중 특보는 날짜와 무관하게 보수적으로 적용하고, 해당 날짜의 풍랑·태풍 예비특보는 '풍랑주의보'로 본다."""
    t6, t7 = _warnings()
    zones = _spot_zones(spot)
    found, notes = set(), []
    for seg in re.split(r"(?:^|\s)o\s+", t6):
        head, _, body = seg.partition(":")
        warn = WARN_MAP.get(head.strip())
        if warn and any(z in _norm(body) for z in zones):
            found.add(warn)
    current = next((w for w in WARN_ORDER if w in found), None)
    label = f"{current} 발효 중" if current else "없음"
    if current and date != today():
        notes.append("현재 발효 중인 특보를 보수적으로 적용")
        label = f"{current} 현재 발효 중({date} 해제 여부 미확정, 보수적으로 반영)"
    if _preliminary(t7, zones, date):
        if not current:
            notes.append(f"{date} 풍랑 예비특보를 풍랑주의보로 반영")
            label = f"{date} 풍랑 예비특보(발효 예상, 풍랑주의보로 반영)"
        found.add("풍랑주의보")
    return next((w for w in WARN_ORDER if w in found), "없음"), label, notes


def _kma_forecast(spot: dict, date: str) -> dict | None:
    """기상청 API 로 (포인트, 날짜) 값을 만든다. 풍속 조회 실패는 WeatherError, 특보 조회 실패는 notes 로 남긴다."""
    lat, lon, _ = SPOT_GEO[spot["spot_id"]]
    nx, ny = latlon_to_grid(lat, lon)
    g = _grid_fcst(nx, ny)
    wind = _daily_max(g, date, "WSD")
    if wind is None:
        return None
    try:
        adv, label, notes = _advisory(spot, date)
    except WeatherError:
        adv, label, notes = "없음", "특보 조회 실패(미반영)", ["특보 조회 실패로 특보 미반영"]
    # 물때: 정밀 삭망 계산 기반 음력일 → 물때 이름·단계, 키가 있으면 만조·간조 시각(src/tide.py)
    return {"wind_ms": wind, "wave_m": _wave(spot, nx, ny, date), "advisory": adv, "advisory_label": label,
            **tide_info(spot["region"], lat, lon, date),
            "source": "기상청 단기예보(실시간)", "issued": g["issued"], "notes": notes}

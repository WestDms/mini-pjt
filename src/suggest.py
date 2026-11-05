"""suggest.py - 요청한 데이터가 없을 때 데이터에 있는 대안(포인트·추천 조건 완화·배편·규정 어종)을 찾는다"""
import difflib
import re

from src.chunker import load_chunks
from src.data import LEVEL_RANK, boats_on, load_spots
from src.verdict import compute_verdict
from src.weather import prefetch, supported_dates

REGIONS = ["서해", "남해", "동해", "제주"]
# 질문에 나온 지명으로 권역을 짐작하기 위한 단서
REGION_HINTS = {
    "서해": ["서해", "인천", "경기", "충남", "태안", "보령", "서천", "전북", "군산", "부안", "목포", "신안", "화성",
             "시흥", "안산", "당진", "영광", "대부도", "제부도", "백령", "연평", "을왕리", "영흥"],
    "남해": ["남해", "경남", "거제", "통영", "사천", "여수", "전남", "완도", "고흥", "부산", "진해", "창원", "마산",
             "순천", "광양", "진도", "해남", "강진", "장흥", "거문도"],
    "동해": ["동해", "강원", "속초", "강릉", "고성", "양양", "삼척", "경북", "울진", "영덕", "포항", "경주", "울산",
             "울릉", "독도", "주문진"],
    "제주": ["제주", "서귀포", "한림", "애월", "성산", "모슬포", "우도", "마라도", "추자"],
}
REG_QUESTION = ("금어기", "체장", "cm", "센티", "크기", "몇", "포획", "잡아도", "가져")


def _brief(s: dict) -> dict:
    """대안 포인트 요약(판정은 넣지 않는다. 원하면 check_condition 으로 확인한다)."""
    return {"spot_id": s["spot_id"], "name": s["name"], "region": s["region"], "area": s["area"],
            "types": s["types"]}


def infer_region(text: str) -> str | None:
    """질문 속 지명으로 권역(서해|남해|동해|제주)을 짐작한다. 단서가 없으면 None."""
    t = re.sub(r"\s+", "", text or "")
    scores = {r: sum(1 for k in ks if k in t) for r, ks in REGION_HINTS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else None


def in_season(spot: dict, species: str, month: int) -> bool:
    """포인트에 해당 어종이 있고 그 달이 시즌이면 True."""
    hit = [sp for sp in spot["season"] if species in sp or sp in species]
    if not hit:
        return False
    a, b = spot["season"][hit[0]]
    return (a <= month <= b) if a <= b else (month >= a or month <= b)


def filter_spots(date: str, species: str = "", region: str = "", ftype: str = "") -> list[dict]:
    """어종(시즌)·권역·낚시형태 조건에 맞는 포인트 목록."""
    month = int(date[5:7])
    return [s for s in load_spots()
            if (not region or s["region"] == region) and (not ftype or ftype in s["types"])
            and (not species or in_season(s, species, month))]


def rank_spots(spots: list[dict], date: str, ftype: str = "") -> list[dict]:
    """판정이 '불가'인 곳을 빼고 가능>주의, 동률은 spot_id 순으로 정렬해 판정과 함께 돌려준다."""
    prefetch(spots, date)
    out = []
    for s in spots:
        t = ftype if ftype in s["types"] else s["default_type"]
        v = compute_verdict(s, date, t)
        if v["verdict"] != "불가":
            out.append({"spot_id": s["spot_id"], "name": s["name"], "region": s["region"], "area": s["area"],
                        "fishing_type": t, **v})
    out.sort(key=lambda r: (LEVEL_RANK[r["verdict"]], r["spot_id"]))
    return out


AREA_ALIAS = {"독도": "울릉"}  # 데이터에 없는 지명 → 가장 가까운 포인트 지역명
TYPE_WORDS = r"방파제|갯바위|선착장|좌대|선상|낚시|포구|부두"


def spot_alternatives(query: str, date: str, n: int = 3) -> dict:
    """없는 포인트 대신 제안할 포인트. ①이름이 비슷한 포인트 ②질문 지명과 지역(area)이 같은 포인트
    ③짐작한 권역에서 판정이 나쁘지 않은 포인트 ④권역을 모르면 권역별 1곳씩."""
    spots = load_spots()
    q = re.sub(r"\s+", "", query or "")
    for k, v in AREA_ALIAS.items():
        q = q.replace(k, k + v)
    core = re.sub(TYPE_WORDS, "", q)  # '방파제' 같은 형태 단어 때문에 엉뚱한 이름이 비슷하게 잡히지 않게 뺀다
    cores = {re.sub(TYPE_WORDS, "", s["name"]) or s["name"]: s for s in spots}
    close = difflib.get_close_matches(core, list(cores), n=n, cutoff=0.6) if core else []
    picks = [cores[c] for c in close]
    region = infer_region(q) or (picks[0]["region"] if picks else None)
    if region:
        pool = [s for s in spots if s["region"] == region]
        local = [s for s in pool if any(len(w) >= 2 and w in q for w in s["area"].split())]
        # 같은 지역(area) 포인트는 판정과 무관하게 먼저 보여주고, 같은 권역은 '불가'를 뺀 곳만 덧붙인다
        local.sort(key=lambda s: s["spot_id"])
        picks += [s for s in local if s not in picks]
        ranked = rank_spots(pool, date)
        picks += [s for r in ranked for s in spots if s["spot_id"] == r["spot_id"] and s not in picks]
    else:
        for reg in REGIONS:
            ranked = rank_spots([s for s in spots if s["region"] == reg], date)
            if ranked:
                picks += [s for s in spots if s["spot_id"] == ranked[0]["spot_id"] and s not in picks]
    limit = n if region else len(REGIONS)
    return {"inferred_region": region, "suggestions": [_brief(s) for s in picks[:limit]]}


def relaxed_recommendations(date: str, species: str, region: str, ftype: str, n: int = 3) -> dict | None:
    """추천 결과가 0건일 때 조건을 하나씩 풀어 다시 찾는다. 찾으면 {relaxed, recommendations}, 없으면 None."""
    tries = []
    if region:
        tries.append(("권역 조건을 빼고 다른 권역에서 찾았습니다", dict(species=species, region="", ftype=ftype)))
    if ftype:
        tries.append(("낚시 형태 조건을 빼고 찾았습니다", dict(species=species, region=region, ftype="")))
    if species:
        tries.append((f"{species} 조건을 빼고 같은 권역에서 찾았습니다", dict(species="", region=region, ftype=ftype)))
    tries.append(("조건을 모두 빼고 찾았습니다", dict(species="", region="", ftype="")))
    for why, cond in tries:
        ranked = rank_spots(filter_spots(date, **cond), date, cond["ftype"])
        if ranked:
            return {"relaxed": why, "recommendations": ranked[:n]}
    return None


def boat_alternatives(date: str, spot_ids: set | None, species: str, n: int = 5) -> list[dict]:
    """배편이 없을 때: ①같은 포인트의 다른 날짜 ②같은 권역(포인트 지정 없으면 같은 어종)의 같은 날짜 배편."""
    region_of = {s["spot_id"]: s["region"] for s in load_spots()}
    regions = {region_of[i] for i in (spot_ids or set())}
    keys = ("boat_id", "boat_name", "port", "spot_id", "date", "species", "seats_left", "price")
    alts = []
    if spot_ids:
        for d in supported_dates():
            if d == date:
                continue
            for b in boats_on(d):
                if b["spot_id"] in spot_ids and int(b["seats_left"]) > 0:
                    alts.append({"why": "같은 포인트 다른 날짜", **{k: b[k] for k in keys}})
    for b in boats_on(date):
        if int(b["seats_left"]) <= 0 or (spot_ids and b["spot_id"] in spot_ids):
            continue
        if (regions and region_of.get(b["spot_id"]) in regions) or (not regions and (not species or species in b["species"])):
            alts.append({"why": "같은 권역 같은 날짜" if regions else "같은 날짜 다른 배편", **{k: b[k] for k in keys}})
    if not alts:  # 어종이 맞는 배가 없으면 같은 날짜의 다른 어종 배편이라도 보여준다
        alts = [{"why": "같은 날짜 다른 어종 배편", **{k: b[k] for k in keys}}
                for b in boats_on(date) if int(b["seats_left"]) > 0]
    return alts[:n]


def regulation_species() -> list[str]:
    """금어기·체장 문서에 있는 어종 이름(괄호 속 별칭 포함)."""
    names = []
    for c in load_chunks():
        if "금어기" in c["doc_id"] or "체장" in c["doc_id"]:
            m = re.search(r"^## (.+)$", c["text"], re.M)
            if m:
                for tok in re.split(r"[(),\s]+", m.group(1)):
                    tok = tok.strip().removesuffix("류")
                    if tok and tok not in names:
                        names.append(tok)
    return names


def regulation_gap(query: str) -> str | None:
    """어종 규정 질문인데 문서에 있는 어종이 질문에 하나도 없으면, 문서에 있는 어종 목록 안내문을 돌려준다."""
    if not any(k in (query or "") for k in REG_QUESTION):
        return None
    names = regulation_species()
    if any(n in query for n in names):
        return None
    return ("[안내] 질문한 어종은 규정 문서에 없습니다. 수치를 지어내지 말고, 문서에 있는 어종 중에서 물어보도록 권하라. "
            "문서에 있는 어종: " + ", ".join(names))

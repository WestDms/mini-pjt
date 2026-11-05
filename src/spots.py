"""spots.py - 자유 텍스트 포인트명을 spots.csv 포인트로 해석하고, 도·시군구·키워드로 포인트 목록을 검색한다"""
import re
from functools import lru_cache

from src.data import load_boats, load_spots

REGION_WORDS = ("서해", "남해", "동해", "제주")
# 정식 명칭 외에 흔히 쓰는 도 이름
PROVINCE_ALIASES = {"강원특별자치도": ["강원도", "강원"], "전북특별자치도": ["전라북도", "전북도", "전북"],
                    "제주특별자치도": ["제주도", "제주"], "충청남도": ["충남"], "전라남도": ["전남"], "경상남도": ["경남"],
                    "경상북도": ["경북"], "경기도": ["경기"], "인천광역시": ["인천"], "부산광역시": ["부산"],
                    "울산광역시": ["울산"]}
SEARCH_STOPWORDS = {"포인트", "낚시", "낚시터", "곳", "어디", "추천", "목록", "알려줘", "있는", "근처", "쪽"}


@lru_cache(maxsize=None)
def _place_keys(spot_id: str) -> tuple[str, ...]:
    """포인트의 지역 이름 키(도 정식 명칭·별칭, 시군구 정식 명칭·어간). 권역 이름과 같은 어간(남해·동해)은 넣지 않는다."""
    s = next(x for x in load_spots() if x["spot_id"] == spot_id)
    keys = {s["province"], *PROVINCE_ALIASES.get(s["province"], []), s["city"]}
    stem = re.sub(r"(시|군|구)$", "", s["city"])
    if len(stem) >= 2 and stem not in REGION_WORDS:
        keys.add(stem)
    return tuple(sorted(keys, key=len, reverse=True))


def place_keys(spot: dict) -> tuple[str, ...]:
    """포인트의 도·시군구 이름 키 목록."""
    return _place_keys(spot["spot_id"])


def search_spot_list(query: str = "", region: str = "", fishing_type: str = "", species: str = "",
                     limit: int = 15) -> dict:
    """도·시군구·이름·소개·편의시설·어종 키워드(query, 공백으로 여러 개)와 권역·형태·어종 조건에 맞는 포인트를 찾는다.
    키워드는 모두 만족해야 하며(AND), 결과는 spot_id 순 최대 limit 곳과 전체 건수를 돌려준다."""
    tokens = [t for t in re.split(r"[\s,]+", query or "") if t and t not in SEARCH_STOPWORDS]
    out = []
    for s in load_spots():
        if region and s["region"] != region:
            continue
        if fishing_type and fishing_type not in s["types"]:
            continue
        if species and not any(species in sp or sp in species for sp in s["season"]):
            continue
        facilities = re.sub(r"[가-힣]+\s*없음", "", s["facilities"])  # '화장실 없음'이 '화장실' 검색에 걸리지 않게 뺀다
        text = " ".join([s["name"], s["area"], s["province"], s["city"], s["info"], facilities,
                         " ".join(s["season"]), " ".join(s["types"])])
        if all(t in text or any(t in k or k in t for k in place_keys(s)) for t in tokens):
            out.append(s)
    return {"total": len(out), "spots": [{"spot_id": s["spot_id"], "name": s["name"], "region": s["region"],
                                          "province": s["province"], "city": s["city"], "types": s["types"],
                                          "species": list(s["season"]), "info": s["info"], "facilities": s["facilities"]}
                                         for s in out[:limit]]}


def find_spot_candidates(name: str) -> list[dict]:
    """포인트명(부분 일치 포함)으로 후보를 찾는다. 정확히 일치하면 그 한 곳만 돌려준다."""
    q = re.sub(r"\s+", "", name or "")
    spots = load_spots()
    for s in spots:
        if q and (q == s["name"] or q == s["spot_id"]):
            return [s]
    cands = [s for s in spots if q and (s["name"] in q or q in s["name"])]
    if not cands:
        toks = [t for t in re.split(r"\s+", (name or "").strip()) if t]
        cands = [s for s in spots if toks and all(t in s["name"] + s["area"] for t in toks)]
    if not cands:  # '오천항 선상'처럼 이름 + 형태 단어가 붙은 경우 앞부분만 비교
        cands = [s for s in spots if len(s["name"]) >= 2 and s["name"][:3] in q]
    return cands


def resolve_spot(query: str) -> dict:
    """자유 텍스트 포인트명을 해석한다. ①Spot.name 일치 ②Boat.port 일치 ③도·시군구(별칭 포함) 일치 순.
    후보 1곳이면 resolved, 2곳 이상이면 ambiguous(최대 3곳, spot_id 순), 0곳이면 not_found."""
    q = re.sub(r"\s+", "", query or "")
    spots = sorted(load_spots(), key=lambda s: s["spot_id"])
    if not q:
        return {"query": query, "matched_by": None, "candidates": [], "status": "not_found"}
    stripped = q
    for w in ("낚시", "선상", "갯바위", "방파제", "좌대"):
        stripped = stripped.replace(w, "")
    def by_name(x):
        exact = [s for s in spots if x in (s["name"], s["spot_id"])]
        if exact:
            return exact
        return [s for s in spots if x and (s["name"] in x or x in s["name"])]
    def by_port(x):
        ids = {b["spot_id"] for b in load_boats() if x and (b["port"] in x or x in b["port"])}
        return [s for s in spots if s["spot_id"] in ids]
    def by_area(x):
        # '거제시'·'통영'·'강원도'처럼 도·시군구 이름이 들어 있으면 그 지역 포인트. '남해'·'동해'는 권역으로 보고 넘긴다
        if not x or len(x) < 2 or x in REGION_WORDS:
            return []
        return [s for s in spots if any(k in x for k in place_keys(s))]
    for how, fn in (("name", by_name), ("port", by_port), ("area", by_area)):
        c = fn(q) or (fn(stripped) if stripped and stripped != q else [])
        if c:
            ids = [s["spot_id"] for s in c][:3]
            return {"query": query, "matched_by": how, "candidates": ids,
                    "candidate_names": [s["name"] for s in c][:3],
                    "status": "resolved" if len(c) == 1 else "ambiguous"}
    return {"query": query, "matched_by": None, "candidates": [], "status": "not_found"}

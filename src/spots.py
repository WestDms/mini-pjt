"""spots.py - 자유 텍스트 포인트명을 spots.csv 포인트로 해석한다"""
import re

from src.data import load_boats, load_spots


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
    """자유 텍스트 포인트명을 해석한다. ①Spot.name 일치 ②Boat.port 일치 ③Spot.area 일치 순.
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
        return [s for s in spots if x and len(x) >= 2 and (x in s["area"].replace(" ", "")
                                                           or s["area"].replace(" ", "") in x)]
    for how, fn in (("name", by_name), ("port", by_port), ("area", by_area)):
        c = fn(q) or (fn(stripped) if stripped and stripped != q else [])
        if c:
            ids = [s["spot_id"] for s in c][:3]
            return {"query": query, "matched_by": how, "candidates": ids,
                    "candidate_names": [s["name"] for s in c][:3],
                    "status": "resolved" if len(c) == 1 else "ambiguous"}
    return {"query": query, "matched_by": None, "candidates": [], "status": "not_found"}

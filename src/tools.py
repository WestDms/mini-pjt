"""tools.py - 출조 Agent 도메인 도구 5개

recommend_spots, check_condition, search_regulation, find_boats, book_boat.
데이터(data/*.csv, criteria.json)는 읽기만 하고 수정하지 않는다.
"""
import csv
import json
import re
import uuid
from functools import lru_cache
from pathlib import Path

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LEVEL_RANK = {"가능": 0, "주의": 1, "불가": 2}
LEVELS = ["가능", "주의", "불가"]


def _const(name: str) -> str:
    """agent.py 의 고정 문구 상수를 지연 import 로 가져온다(순환 import 방지)."""
    from src import agent
    return getattr(agent, name)


@lru_cache(maxsize=1)
def load_spots() -> list[dict]:
    """spots.csv 를 읽어 types 와 species_season 을 구조화해 돌려준다."""
    rows = list(csv.DictReader(open(DATA / "spots.csv", encoding="utf-8-sig")))
    for r in rows:
        r["types"] = r["types"].split(";")
        season = {}
        for item in r["species_season"].split(";"):
            sp, rng = item.split(":")
            s, e = rng.split("-")
            season[sp] = (int(s), int(e))
        r["season"] = season
    return rows


@lru_cache(maxsize=1)
def load_forecast() -> dict:
    """forecast.csv 를 {(spot_id, date): 행} 으로 읽는다."""
    rows = csv.DictReader(open(DATA / "forecast.csv", encoding="utf-8-sig"))
    return {(r["spot_id"], r["date"]): r for r in rows}


@lru_cache(maxsize=1)
def load_boats() -> list[dict]:
    """boats.csv 를 읽는다."""
    return list(csv.DictReader(open(DATA / "boats.csv", encoding="utf-8-sig")))


@lru_cache(maxsize=1)
def load_criteria() -> dict:
    """criteria.json 을 읽는다."""
    return json.load(open(DATA / "criteria.json", encoding="utf-8"))


def valid_date(date: str | None) -> str | None:
    """날짜 문자열을 검증한다. 비어 있으면 D0(TODAY), 지원 범위 밖이면 None."""
    crit = load_criteria()
    if not date:
        return crit["today"]
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(date))
    d = m.group(0) if m else str(date).strip()
    return d if d in crit["supported_dates"] else None


def compute_verdict(spot: dict, date: str, ftype: str) -> dict:
    """(포인트, 날짜, 낚시형태)의 판정을 criteria.json 으로 결정적으로 계산한다."""
    crit = load_criteria()
    f = load_forecast().get((spot["spot_id"], date))
    if f is None:
        return {"verdict": "불가", "reason": "예보 데이터 없음"}
    wind, wave = float(f["wind_ms"]), float(f["wave_m"])
    th = crit["thresholds"][ftype]
    if wind <= th["가능"]["wind_max"] and wave <= th["가능"]["wave_max"]:
        level = "가능"
    elif wind <= th["주의"]["wind_max"] and wave <= th["주의"]["wave_max"]:
        level = "주의"
    else:
        level = "불가"
    notes = []
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
    return {"verdict": level, "wind_ms": wind, "wave_m": wave, "advisory": f["advisory"],
            "tide": f["tide"], "notes": notes}


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


def _store_pref(runtime: ToolRuntime) -> tuple:
    """runtime 에서 Store 와 user_id 를 꺼낸다."""
    cfg = (runtime.config or {}).get("configurable", {}) if runtime else {}
    return getattr(runtime, "store", None), cfg.get("user_id")


async def _load_pref(runtime: ToolRuntime) -> dict:
    """Store(user_id)에서 선호(어종·권역·형태)를 읽는다."""
    store, uid = _store_pref(runtime)
    if store is None or not uid:
        return {}
    item = await store.aget(("users", uid), "preference")
    return dict(item.value) if item else {}


@tool
async def recommend_spots(runtime: ToolRuntime, date: str = "", species: str = "",
                          region: str = "", fishing_type: str = "") -> str:
    """출조지를 추천한다. 어종·시즌·권역·낚시형태로 거른 뒤 '불가'는 제외하고
    가능>주의 순(동률은 spot_id 순) 상위 3곳을 돌려준다.
    date는 YYYY-MM-DD(없으면 오늘), region은 서해|남해|동해|제주, fishing_type은 방파제|갯바위|선상|좌대.
    받은 어종·권역·형태는 사용자 선호로 저장하고, 빠진 값은 저장된 선호로 채운다."""
    d = valid_date(date)
    if d is None:
        return _const("DATE_OUT_OF_RANGE")
    pref = await _load_pref(runtime)
    species = species or pref.get("species", "")
    region = region or pref.get("region", "")
    ftype = fishing_type or pref.get("fishing_type", "")
    store, uid = _store_pref(runtime)
    if store is not None and uid:
        new = {"species": species or None, "region": region or None, "fishing_type": ftype or None}
        # 이번 요청에서 받은 인자를 덮어쓰고, 비어 있는 항목은 기존 값을 유지한다
        merged = {k: (new[k] if new[k] else pref.get(k)) for k in new}
        await store.aput(("users", uid), "preference", merged)
    month = int(d[5:7])
    results = []
    for s in load_spots():
        if region and s["region"] != region:
            continue
        if ftype and ftype not in s["types"]:
            continue
        if species:
            hit = [sp for sp in s["season"] if species in sp or sp in species]
            if not hit:
                continue
            a, b = s["season"][hit[0]]
            if not ((a <= month <= b) if a <= b else (month >= a or month <= b)):
                continue
        t = ftype or s["default_type"]
        v = compute_verdict(s, d, t)
        if v["verdict"] == "불가":
            continue
        results.append({"spot_id": s["spot_id"], "name": s["name"], "region": s["region"],
                        "area": s["area"], "fishing_type": t, **v})
    results.sort(key=lambda r: (LEVEL_RANK[r["verdict"]], r["spot_id"]))
    return json.dumps({"date": d, "filters": {"species": species, "region": region, "fishing_type": ftype},
                       "recommendations": results[:3], "total_matched": len(results)}, ensure_ascii=False)


@tool
async def check_condition(runtime: ToolRuntime, spot_name: str, date: str = "",
                          fishing_type: str = "") -> str:
    """특정 포인트의 출조 가능 여부(가능|주의|불가)를 판정한다.
    date는 YYYY-MM-DD(없으면 오늘), fishing_type은 질문에 형태가 있을 때만 넣는다
    (없으면 저장된 선호 → 포인트 기본 형태 순). 모호한 포인트명은 후보를 돌려준다."""
    d = valid_date(date)
    if d is None:
        return _const("DATE_OUT_OF_RANGE")
    res = resolve_spot(spot_name)
    if res["status"] == "not_found":
        return _const("SPOT_NOT_FOUND")
    if res["status"] == "ambiguous":
        return json.dumps({"status": "ambiguous", "query": spot_name, "matched_by": res["matched_by"],
                           "candidates": res["candidates"], "candidate_names": res["candidate_names"],
                           "instruction": "어느 포인트인지 사용자에게 후보 중에서 되물어라. 임의로 고르지 마라."},
                          ensure_ascii=False)
    s = next(x for x in load_spots() if x["spot_id"] == res["candidates"][0])
    type_source = "질문"
    if fishing_type:
        if fishing_type not in s["types"]:
            return json.dumps({"status": "type_unsupported", "spot_id": s["spot_id"], "spot": s["name"],
                               "date": d, "fishing_type": fishing_type, "supported_types": s["types"],
                               "instruction": "Verdict를 내지 말고 supported_types 중에서 어떤 형태로 볼지 되물어라."},
                              ensure_ascii=False)
        ftype = fishing_type
    else:
        pref = await _load_pref(runtime)
        if pref.get("fishing_type") in s["types"]:
            ftype, type_source = pref["fishing_type"], "Store"
        else:
            ftype, type_source = s["default_type"], "default"
    v = compute_verdict(s, d, ftype)
    return json.dumps({"status": "ok", "spot": s["name"], "spot_id": s["spot_id"], "date": d,
                       "fishing_type": ftype, "type_source": type_source,
                       "basis": f"{ftype} 기준 판정", **v}, ensure_ascii=False)


@tool(response_format="content_and_artifact")
def search_regulation(query: str) -> tuple[str, list[dict]]:
    """낚시 규정(금어기·포획금지체장·안전수칙·지역특례)을 하이브리드 검색한다.
    문서에 없는 어종이면 찾을 수 없다고 답해야 한다."""
    from src import retriever
    chunks = retriever.search(query, k=3)
    text = "\n\n".join(f"[{c['doc_id']}]\n{c['text']}" for c in chunks)
    return text or "관련 규정을 찾지 못했습니다.", chunks


@tool
def find_boats(date: str = "", spot_name: str = "", species: str = "") -> str:
    """선상 배편을 찾는다. date는 YYYY-MM-DD(없으면 오늘), spot_name·species로 좁힐 수 있다.
    배ID, 선박명, 항구, 어종, 남은 자리, 가격을 돌려준다."""
    d = valid_date(date)
    if d is None:
        return _const("DATE_OUT_OF_RANGE")
    spot_ids = None
    if spot_name:
        c = find_spot_candidates(spot_name)
        if not c:
            return _const("SPOT_NOT_FOUND")
        spot_ids = {x["spot_id"] for x in c}
    out = []
    for b in load_boats():
        if b["date"] != d:
            continue
        if spot_ids and b["spot_id"] not in spot_ids:
            continue
        if species and species not in b["species"]:
            continue
        out.append({k: b[k] for k in ("boat_id", "boat_name", "port", "spot_id", "date", "species",
                                      "capacity", "seats_left", "price")})
    return json.dumps({"date": d, "boats": out}, ensure_ascii=False)


def precheck_booking(boat_id: str, date: str, people, name: str = "x", phone: str = "x") -> dict:
    """예약 사전검증(결정적). HITL 승인보다 먼저 호출한다.
    status: ok | missing_fields | date_out_of_range | boat_not_found | seats_insufficient | verdict_impossible"""
    if not (str(boat_id or "").strip() and str(date or "").strip() and people not in (None, "")
            and str(name or "").strip() and str(phone or "").strip()):
        return {"status": "missing_fields", "reason": "배ID·날짜·인원·이름·전화번호가 모두 필요합니다."}
    d = valid_date(date)
    if d is None:
        return {"status": "date_out_of_range", "reason": _const("DATE_OUT_OF_RANGE")}
    boat = next((b for b in load_boats() if b["boat_id"] == str(boat_id).strip().upper() and b["date"] == d), None)
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


@tool
def book_boat(boat_id: str, date: str, people: int, name: str, phone: str) -> str:
    """가상 선상 예약을 한다(HITL 승인 필요). 배ID·날짜·인원·이름·전화번호가 모두 필요하다.
    남은 자리보다 인원이 많거나 해당 날짜 선상 판정이 '불가'이면 거절한다. 예약번호만 발급한다."""
    pc = precheck_booking(boat_id, date, people, name, phone)
    if pc["status"] != "ok":
        return pc["reason"]
    boat, d = pc["boat"], pc["date"]
    no = "R" + d.replace("-", "")[2:] + "-" + uuid.uuid4().hex[:6].upper()
    return json.dumps({"reservation_no": no, "boat_id": boat["boat_id"], "boat_name": boat["boat_name"],
                       "date": d, "people": pc["people"], "name": name, "phone": phone,
                       "verdict": pc["verdict"]}, ensure_ascii=False)


ALL_TOOLS = [recommend_spots, check_condition, search_regulation, find_boats, book_boat]

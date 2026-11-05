"""tools.py - 출조 Agent 도메인 도구 6개(에이전트에 노출하는 @tool 정의만 둔다)

recommend_spots, check_condition, search_regulation, find_boats, book_boat, spot_info.
데이터 읽기는 data, 판정은 verdict, 포인트 해석은 spots, 선호는 preferences, 예약 검증은 booking,
데이터가 없을 때 대안 찾기는 suggest 가 맡는다.
데이터(data/*.csv, criteria.json)는 읽기만 하고 수정하지 않는다.
"""
import asyncio
import json
import uuid

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from src.booking import precheck_booking
from src.constants import DATE_OUT_OF_RANGE, SPOT_NOT_FOUND
from src.data import LEVEL_RANK, boats_on, load_boats, load_spots
from src.preferences import load_pref, store_pref
from src.spots import find_spot_candidates, resolve_spot
from src.suggest import (boat_alternatives, in_season, regulation_gap, relaxed_recommendations,
                         spot_alternatives)
from src.verdict import compute_verdict, valid_date
from src.weather import prefetch


@tool
async def recommend_spots(runtime: ToolRuntime, date: str = "", species: str = "",
                          region: str = "", fishing_type: str = "") -> str:
    """출조지를 추천한다. 어종·시즌·권역·낚시형태로 거른 뒤 '불가'는 제외하고
    가능>주의 순(동률은 spot_id 순) 상위 3곳을 돌려준다.
    date는 YYYY-MM-DD(없으면 오늘), region은 서해|남해|동해|제주, fishing_type은 방파제|갯바위|선상|좌대.
    받은 어종·권역·형태는 사용자 선호로 저장하고, 빠진 값은 저장된 선호로 채운다."""
    d = valid_date(date)
    if d is None:
        return DATE_OUT_OF_RANGE
    pref = await load_pref(runtime)
    species = species or pref.get("species", "")
    region = region or pref.get("region", "")
    ftype = fishing_type or pref.get("fishing_type", "")
    store, uid = store_pref(runtime)
    if store is not None and uid:
        new = {"species": species or None, "region": region or None, "fishing_type": ftype or None}
        # 이번 요청에서 받은 인자를 덮어쓰고, 비어 있는 항목은 기존 값을 유지한다
        merged = {k: (new[k] if new[k] else pref.get(k)) for k in new}
        await store.aput(("users", uid), "preference", merged)
    month = int(d[5:7])
    results = []
    # 실시간 모드에서는 후보 포인트 예보를 병렬로 미리 받아 둔다(csv 모드는 아무것도 하지 않음)
    await asyncio.to_thread(prefetch, [s for s in load_spots() if (not region or s["region"] == region)
                                       and (not ftype or ftype in s["types"])], d)
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
    out = {"date": d, "filters": {"species": species, "region": region, "fishing_type": ftype},
           "recommendations": results[:3], "total_matched": len(results)}
    if not results:  # 조건에 맞는 곳이 없으면 조건을 하나씩 풀어 데이터에 있는 대안을 찾는다
        alt = await asyncio.to_thread(relaxed_recommendations, d, species, region, ftype)
        if alt:
            out["alternatives"] = alt
            out["instruction"] = "요청 조건에 맞는 곳이 없다고 먼저 말하고, alternatives.relaxed 사유와 함께 대안을 추천하라."
    return json.dumps(out, ensure_ascii=False)


@tool
async def check_condition(runtime: ToolRuntime, spot_name: str, date: str = "",
                          fishing_type: str = "") -> str:
    """특정 포인트의 출조 가능 여부(가능|주의|불가)를 판정한다.
    date는 YYYY-MM-DD(없으면 오늘), fishing_type은 질문에 형태가 있을 때만 넣는다
    (없으면 저장된 선호 → 포인트 기본 형태 순). 모호한 포인트명은 후보를 돌려준다."""
    d = valid_date(date)
    if d is None:
        return DATE_OUT_OF_RANGE
    res = resolve_spot(spot_name)
    if res["status"] == "not_found":
        alt = await asyncio.to_thread(spot_alternatives, spot_name, d)
        return json.dumps({"status": "not_found", "query": spot_name, "message": SPOT_NOT_FOUND, **alt,
                           "instruction": "message 를 그대로 전한 뒤 suggestions 를 대안 포인트로 제안하라. "
                                          "없는 포인트의 판정·수치는 말하지 말고, 대안의 판정은 사용자가 원할 때 확인한다."},
                          ensure_ascii=False)
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
        pref = await load_pref(runtime)
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
    gap = regulation_gap(query)  # 문서에 없는 어종이면 문서에 있는 어종 목록을 덧붙인다
    if gap:
        text = f"{text}\n\n{gap}" if text else gap
    return text or "관련 규정을 찾지 못했습니다.", chunks


@tool
def find_boats(date: str = "", spot_name: str = "", species: str = "") -> str:
    """선상 배편을 찾는다. date는 YYYY-MM-DD(없으면 오늘), spot_name(포인트·항구)·species(어종 이름, 예: 감성돔)로 좁힐 수 있다.
    배ID, 선박명, 항구, 어종, 남은 자리, 가격을 돌려준다."""
    d = valid_date(date)
    if d is None:
        return DATE_OUT_OF_RANGE
    if species.strip() in ("선상", "방파제", "갯바위", "좌대", "낚시", "선상낚시"):
        species = ""  # 어종이 아니라 낚시 형태가 들어오면 무시한다(배편은 모두 선상)
    spot_ids = None
    if spot_name:
        c = find_spot_candidates(spot_name)
        if not c:
            return json.dumps({"status": "not_found", "query": spot_name, "message": SPOT_NOT_FOUND,
                               **spot_alternatives(spot_name, d),
                               "instruction": "message 를 그대로 전한 뒤 suggestions 포인트의 배편을 찾아볼지 제안하라."},
                              ensure_ascii=False)
        spot_ids = {x["spot_id"] for x in c}
    out = []
    for b in boats_on(d):
        if spot_ids and b["spot_id"] not in spot_ids:
            continue
        if species and species not in b["species"]:
            continue
        out.append({k: b[k] for k in ("boat_id", "boat_name", "port", "spot_id", "date", "species",
                                      "capacity", "seats_left", "price")})
    res = {"date": d, "boats": out}
    if not out:  # 배편이 없으면 같은 포인트 다른 날짜·같은 권역 배편을 대안으로 준다
        alts = boat_alternatives(d, spot_ids, species)
        if alts:
            res["alternatives"] = alts
            res["instruction"] = "요청한 조건의 배편은 없다고 먼저 말하고, alternatives 를 why 와 함께 대안으로 추천하라."
    return json.dumps(res, ensure_ascii=False)



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


@tool
def spot_info(spot_name: str) -> str:
    """포인트(출조지) 정보를 조회한다. 지역·권역, 가능한 낚시 형태, 대상어종과 어종별 시즌(이번 달 시즌 여부),
    그 포인트로 가는 선상 배편(배ID·선박명·항구)을 돌려준다. '○○ 대상어종', '○○ 어떤 곳이야' 같은 질문에 쓴다.
    출조 가능 판정(날씨)은 check_condition, 규정(금어기·체장)은 search_regulation 을 쓴다."""
    res = resolve_spot(spot_name)
    if res["status"] == "not_found":
        return json.dumps({"status": "not_found", "query": spot_name, "message": SPOT_NOT_FOUND,
                           **spot_alternatives(spot_name, valid_date("")),
                           "instruction": "message 를 그대로 전한 뒤 suggestions 를 대안 포인트로 제안하라."},
                          ensure_ascii=False)
    if res["status"] == "ambiguous":
        return json.dumps({"status": "ambiguous", "query": spot_name, "candidate_names": res["candidate_names"],
                           "instruction": "어느 포인트인지 사용자에게 후보 중에서 되물어라."}, ensure_ascii=False)
    s = next(x for x in load_spots() if x["spot_id"] == res["candidates"][0])
    month = int(valid_date("")[5:7])
    species = [{"species": sp, "season": f"{a}월~{b}월", "in_season_now": in_season(s, sp, month)}
               for sp, (a, b) in s["season"].items()]
    boats = {}
    for b in load_boats():  # 같은 배가 날짜별로 여러 행이어도 한 번만 보여준다
        if b["spot_id"] == s["spot_id"] and b["boat_id"] not in boats:
            boats[b["boat_id"]] = {"boat_id": b["boat_id"], "boat_name": b["boat_name"], "port": b["port"],
                                   "species": b["species"].replace(";", ", "), "price": b["price"]}
    return json.dumps({"status": "ok", "spot_id": s["spot_id"], "name": s["name"], "region": s["region"],
                       "area": s["area"], "fishing_types": s["types"], "default_type": s["default_type"],
                       "species": species, "boats": list(boats.values()),
                       "note": "날짜별 남은 자리는 find_boats, 출조 가능 여부는 check_condition 으로 확인한다."},
                      ensure_ascii=False)


ALL_TOOLS = [recommend_spots, check_condition, search_regulation, find_boats, book_boat, spot_info]

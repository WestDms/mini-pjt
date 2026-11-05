"""test_spot_search.py - 포인트 상세(도·시군구·소개·편의시설)와 도·시군구·키워드 검색 테스트."""
import json

from src.data import load_spots
from src.spots import place_keys, resolve_spot, search_spot_list
from src.suggest import infer_region
from src.tools import search_spots, spot_info


def test_all_spots_have_details():
    """62곳 모두 도·시군구·소개·편의시설이 있고, 도 이름은 현재 정식 명칭을 쓴다."""
    spots = load_spots()
    assert len(spots) == 62
    assert all(s["province"] and s["city"] and s["info"] and s["facilities"] for s in spots)
    s = next(x for x in spots if x["spot_id"] == "E01")
    assert (s["province"], s["city"]) == ("강원특별자치도", "속초시")


def test_place_keys_aliases_and_region_words():
    """도 별칭(강원도)·시군구 어간(거제)은 키에 넣고, 권역 이름과 같은 어간(남해·동해)은 넣지 않는다."""
    e01 = next(x for x in load_spots() if x["spot_id"] == "E01")
    assert {"강원도", "강원", "속초시", "속초"} <= set(place_keys(e01))
    s06 = next(x for x in load_spots() if x["spot_id"] == "S06")
    assert "남해군" in place_keys(s06) and "남해" not in place_keys(s06)


def test_resolve_by_city_and_province():
    """'거제시'·'울릉군'은 그 지역 포인트로, '남해'는 권역이라 포인트 하나로 해석하지 않는다."""
    assert resolve_spot("거제시")["status"] == "ambiguous"
    assert set(resolve_spot("울릉군")["candidates"]) <= {"E08", "E09"}
    assert resolve_spot("남해")["status"] == "not_found"


def test_search_spot_list():
    """도 이름+형태, 시군구, 편의시설 키워드(AND)로 찾는다."""
    r = search_spot_list("강원도", fishing_type="방파제")
    assert r["total"] >= 4 and all(s["province"] == "강원특별자치도" for s in r["spots"])
    assert {s["name"] for s in search_spot_list("거제시")["spots"]} == {"학동갯바위", "여차갯바위", "지세포방파제"}
    island = search_spot_list("섬 갯바위")["spots"]
    assert island and all("섬" in s["info"] for s in island)
    assert search_spot_list("화장실", fishing_type="갯바위")["total"] == 0


def test_search_spots_tool_moves_place_to_query():
    """region 에 도 이름이 들어와도 키워드로 돌려 검색한다."""
    r = json.loads(search_spots.invoke({"region": "강원도"}))
    assert r["total"] >= 6 and r["conditions"]["query"] == "강원도"


def test_spot_info_has_details():
    """spot_info 결과에 도·시군구·소개·편의시설이 들어 있다."""
    r = json.loads(spot_info.invoke({"spot_name": "후포항"}))
    assert r["province"] == "경상북도" and r["city"] == "울진군" and r["info"] and r["facilities"]


def test_infer_region_from_city():
    """단서 목록에 없는 시군구 이름(영덕군)으로도 권역을 짐작한다."""
    assert infer_region("영덕군 어디 갯바위") == "동해"

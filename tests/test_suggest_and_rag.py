"""test_suggest_and_rag.py - 데이터가 없을 때 대안 찾기와 규정 검색의 어종 우선 청크 테스트(임베딩 호출 없음)."""
from src.retriever import species_chunks
from src.suggest import (boat_alternatives, infer_region, regulation_gap, relaxed_recommendations,
                         spot_alternatives)


def test_infer_region():
    """질문 속 지명으로 권역을 짐작한다."""
    assert infer_region("독도 동도 선착장") == "동해"
    assert infer_region("인천 남항부두") == "서해"
    assert infer_region("아무데나") is None


def test_spot_alternatives_prefers_similar_and_local():
    """오타는 비슷한 이름을, 지명은 같은 지역 포인트를 먼저 제안한다."""
    assert spot_alternatives("지세보방파제", "2026-11-07")["suggestions"][0]["name"] == "지세포방파제"
    names = [s["name"] for s in spot_alternatives("독도 동도 선착장", "2026-11-07")["suggestions"]]
    assert {"저동항", "울릉도갯바위"} <= set(names[:2])
    local = [s["area"] for s in spot_alternatives("부산 영도 방파제", "2026-11-07")["suggestions"]]
    assert all(a.startswith("부산") for a in local)


def test_relaxed_recommendations():
    """제주 쭈꾸미처럼 조건에 맞는 곳이 없으면 조건을 풀어 대안을 찾는다."""
    r = relaxed_recommendations("2026-11-07", "쭈꾸미", "제주", "")
    assert r and r["recommendations"] and "권역" in r["relaxed"]


def test_boat_alternatives():
    """좌석이 없는 포인트는 다른 날짜·같은 권역, 어종이 없으면 같은 날짜 다른 배편을 준다."""
    alts = boat_alternatives("2026-11-07", {"S06"}, "")
    assert alts and {a["why"] for a in alts} <= {"같은 포인트 다른 날짜", "같은 권역 같은 날짜"}
    assert boat_alternatives("2026-11-07", None, "개복치")[0]["why"] == "같은 날짜 다른 어종 배편"


def test_regulation_gap():
    """규정에 없는 어종 질문에만 문서 어종 목록 안내를 붙인다."""
    assert "문서에 있는 어종" in regulation_gap("개복치 포획금지체장 알려줘")
    assert regulation_gap("우럭 몇 cm부터?") is None
    assert regulation_gap("구명조끼 꼭 입어야 해?") is None


def test_species_chunks_both_docs():
    """'방어 금어기 체장'은 금어기·체장 문서의 방어 청크를 모두, '우럭 금어기'는 금어기 문서만 고른다."""
    ids = [c["doc_id"] for c in species_chunks("방어 금어기 체장")]
    assert any("금어기" in i for i in ids) and any("체장" in i for i in ids)
    assert all("금어기" in c["doc_id"] for c in species_chunks("우럭 금어기"))

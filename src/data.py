"""data.py - data/ 의 CSV·JSON 을 읽어 캐시한다(읽기 전용)"""
import csv
import json
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LEVEL_RANK = {"가능": 0, "주의": 1, "불가": 2}
LEVELS = ["가능", "주의", "불가"]


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
def load_boats() -> list[dict]:
    """boats.csv 를 읽는다. date 가 '매일'인 행은 날짜와 무관하게 매일 운항하는 배편이다."""
    return list(csv.DictReader(open(DATA / "boats.csv", encoding="utf-8-sig")))


DAILY = "매일"


def boats_on(date: str) -> list[dict]:
    """해당 날짜에 운항하는 배편 목록. 그 날짜가 지정된 행을 우선하고,
    같은 배의 지정 행이 없으면 '매일' 행을 그 날짜로 채워 쓴다."""
    dated = [b for b in load_boats() if b["date"] == date]
    have = {b["boat_id"] for b in dated}
    daily = [{**b, "date": date} for b in load_boats() if b["date"] == DAILY and b["boat_id"] not in have]
    return dated + daily


@lru_cache(maxsize=1)
def load_criteria() -> dict:
    """criteria.json 을 읽는다."""
    return json.load(open(DATA / "criteria.json", encoding="utf-8"))

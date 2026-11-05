"""conftest.py - 테스트는 항상 고정 스냅샷(csv) 데이터로 돌리고 외부 API 키를 쓰지 않는다."""
import os
import sys
from pathlib import Path

# .env 의 load_dotenv 는 이미 있는 환경변수를 덮어쓰지 않으므로, import 전에 테스트 값을 먼저 넣는다
os.environ["WEATHER_SOURCE"] = "csv"
os.environ["TODAY"] = "2026-11-06"
os.environ["KMA_SERVICE_KEY"] = ""
os.environ["KHOA_SERVICE_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#!/usr/bin/env bash
# run.sh - 의존성 설치 → Chroma 준비 → 서버 실행
set -e
cd "$(dirname "$0")"
pip install -r requirements.txt
if [ ! -d data/chroma ]; then
  python -m src.retriever
fi
exec uvicorn src.agent:app --port 8000

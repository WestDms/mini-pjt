# 출조 Agent 컨테이너 이미지
# 빌드: docker build -t fishing-agent .
# 실행: docker run --env-file .env -p 8000:8000 fishing-agent
# 비밀 값(.env)은 이미지에 넣지 않고 --env-file 로만 주입한다.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

# 필요한 디렉터리만 복사한다(.env 는 복사하지 않는다)
COPY src ./src
COPY data/spots.csv data/forecast.csv data/criteria.json data/boats.csv ./data/
COPY data/regulations ./data/regulations
RUN mkdir -p traces

EXPOSE 8000

# data/chroma/ 가 없으면 빌드(임베딩은 Bedrock 자격증명 필요) 후 서버 실행
CMD ["sh", "-c", "[ -d data/chroma ] || python -m src.retriever; exec uvicorn src.agent:app --host 0.0.0.0 --port 8000"]

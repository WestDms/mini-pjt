"""retriever.py - 규정 문서 RAG 파이프라인 (BM25 + Chroma 하이브리드)

data/regulations/*.md 를 '## ' 제목 단위로 청크로 나누고,
BM25(한국어 형태소)와 Chroma(Bedrock Titan 임베딩)를 EnsembleRetriever 로 합친다.
data/chroma/ 가 없으면 build_chroma() 가 새로 만든다.
"""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
REG_DIR = ROOT / "data" / "regulations"
CHROMA_DIR = ROOT / "data" / "chroma"
COLLECTION = "regulations"

_kiwi = None
_hybrid = None


def load_chunks() -> list[dict]:
    """규정 마크다운을 '## ' 제목 단위 청크로 나눈다. doc_id는 regulations/<파일명>#<청크번호>."""
    chunks = []
    for path in sorted(REG_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip() if text.strip() else path.stem
        parts = re.split(r"(?m)^(?=## )", text)
        n = 0
        for part in parts:
            part = part.strip()
            if not part.startswith("## "):
                continue  # 문서 머리말(제목·안내)은 청크로 쓰지 않는다
            chunks.append({
                "doc_id": f"regulations/{path.name}#{n}",
                "text": f"[{title}]\n{part}",
            })
            n += 1
    return chunks


def korean_tokenizer(text: str) -> list[str]:
    """한국어 형태소 토크나이저. 명사·어간·숫자·외래어만 남긴다."""
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
    keep = {"NNG", "NNP", "NNB", "NR", "NP", "VV", "VA", "SL", "SN"}
    return [t.form for t in _kiwi.tokenize(text) if t.tag in keep]


def _embeddings():
    """Bedrock Titan 임베딩 객체를 만든다."""
    from langchain_aws import BedrockEmbeddings
    return BedrockEmbeddings(
        model_id=os.getenv("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    )


def build_chroma() -> None:
    """data/chroma/ 가 없으면 규정 청크를 임베딩해 Chroma 벡터DB를 만든다."""
    if CHROMA_DIR.exists() and any(CHROMA_DIR.iterdir()):
        return
    from langchain_chroma import Chroma
    from langchain_core.documents import Document
    docs = [Document(page_content=c["text"], metadata={"doc_id": c["doc_id"]}) for c in load_chunks()]
    Chroma.from_documents(docs, _embeddings(), collection_name=COLLECTION,
                          persist_directory=str(CHROMA_DIR), ids=[c["doc_id"] for c in load_chunks()])


def get_hybrid():
    """BM25 + Chroma 하이브리드 검색기를 (캐시해서) 돌려준다. 가중치는 BM25 0.3 / 벡터 0.7."""
    global _hybrid
    if _hybrid is None:
        from langchain_chroma import Chroma
        from langchain_classic.retrievers import EnsembleRetriever
        from langchain_community.retrievers import BM25Retriever
        from langchain_core.documents import Document
        build_chroma()
        docs = [Document(page_content=c["text"], metadata={"doc_id": c["doc_id"]}) for c in load_chunks()]
        bm25 = BM25Retriever.from_documents(docs, preprocess_func=korean_tokenizer)
        bm25.k = 3
        vector = Chroma(collection_name=COLLECTION, embedding_function=_embeddings(),
                        persist_directory=str(CHROMA_DIR)).as_retriever(search_kwargs={"k": 3})
        _hybrid = EnsembleRetriever(retrievers=[bm25, vector], weights=[0.3, 0.7])
    return _hybrid


def search(query: str, k: int = 3) -> list[dict]:
    """질의로 규정 청크를 검색해 [{doc_id, text}] 상위 k개를 돌려준다."""
    hits = get_hybrid().invoke(query)
    out, seen = [], set()
    for h in hits:
        did = h.metadata.get("doc_id")
        if did in seen:
            continue
        seen.add(did)
        out.append({"doc_id": did, "text": h.page_content})
    return out[:k]


if __name__ == "__main__":
    build_chroma()
    print("chroma ready:", CHROMA_DIR)

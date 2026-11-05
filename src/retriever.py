"""retriever.py - 규정 문서 RAG 검색 (BM25 + Chroma 하이브리드)

청크 나누기는 chunker, 임베딩·Chroma 생성은 vectorstore 가 맡고,
여기서는 BM25(한국어 형태소)와 Chroma 를 EnsembleRetriever 로 합쳐 검색한다.
`python -m src.retriever` 는 data/chroma/ 가 없을 때 벡터DB 를 만든다.
"""
import re
import threading

from src.chunker import load_chunks
from src.vectorstore import CHROMA_DIR, COLLECTION, build_chroma, embeddings

_kiwi = None
_hybrid = None
# 모델이 search_regulation 을 한 턴에 여러 번 병렬 호출하면 첫 초기화가 동시에 일어나
# Chroma 클라이언트 생성이 충돌한다. 초기화와 형태소 분석은 잠금으로 한 번에 하나만 실행한다.
_init_lock = threading.Lock()
_kiwi_lock = threading.Lock()


def korean_tokenizer(text: str) -> list[str]:
    """한국어 형태소 토크나이저. 명사·어간·숫자·외래어만 남긴다."""
    global _kiwi
    with _kiwi_lock:
        if _kiwi is None:
            from kiwipiepy import Kiwi
            _kiwi = Kiwi()
        tokens = _kiwi.tokenize(text)
    keep = {"NNG", "NNP", "NNB", "NR", "NP", "VV", "VA", "SL", "SN"}
    return [t.form for t in tokens if t.tag in keep]


def get_hybrid():
    """BM25 + Chroma 하이브리드 검색기를 (캐시해서) 돌려준다. 가중치는 BM25 0.3 / 벡터 0.7.
    여러 스레드가 동시에 불러도 한 번만 만들어지도록 잠금 안에서 초기화한다."""
    global _hybrid
    if _hybrid is not None:
        return _hybrid
    with _init_lock:
        if _hybrid is not None:
            return _hybrid
        from langchain_chroma import Chroma
        from langchain_classic.retrievers import EnsembleRetriever
        from langchain_community.retrievers import BM25Retriever
        from langchain_core.documents import Document
        build_chroma()
        docs = [Document(page_content=c["text"], metadata={"doc_id": c["doc_id"]}) for c in load_chunks()]
        bm25 = BM25Retriever.from_documents(docs, preprocess_func=korean_tokenizer)
        bm25.k = 3
        vector = Chroma(collection_name=COLLECTION, embedding_function=embeddings(),
                        persist_directory=str(CHROMA_DIR)).as_retriever(search_kwargs={"k": 3})
        _hybrid = EnsembleRetriever(retrievers=[bm25, vector], weights=[0.3, 0.7])
    return _hybrid


SIZE_WORDS = ("체장", "cm", "센티", "크기", "몇")


def species_chunks(query: str) -> list[dict]:
    """질문에 나온 어종 이름이 금어기·체장 청크 제목(괄호 별칭 포함)과 일치하면 그 청크들을 돌려준다.
    질문이 금어기만 물으면 금어기 문서만, 체장만 물으면 체장 문서만 고른다."""
    q = re.sub(r"\s+", "", query or "")
    want_ban, want_size = "금어기" in q, any(w in q for w in SIZE_WORDS)
    out = []
    for c in load_chunks():
        is_ban, is_size = "금어기" in c["doc_id"], "체장" in c["doc_id"]
        if not (is_ban or is_size):
            continue
        if want_ban != want_size and not ((want_ban and is_ban) or (want_size and is_size)):
            continue
        m = re.search(r"^## (.+)$", c["text"], re.M)
        names = [t.strip().removesuffix("류") for t in re.split(r"[(),\s]+", m.group(1))] if m else []
        if any(len(n) >= 2 and n in q for n in names):
            out.append({"doc_id": c["doc_id"], "text": c["text"]})
    return out


def search(query: str, k: int = 3) -> list[dict]:
    """질의로 규정 청크를 검색해 [{doc_id, text}] 를 돌려준다.
    어종 이름이 정확히 맞는 청크를 먼저 넣고(최대 5개), 남는 자리는 하이브리드 검색 상위 결과로 채운다."""
    exact = species_chunks(query)[:5]
    out, seen = list(exact), {c["doc_id"] for c in exact}
    for h in get_hybrid().invoke(query):
        did = h.metadata.get("doc_id")
        if did in seen:
            continue
        seen.add(did)
        out.append({"doc_id": did, "text": h.page_content})
    return out[:max(k, len(exact))]


if __name__ == "__main__":
    build_chroma()
    print("chroma ready:", CHROMA_DIR)

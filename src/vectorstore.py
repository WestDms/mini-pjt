"""vectorstore.py - Bedrock Titan 임베딩과 Chroma 벡터DB(data/chroma/) 생성"""
import os
from pathlib import Path

from dotenv import load_dotenv

from src.chunker import load_chunks

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
CHROMA_DIR = ROOT / "data" / "chroma"
COLLECTION = "regulations"


def embeddings():
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
    Chroma.from_documents(docs, embeddings(), collection_name=COLLECTION,
                          persist_directory=str(CHROMA_DIR), ids=[c["doc_id"] for c in load_chunks()])

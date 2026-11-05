"""chunker.py - 규정 마크다운(data/regulations/*.md)을 '## ' 제목 단위 청크로 나눈다"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REG_DIR = ROOT / "data" / "regulations"


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

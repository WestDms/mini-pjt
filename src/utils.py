"""utils.py - 여러 모듈이 함께 쓰는 작은 도우미(메시지 텍스트 추출, 환경변수 불리언)"""
import os


def get_text(message) -> str:
    """메시지 content 가 블록 리스트여도 텍스트만 모아 문자열로 돌려준다."""
    content = message.content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content or ""


def env_true(name: str) -> bool:
    """환경변수가 'true'(대소문자 무관)인지 확인한다."""
    return os.getenv(name, "false").strip().lower() == "true"

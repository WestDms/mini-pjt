"""cli.py - 터미널 실행: 대화형 CLI(기본)와 고정 예약 데모(--demo). 사람 승인→재개는 여기서만 한다"""
import asyncio
import os
import re
import uuid

from src.app import FishingApp
from src.constants import BOOKING_CANCELLED, DEMO_QUESTION


async def _demo(build_app) -> None:
    """CLI 데모. EVAL_AUTO_APPROVE 와 무관하게 HITL ON 으로 데모 예약을 보내 interrupt 를 띄우고, stdin 한 줄로 1번만 재개한다."""
    os.environ["EVAL_AUTO_APPROVE"] = "false"  # 데모는 항상 HITL ON
    fa = await build_app()
    thread, user = uuid.uuid4().hex, "cli_user"
    try:
        q = DEMO_QUESTION
        print(f"질문> {q}")
        r = await fa.query(q, thread, user)
        print(r["answer"])
        await _cli_approve(fa, r, thread, user)
        config = {"configurable": {"thread_id": thread, "user_id": user}}
        print(f"[대기 interrupt 없음: {await fa._pending(config) is None}]")
    finally:
        await fa.aclose()


async def _cli_approve(fa: FishingApp, r: dict, thread: str, user: str) -> None:
    """응답이 HITL 대기면 사용자에게 승인 여부를 묻고 한 번 재개한다. y/yes/승인만 승인으로 본다."""
    if not any(t["step"] == "hitl_pending" for t in r["trace"]):
        return
    try:
        line = input("승인하시겠습니까? (y/n)> ")
    except EOFError:
        line = ""
    ok = line.strip().lower() in {"y", "yes", "승인"}  # 명시적 승인만 인정, 그 외는 거부
    res = await fa.resume(thread, ok, user)
    if ok:
        nos = [m.group(1) for t in res["trace"] if t["step"] == "tool:book_boat"
               for m in [re.search(r'"reservation_no":\s*"([^"]+)"', t["output"])] if m]
        print(res["answer"])
        if nos and nos[0] not in res["answer"]:
            print(f"예약번호: {nos[0]}")
    else:
        print(BOOKING_CANCELLED)


async def _chat(build_app) -> None:
    """대화형 CLI. 사용자 질문을 계속 받아 답하고, 예약 승인(HITL)은 그때마다 사람에게 묻는다.
    같은 대화 안에서는 thread_id 를 유지해 앞 대화를 기억한다. '새 대화' 는 새 대화, '종료'(exit·quit 도 허용) 는 끝낸다."""
    os.environ["EVAL_AUTO_APPROVE"] = "false"  # 대화형도 항상 사람 승인
    fa = await build_app()
    thread, user = uuid.uuid4().hex, "cli_user"
    print("안녕하세요! 출조 도우미입니다. 🎣\n"
          "어디로, 언제 나가면 좋을지 편하게 물어보세요.\n"
          "출조지 추천, 날씨·물때로 본 출조 가능 여부, 금어기·체장 같은 낚시 규정, 선상 배편 예약을 도와드려요.\n"
          "예) \"내일 남해에서 감성돔 잡을 만한 곳 추천해줘\", \"모레 지세포방파제 나가도 될까?\"\n"
          "(새 대화는 '새 대화', 마칠 때는 '종료'라고 입력하세요)")
    try:
        while True:
            try:
                q = input("\n질문> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not q:
                continue
            if q.lower() in {"종료", "exit", "quit", "q"}:
                break
            if q.replace(" ", "") == "새대화" or q == "/new":  # /new 는 이전 안내와의 호환용
                thread = uuid.uuid4().hex
                print("새 대화를 시작합니다.")
                continue
            r = await fa.query(q, thread, user)
            print(r["answer"])
            await _cli_approve(fa, r, thread, user)
    finally:
        await fa.aclose()


def main(build_app, argv: list[str]) -> None:
    """CLI 진입점. argv 에 --demo 가 있으면 고정 예약 데모, 없으면 대화형 모드를 실행한다."""
    asyncio.run(_demo(build_app) if "--demo" in argv else _chat(build_app))

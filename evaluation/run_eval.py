"""run_eval.py - 출조 Agent 자체 평가 (build_app() 직접 호출, EVAL_AUTO_APPROVE=true).

사용: WEATHER_SOURCE=csv python evaluation/run_eval.py --round <N>  (평가셋 전체를 순서대로 실행)
통과 = expected_tools 가 실제 호출 도구에 포함 AND forbidden 위반 없음 AND LLM Judge 가 expected_traits 전부 충족.
케이스당 1회만 실행해 채점한다(재실행으로 좋은 결과를 고르지 않는다).
"""
import argparse
import asyncio
import csv
import os
import re
import sys
import types
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_ID = __import__("uuid").uuid4().hex[:8]  # 실행마다 thread/user 를 분리하는 접미사
sys.path.insert(0, str(ROOT))
os.environ["EVAL_AUTO_APPROVE"] = "true"
warnings.filterwarnings("ignore")

from dotenv import load_dotenv  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

load_dotenv(ROOT / ".env")
os.environ["EVAL_AUTO_APPROVE"] = "true"

from langchain_core.prompts import ChatPromptTemplate  # noqa: E402

from src.agent import build_app, make_llm  # noqa: E402

CSV_PATH = ROOT / "evaluation" / "test_queries.csv"


class ItemVerdict(BaseModel):
    """Judge 의 항목별 판정."""
    item: str = Field(description="평가 항목 원문")
    ok: bool = Field(description="traits 는 충족 여부, forbidden 은 위반 여부")


class JudgeResult(BaseModel):
    """LLM Judge 출력. traits 는 충족 여부, forbidden 은 위반 여부."""
    traits: list[ItemVerdict]
    forbidden: list[ItemVerdict]
    reason: str


JUDGE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "당신은 낚시 출조 Agent 의 답변을 엄격하게 채점하는 평가자입니다. 답변 본문과 참고 컨텍스트만 근거로 판단하세요.\n"
               "- traits: 각 항목을 답변이 충족하면 ok=true, 아니면 false.\n"
               "- forbidden: 각 항목을 답변이 위반(해당 행동을 함)하면 ok=true, 위반하지 않았으면 false.\n"
               "- 마스킹된 값(홍*동, 010-****-5678)은 올바른 마스킹 표기이므로 이름·전화 마스킹 항목은 충족으로 본다.\n"
               "항목 문자열은 입력 그대로 item 에 복사하고, reason 에 한국어로 짧게 근거를 적으세요."),
    ("human", "질문: {question}\n\n답변:\n{answer}\n\n참고 컨텍스트(도구 결과):\n{contexts}\n\n"
              "[traits]\n{traits}\n\n[forbidden]\n{forbidden}"),
])


def split_items(s: str) -> list[str]:
    """';' 로 구분된 항목 문자열을 리스트로 만든다."""
    return [x.strip() for x in s.split(";") if x.strip()]


def parse_note(note: str) -> dict:
    """note 칸의 thread/user/setup/setup_thread 표기를 읽는다. '/' 뒤는 설명이므로 버린다."""
    head = note.split(" / ")[0] if " / " in note else note
    out = {}
    for key in ("setup_thread", "thread", "user", "setup"):
        m = re.search(rf"(?:^|[;,\s]){key}=([^;]+)", head)
        if m:
            out[key] = m.group(1).strip()
    return out


def has_kw(text: str, kw: str) -> bool:
    """문자열 포함 여부."""
    return kw in text


async def judge(llm_judge, row, res) -> JudgeResult:
    """LLM Judge 로 expected_traits 와 [판단] forbidden 을 채점한다."""
    traits = split_items(row["expected_traits"])
    fb = [f for f in split_items(row["forbidden"]) if f.startswith("[판단]")]
    ctx = "\n".join(f"[{c['doc_id']}] {c['text']}" for c in res["contexts"])[:6000] or "(없음)"
    return await llm_judge.ainvoke({
        "question": row["input"], "answer": res["answer"], "contexts": ctx,
        "traits": "\n".join(f"- {t}" for t in traits) or "(없음)",
        "forbidden": "\n".join(f"- {f}" for f in fb) or "(없음)"})


async def ragas_scores(row, res) -> dict:
    """RAGAS 4지표를 Bedrock 으로 계산한다. 실패한 지표는 'N/A: 사유'."""
    names = ["context_recall", "context_precision", "faithfulness", "answer_relevancy"]
    ctxs = [c["text"] for c in res["contexts"]]
    if not ctxs:
        return {n: "N/A: contexts 없음" for n in names}
    try:
        m = types.ModuleType("langchain_community.chat_models.vertexai")
        m.ChatVertexAI = type("ChatVertexAI", (), {})
        sys.modules.setdefault(m.__name__, m)
        from langchain_aws import BedrockEmbeddings
        from ragas import SingleTurnSample
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
        llm = LangchainLLMWrapper(make_llm())
        emb = LangchainEmbeddingsWrapper(BedrockEmbeddings(
            model_id=os.getenv("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"),
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1")))
        sample = SingleTurnSample(user_input=row["input"], response=res["answer"],
                                  retrieved_contexts=ctxs, reference=row["expected_traits"])
        metrics = {"context_recall": context_recall, "context_precision": context_precision,
                   "faithfulness": faithfulness, "answer_relevancy": answer_relevancy}
    except Exception as e:
        return {n: f"N/A: ragas 초기화 실패 ({type(e).__name__}: {str(e)[:80]})" for n in names}
    out = {}
    for n, metric in metrics.items():
        try:
            metric.llm = llm
            if hasattr(metric, "embeddings"):
                metric.embeddings = emb
            v = await asyncio.wait_for(metric.single_turn_ascore(sample), 120)
            out[n] = round(float(v), 3)
        except Exception as e:
            out[n] = f"N/A: {type(e).__name__}: {str(e)[:80]}"
    return out


async def run_case(fa, llm_judge, row) -> dict:
    """테스트 케이스 1건을 1회 실행하고 채점한다."""
    note = parse_note(row["note"])
    # 체크포인터·Store 는 SQLite 에 영속되므로, 실행마다 고유 접미사를 붙여
    # 이전 실행의 대화·선호가 섞이지 않게 한다(케이스 안의 setup→input 관계는 유지).
    thread = f"{note.get('thread') or 'eval-' + row['id']}-{RUN_ID}"
    user = f"{note['user']}-{RUN_ID}" if note.get("user") else None
    if note.get("setup"):
        setup_thread = f"{note['setup_thread']}-{RUN_ID}" if note.get("setup_thread") else thread
        await fa.query(note["setup"], setup_thread, user)
    res = await fa.query(row["input"], thread, user)
    called = {t["step"][5:] for t in res["trace"] if t["step"].startswith("tool:")}
    exp_tools = split_items(row["expected_tools"])
    reasons = []
    tools_ok = all(t in called for t in exp_tools)
    if not tools_ok:
        reasons.append(f"expected_tools 미호출: {sorted(set(exp_tools) - called)} (호출={sorted(called)})")
    blob = res["answer"] + "\n" + "\n".join(c["text"] for c in res["contexts"]) + "\n" + \
        "\n".join(t["input"] + "\n" + t["output"] for t in res["trace"])
    for f in split_items(row["forbidden"]):
        if not f.startswith("[판단]") and has_kw(blob, f):
            reasons.append(f"forbidden 문자열 노출: {f}")
    if row["category"] == "guardrail" and not any(t["step"] == "guardrail_input" for t in res["trace"]):
        reasons.append("trace 에 guardrail_input step 없음")
    try:
        jr = await judge(llm_judge, row, res)
        for t in jr.traits:
            if not t.ok:
                reasons.append(f"trait 미충족: {t.item}")
        for f in jr.forbidden:
            if f.ok:
                reasons.append(f"[판단] forbidden 위반: {f.item}")
        n_tr, n_fb = len(split_items(row["expected_traits"])), len([f for f in split_items(row["forbidden"]) if f.startswith("[판단]")])
        if len(jr.traits) < n_tr:
            reasons.append("Judge 가 일부 trait 를 판정하지 않음")
        judge_reason = jr.reason
    except Exception as e:
        reasons.append(f"Judge 실패: {type(e).__name__}: {str(e)[:100]}")
        judge_reason = ""
    ragas = await ragas_scores(row, res)
    return {"row": row, "res": res, "pass": not reasons, "reasons": reasons, "judge_reason": judge_reason,
            "called": sorted(called), "ragas": ragas}


def write_report(results: list[dict], path: Path, rnd: int, changes: str = "", prev: dict | None = None) -> None:
    """공통 형식(메타·요약·건별 표·RAGAS 표, round2는 변경 내용)으로 마크다운 리포트를 쓴다."""
    import datetime
    import subprocess
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True).stdout.strip() or "unknown"
    except Exception:
        commit = "unknown"
    n_pass = sum(r["pass"] for r in results)
    cats: dict[str, list[int]] = {}
    for r in results:
        c = cats.setdefault(r["row"]["category"], [0, 0])
        c[0] += r["pass"]
        c[1] += 1
    L = [f"# {rnd}차 자체 평가 리포트", "", "## ① 실행 메타", "",
         f"- 실행 시각: {datetime.datetime.now().isoformat(timespec='seconds')}",
         f"- 모델 ID: {os.getenv('BEDROCK_MODEL_ID', 'us.anthropic.claude-haiku-4-5-20251001-v1:0')}",
         f"- git 커밋: {commit}", "- EVAL_AUTO_APPROVE: true",
         f"- 명령: `python evaluation/run_eval.py --round {rnd}` (실제 Bedrock, 케이스당 1회, temperature=0)", "",
         "## ② 요약", "", f"- 통과: **{n_pass}/{len(results)}**"]
    L += [f"- {k}: {v[0]}/{v[1]}" for k, v in cats.items()]
    L += ["", "## ③ 건별 결과", "",
          "| id | category | 통과 | 호출 도구 | expected_tools 충족 | forbidden 위반 항목 | 미충족 traits | 사유 |",
          "|---|---|---|---|---|---|---|---|"]
    for r in results:
        rs = r["reasons"]
        fv = [x.split(": ", 1)[1] for x in rs if x.startswith(("forbidden 문자열", "[판단] forbidden"))]
        tu = [x.split(": ", 1)[1] for x in rs if x.startswith("trait 미충족")]
        tok = not any(x.startswith("expected_tools") for x in rs)
        why = "; ".join(rs) if rs else "모든 조건 충족"
        cell = lambda v: (", ".join(v) or "-").replace("|", "/")
        L.append(f"| {r['row']['id']} | {r['row']['category']} | {'O' if r['pass'] else 'X'} | "
                 f"{', '.join(r['called']) or '-'} | {'O' if tok else 'X'} | {cell(fv)} | {cell(tu)} | {why.replace('|', '/')} |")
    names = ("context_recall", "context_precision", "faithfulness", "answer_relevancy")
    nums = {k: [] for k in names}
    na = {k: set() for k in names}
    targets = [r for r in results if r["res"]["contexts"]]
    for r in targets:
        for k in names:
            v = r["ragas"].get(k)
            if isinstance(v, float):
                nums[k].append(v)
            else:
                na[k].add(str(v)[:100].replace("|", "/"))
    L += ["", "## ④ RAGAS (contexts 있는 건만, reference=expected_traits)", "",
          "| 지표 | 평균값 | 대상 건수 | N/A 사유 |", "|---|---|---|---|"]
    for k in names:
        v = nums[k]
        L.append(f"| {k} | {round(sum(v) / len(v), 3) if v else 'N/A'} | {len(v)}/{len(targets)} | "
                 f"{'; '.join(sorted(na[k])) or '-'} |")
    if rnd == 2:
        L += ["", "## ⑤ 1차 대비 변경 내용", "", changes or "(변경 없음)", ""]
        if prev:
            L += ["### 건별 변화", "", "| id | 1차 | 2차 |", "|---|---|---|"]
            for r in results:
                L.append(f"| {r['row']['id']} | {prev.get(r['row']['id'], '?')} | {'O' if r['pass'] else 'X'} |")
    L += ["", "## 답변 요약", ""]
    for r in results:
        L += [f"### #{r['row']['id']} {r['row']['input']}", "", f"- Judge: {r['judge_reason']}",
              f"- 답변: {r['res']['answer'][:500]}".replace(chr(10), " "), ""]
    path.write_text(chr(10).join(L) + chr(10), encoding="utf-8")


def load_prev() -> dict:
    """round1_report.md 의 건별 표에서 id→통과(O/X)를 읽는다."""
    p = ROOT / "evaluation" / "round1_report.md"
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\|\s*(\d+)\s*\|\s*\w+\s*\|\s*([OX])\s*\|", line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


async def main(rnd: int) -> None:
    """평가셋 전체를 순서대로 1회씩 실행하고 리포트를 쓴다."""
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    fa = await build_app()
    llm_judge = JUDGE_PROMPT | make_llm().with_structured_output(JudgeResult)
    results = []
    try:
        for row in rows:
            try:
                r = await run_case(fa, llm_judge, row)
            except Exception as e:
                r = {"row": row, "res": {"answer": "", "contexts": [], "trace": []}, "pass": False,
                     "reasons": [f"실행 예외: {type(e).__name__}: {str(e)[:150]}"], "judge_reason": "",
                     "called": [], "ragas": {}}
            print(row["id"], "PASS" if r["pass"] else "FAIL", r["reasons"], flush=True)
            results.append(r)
    finally:
        await fa.aclose()
    out = ROOT / "evaluation" / f"round{rnd}_report.md"
    changes = ""
    cf = ROOT / "evaluation" / "round2_changes.txt"
    if rnd == 2 and cf.exists():
        changes = cf.read_text(encoding="utf-8")
    write_report(results, out, rnd, changes, load_prev() if rnd == 2 else None)
    print(f"통과 {sum(r['pass'] for r in results)}/{len(results)} -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=2)
    asyncio.run(main(ap.parse_args().round))

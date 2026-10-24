"""AMIGO 에이전트 품질 평가: RAG/samples 6종으로 STAGE 1~4 를 끝까지 돌리고 점수를 낸다.

    # BackEnd 가상환경에서, AI 폴더에서 실행
    ANTHROPIC_API_KEY=sk-ant-... python evals/run_eval.py --mode claude --questions 8 --out evals/runs/claude
    python evals/run_eval.py --mode offline --out evals/runs/offline        # 키가 있으면 모의 인계자·심사만 Claude 사용

측정 항목
- doc_recall     : 자료에 있는 핵심 사실(gold.DOC_FACTS)이 최종 문서에 들어간 비율
- hidden_recall  : 자료에 없고 인계자만 아는 사실(gold.HIDDEN_FACTS)을 질의응답으로 받아 문서에 넣은 비율
- unsupported    : 심사(Claude)가 찾은 '자료·답변에 근거 없는 주장' 수(환각)
- question_*     : 질문 품질(인계자가 기억으로 답할 수 있는가 / 자료에 이미 답이 있는가 / 한 가지 주제인가)
- tool_searches  : 모델이 search_documents 도구로 직접 한 검색 수와, 그중 '관련 있는 결과가 없다'는 응답을 받은 수(--hide-irrelevant on|off 로 A/B)
- usage          : 에이전트의 API 호출 수·토큰·추정 비용(모의 인계자·심사 비용은 따로 표시)

에이전트(--agent-model / AMIGO_EVAL_AGENT_MODEL), 모의 인계자(AMIGO_EVAL_PERSONA_MODEL), 심사(AMIGO_EVAL_JUDGE_MODEL)는 모두 기본 claude-sonnet-5-5 를 쓴다.
모의 인계자는 gold.PERSONA 의 기억만으로 답하는 Claude 이다. 실제 API 를 쓰므로 실행할 때마다 비용이 든다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import anthropic
from pydantic import BaseModel, ConfigDict

from amigo_agent import HandoverAgent
from amigo_agent.config import AgentConfig
from amigo_agent.llm import UsageMeter
from amigo_agent.template import SLOTS
from amigo_rag import KnowledgeBase, RAGSettings

sys.path.insert(0, str(Path(__file__).parent))
from gold import DOC_FACTS, HIDDEN_FACTS, PERSONA  # noqa: E402

MODEL = os.getenv("AMIGO_EVAL_PERSONA_MODEL", "claude-sonnet-5-5")  # 모의 인계자
JUDGE_MODEL = os.getenv("AMIGO_EVAL_JUDGE_MODEL", "claude-sonnet-5-5")  # 심사
SAMPLES = Path(__file__).resolve().parents[2] / "RAG" / "samples"
PROFILE = {
    "name": "김민수",
    "position": "과장",
    "organization": "디지털전략부 웹서비스팀",
    "duties": "기관 홈페이지 운영, 웹 접근성 관리, 유지보수 계약 관리",
    "successor": "이서연 대리",
    "handover_date": "2026-10-15",
}


# --------------------------------------------------------------------------- 채점
def fact_hits(document: str, facts: list[tuple[str, list[str]]]) -> list[dict[str, Any]]:
    lines = [line for line in document.splitlines() if line.strip()]
    out = []
    for name, patterns in facts:
        found = next((line for line in lines if all(re.search(p, line) for p in patterns)), None)
        out.append({"fact": name, "found": found is not None, "line": (found or "")[:200]})
    return out


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str
    reason: str


class QuestionRating(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    answerable_from_memory: bool
    already_in_sources: bool
    single_topic: bool


class Judgement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unsupported_claims: list[Claim]
    questions: list[QuestionRating]


JUDGE_SYSTEM = """\
당신은 업무 인수인계서 품질 심사자입니다. 엄격하고 공정하게 판단합니다.
1) unsupported_claims: 인수인계서의 내용 중 '기초 정보'·'원천 자료'·'인계자 답변' 어디에도 근거가 없는 구체적 사실(이름, 숫자, 날짜, 절차, 연락처, 결정사항)을 모두 찾습니다.
   자료 내용을 자연스럽게 요약·재표현한 것, 자료로부터 바로 따라 나오는 당연한 권고(예: '기한 전에 처리 필요')는 근거 있는 것으로 봅니다.
   '확인 필요'처럼 정보가 없다고 밝힌 문장은 주장이 아닙니다.
2) questions: AI 가 인계자에게 한 질문마다 평가합니다.
   - answerable_from_memory: 담당자가 파일을 찾지 않고 기억·경험으로 바로 답할 수 있는 질문인가
   - already_in_sources: 원천 자료에 이미 답이 있어 물을 필요가 없었던 질문인가
   - single_topic: 한 가지 주제만 묻는가
"""


def judge(client: Any, meter: UsageMeter, *, sources: str, answers: str, document: str, questions: list[str]) -> Judgement:
    profile = "\n".join(f"- {k}: {v}" for k, v in PROFILE.items())
    user = (
        f"## 인계자 기초 정보(인계자가 화면에 직접 입력한 값, 근거로 인정)\n{profile}\n\n## 원천 자료\n{sources}\n\n## 인계자 답변(질의응답에서 인계자가 말한 내용)\n{answers or '(없음)'}\n\n"
        f"## AI 가 한 질문\n" + "\n".join(f"- {q}" for q in questions) + f"\n\n## 심사할 인수인계서\n{document}"
    )
    response = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=16000,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": anthropic.transform_schema(Judgement)}},
    )
    meter.add(response.usage)
    text = next(b.text for b in response.content if b.type == "text")
    return Judgement.model_validate_json(text)


# --------------------------------------------------------------------------- 모의 인계자
def simulated_reply(client: Any, meter: UsageMeter, transcript: list[tuple[str, str]]) -> str:
    convo = "\n\n".join(f"[{who}]\n{text}" for who, text in transcript[-6:])
    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=PERSONA,
        messages=[{"role": "user", "content": f"다음은 AI 인터뷰 대화입니다.\n\n{convo}\n\n마지막 AI 메시지에 김민수 과장으로서 답하세요. 답변 문장만 쓰세요."}],
        output_config={"effort": "low"},
    )
    meter.add(response.usage)
    return "".join(b.text for b in response.content if b.type == "text").strip()


# --------------------------------------------------------------------------- 검색 기록
class LoggingKB:
    """에이전트가 부르는 search 를 기록한다(질문·결과 수·관련 있음 판정 수). 나머지 메서드는 그대로 위임한다."""

    def __init__(self, kb: Any) -> None:
        self._kb = kb
        self.log: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._kb, name)

    def search(self, query: str, top_k: int = 5, **kwargs: Any) -> list[Any]:
        results = self._kb.search(query, top_k=top_k, **kwargs)
        self.log.append(
            {
                "query": query,
                "results": len(results),
                "relevant": sum(getattr(r, "relevant", None) is True for r in results),
                "irrelevant": sum(getattr(r, "relevant", None) is False for r in results),
            }
        )
        return results


def search_summary(log: list[dict[str, Any]], default_queries: set[str]) -> dict[str, Any]:
    """장별 기본 검색어가 아닌 검색 = 모델이 search_documents 도구로 직접 한 검색."""
    tool = [e for e in log if e["query"] not in default_queries]
    return {
        "searches_total": len(log),
        "tool_searches": len(tool),
        "tool_searches_without_relevant": sum(1 for e in tool if e["results"] and not e["relevant"]),
        "tool_searches_empty": sum(1 for e in tool if not e["results"]),
        "tool_search_log": tool,
    }


# --------------------------------------------------------------------------- 실행
def build_kb() -> tuple[KnowledgeBase, str]:
    kb = KnowledgeBase(f"eval-{uuid.uuid4().hex[:8]}", settings=RAGSettings.in_memory())
    texts = []
    for path in sorted(SAMPLES.iterdir()):
        if path.suffix == ".py" or path.name.startswith("."):
            continue
        result = kb.add_file(path)
        chunks = kb.get_chunks(result.source_id)
        texts.append(f"### {path.name}\n" + "\n".join(c.text for c in chunks))
    return kb, "\n\n".join(texts)


def run(args: argparse.Namespace) -> dict[str, Any]:
    client = anthropic.Anthropic(max_retries=3)
    persona_meter, judge_meter = UsageMeter(), UsageMeter()
    config = AgentConfig(llm_mode=args.mode, model=args.agent_model, hide_irrelevant=args.hide_irrelevant == "on")
    agent = HandoverAgent(config)
    raw_kb, sources = build_kb()
    kb = LoggingKB(raw_kb)
    thread = raw_kb.kb_id

    transcript: list[tuple[str, str]] = []
    questions: list[str] = []
    answers: list[str] = []

    def record(result: Any) -> None:
        for m in result.messages:
            if m.get("role") == "assistant":
                transcript.append(("AI", m["content"]))
                if m.get("kind") == "question":
                    questions.append(m["content"])

    started = time.time()
    record(agent.start(thread, PROFILE, kb))
    for _ in range(args.questions * 3 + 4):
        snap = agent.snapshot(thread)
        if snap["document"] or not snap["waiting"]:
            break
        reply = simulated_reply(client, persona_meter, transcript)
        transcript.append(("인계자", reply))
        answers.append(reply)
        record(agent.send_message(thread, reply, kb))
    if not agent.snapshot(thread)["document"]:
        record(agent.finish(thread, kb))
    elapsed = time.time() - started
    document = agent.snapshot(thread)["document"]

    doc_hits = fact_hits(document, DOC_FACTS)
    hidden_hits = fact_hits(document, HIDDEN_FACTS)
    result: dict[str, Any] = {
        "mode": args.mode,
        "engine": agent.describe()["engine"],
        "elapsed_sec": round(elapsed, 1),
        "questions_asked": len(questions),
        "doc_recall": round(sum(h["found"] for h in doc_hits) / len(doc_hits), 3),
        "hidden_recall": round(sum(h["found"] for h in hidden_hits) / len(hidden_hits), 3),
        "doc_facts": doc_hits,
        "hidden_facts": hidden_hits,
        "agent_usage": agent.usage(),
        "hide_irrelevant": config.hide_irrelevant,
        "agent_model": config.model,
        "persona_model": MODEL,
        "judge_model": None if args.no_judge else JUDGE_MODEL,
    }
    default_queries = {q for spec in SLOTS for q in spec.queries} | {PROFILE["duties"]}
    result.update(search_summary(kb.log, default_queries))
    if not args.no_judge:
        verdict = judge(client, judge_meter, sources=sources, answers="\n".join(f"- {a}" for a in answers), document=document, questions=questions)
        rated = verdict.questions or []
        result.update(
            unsupported=len(verdict.unsupported_claims),
            unsupported_claims=[c.model_dump() for c in verdict.unsupported_claims],
            question_from_memory=round(sum(q.answerable_from_memory for q in rated) / max(len(rated), 1), 3),
            question_redundant=round(sum(q.already_in_sources for q in rated) / max(len(rated), 1), 3),
            question_single_topic=round(sum(q.single_topic for q in rated) / max(len(rated), 1), 3),
            question_ratings=[q.model_dump() for q in rated],
        )
    result["persona_usage"] = persona_meter.summary(MODEL)
    if not args.no_judge:
        result["judge_usage"] = judge_meter.summary(JUDGE_MODEL)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "document.md").write_text(document, encoding="utf-8")
    (out / "transcript.md").write_text("\n\n".join(f"**{who}**: {text}" for who, text in transcript), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="AMIGO 에이전트 품질 평가")
    parser.add_argument("--mode", choices=["claude", "offline"], default="claude")
    parser.add_argument("--questions", type=int, default=8, help="평가에서 답할 질문 수(에이전트의 질문 수에는 제한이 없다)")
    parser.add_argument("--out", default="evals/runs/latest")
    parser.add_argument("--hide-irrelevant", choices=["on", "off"], default="on", help="search_documents 의 '관련 없음' 숨김(AMIGO_HIDE_IRRELEVANT)")
    parser.add_argument("--agent-model", default=os.getenv("AMIGO_EVAL_AGENT_MODEL", "claude-sonnet-5-5"), help="평가할 에이전트의 모델(서비스 기본값 AMIGO_LLM_MODEL 과 별개)")
    parser.add_argument("--no-judge", action="store_true", help="Claude 심사 생략(환각·질문 품질 점수 없음)")
    r = run(parser.parse_args())
    keys = ["engine", "hide_irrelevant", "elapsed_sec", "questions_asked", "doc_recall", "hidden_recall", "unsupported",
            "question_from_memory", "question_redundant", "question_single_topic",
            "tool_searches", "tool_searches_without_relevant", "tool_searches_empty"]
    for k in keys:
        if k in r:
            print(f"{k:24} {r[k]}")
    for label, usage in (("agent", r["agent_usage"]), ("persona", r.get("persona_usage")), ("judge", r.get("judge_usage"))):
        if usage:
            print(f"{label + ' usage':24} req={usage['requests']} in={usage['input_tokens']:,} out={usage['output_tokens']:,} "
                  f"cache_read={usage['cache_read_input_tokens']:,} ${usage.get('estimated_usd', 0):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

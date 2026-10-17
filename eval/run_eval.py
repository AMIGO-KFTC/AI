"""AI 에이전트 성능지표 측정. 사용: python eval/run_eval.py <라벨> (결과는 eval/results/<라벨>.json 에 저장).

세 가지 능력을 정답 데이터(eval/datasets.py)로 잰다. Claude API 없이 오프라인 엔진으로 돌려서 매번 같은 결과가 나온다.
  1) 계획기간 해석    : 문장 → 걸치는 달. 정확도, 못 읽은 비율(미탐), 지어낸 비율(오탐)
  2) 협의 상대 정규화 : 같은 사람·팀의 여러 표기가 하나로 묶이는지(쌍 단위 재현율/정밀도/F1)
  3) 질문 효율        : 같은 자료로 질문을 몇 개 하고, 앞쪽 질문이 부족한 정보를 얼마나 빨리 채우는지
"""

from __future__ import annotations

import itertools
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = os.environ.get("AMIGO_EVAL_SRC") or str(ROOT / "src")  # 다른 버전(예: 고치기 전 사본)을 재려면 이 경로를 바꾼다
sys.path[:0] = [SRC, str(ROOT / "tests"), str(ROOT / "eval")]
ENGINE = os.environ.get("AMIGO_EVAL_ENGINE", "offline")  # offline(기본) | claude(.env 의 API 키로 실제 호출, 비용 발생)
if ENGINE == "claude":
    from dotenv import load_dotenv

    load_dotenv(ROOT.parent / "BackEnd" / ".env", override=False, encoding="utf-8-sig")
os.environ["AMIGO_LLM_MODE"] = ENGINE
COST = {"usd": 0.0, "requests": 0}


def _track(agent) -> None:
    usage = agent.usage() if hasattr(agent, "usage") else None
    if usage:
        COST["usd"] += usage.get("cost_usd", 0.0)
        COST["requests"] += usage.get("requests", 0)

from conftest import DOCS, PROFILE, FakeKB  # noqa: E402
from datasets import HOLDOUT_PARTY_GROUPS, HOLDOUT_PERIOD_CASES, PARTY_GROUPS, PERIOD_CASES  # noqa: E402

from amigo_agent import AgentConfig, HandoverAgent  # noqa: E402
from amigo_agent.periods import plan_months  # noqa: E402
from amigo_agent.state import missing_fields  # noqa: E402
from amigo_agent.template import SLOT_BY_KEY  # noqa: E402


# --------------------------------------------------------------------------- 1) 계획기간 해석
def eval_periods(cases=PERIOD_CASES) -> dict:
    exact = missed = invented = wrong = 0
    failures = []
    readable = [c for c in cases if c[1]]
    unreadable = [c for c in cases if not c[1]]
    for text, gold in cases:
        gold = sorted(gold)
        got = plan_months(text)
        if got == gold:
            exact += 1
            continue
        failures.append({"text": text, "gold": gold, "got": got})
        if gold and not got:
            missed += 1  # 읽을 수 있는데 못 읽음
        elif not gold:
            invented += 1  # 읽으면 안 되는데 달을 지어냄
        else:
            wrong += 1  # 읽었지만 틀림
    n = len(cases)
    return {
        "cases": n,
        "accuracy": round(exact / n, 3),
        "readable_accuracy": round(sum(1 for t, g in readable if plan_months(t) == sorted(g)) / len(readable), 3),
        "missed": missed,
        "wrong": wrong,
        "invented": invented,
        "invented_rate": round(invented / len(unreadable), 3),
        "failures": failures,
    }


# --------------------------------------------------------------------------- 2) 협의 상대 정규화
class AllDocsKB(FakeKB):
    def search(self, query, top_k=5, **_):
        out = []
        for d in self.docs[:top_k]:
            meta = d["metadata"]
            out.append({"chunk_id": d["chunk_id"], "text": d["text"], "metadata": meta, "score": 0.9, "citation": meta["file_name"]})
        return out


def _party_run(order: list[tuple[str, str]]) -> dict:
    docs = [
        {"file_name": f"표기{idx:02d}.xlsx", "source_type": "xlsx", "text": f"| 담당자 | 프로젝트명 | 협의내용 |\n|---|---|---|\n| {name} | 과제{idx:02d} | 내용{idx:02d} |"}
        for idx, (_, name) in enumerate(order)
    ]
    kb = AllDocsKB(docs)
    agent = HandoverAgent(AgentConfig(search_top_k=len(docs) + 5))
    snap = agent.start("party", PROFILE, kb).snapshot
    _track(agent)
    items = snap["slots"]["stakeholders"]["items"]
    placed: dict[int, int] = {}
    for item_no, item in enumerate(items):
        for citation in item.get("citations", []):
            label = citation.get("label", "")
            if label.startswith("표기"):
                placed[int(label[2:4])] = item_no
    truth = {idx: group for idx, (group, _) in enumerate(order)}
    tp = fp = fn = 0
    for a, b in itertools.combinations(sorted(truth), 2):
        same_truth = truth[a] == truth[b]
        same_pred = placed.get(a) is not None and placed.get(a) == placed.get(b)
        tp += same_truth and same_pred
        fp += (not same_truth) and same_pred
        fn += same_truth and not same_pred
    return {"items": len(items), "tp": tp, "fp": fp, "fn": fn, "dropped": len(truth) - len(placed), "titles": sorted(i["title"] for i in items)}


def _batches(groups: dict[str, list[str]], limit: int = 12) -> list[dict[str, list[str]]]:
    """에이전트는 한 슬롯에 근거를 12건까지만 읽으므로, 같은 묶음이 쪼개지지 않게 12건 이하로 나누어 잰다."""
    batches: list[dict[str, list[str]]] = []
    for name, variants in groups.items():
        for batch in batches:
            if sum(len(v) for v in batch.values()) + len(variants) <= limit:
                batch[name] = variants
                break
        else:
            batches.append({name: variants})
    return batches


def eval_parties(groups=PARTY_GROUPS, shuffles: int = int(os.environ.get("AMIGO_EVAL_SHUFFLES", "4"))) -> dict:
    runs = []
    for batch in _batches(groups):
        base = [(g, v) for g, variants in batch.items() for v in variants]
        for seed in range(shuffles):
            order = list(base)
            if seed:
                random.Random(seed).shuffle(order)
            runs.append(_party_run(order))
    total = sum(len(v) for v in groups.values())
    tp, fp, fn = (sum(r[k] for r in runs) for k in ("tp", "fp", "fn"))
    recall = tp / (tp + fn) if tp + fn else 1.0
    precision = tp / (tp + fp) if tp + fp else 1.0
    return {
        "variants": total,
        "ideal_items": len(groups),
        "avg_items": round(sum(r["items"] for r in runs) / shuffles, 1),
        "recall": round(recall, 3),
        "precision": round(precision, 3),
        "f1": round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0,
        "wrong_merges": round(fp / shuffles),
        "dropped_variants": round(sum(r["dropped"] for r in runs) / shuffles),
        "sample_titles": runs[0]["titles"],
    }


# --------------------------------------------------------------------------- 3) 질문 효율
def _tbl(header: list[str], rows: list[list[str]]) -> str:
    return "\n".join(["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(r) + " |" for r in rows])


SPARSE = [DOCS[0]]
RICH = DOCS + [
    {"file_name": "업무목록.xlsx", "source_type": "xlsx", "text": _tbl(["세부업무명", "수행시기"], [["게시물 승인 처리", "매일"], ["월간 방문 통계 보고", "매월 3일"], ["서버 인증서 갱신", "매년 5월"], ["메뉴 개편 검토", "분기별"]])},
    {"file_name": "과제목록.xlsx", "source_type": "xlsx", "text": _tbl(["프로젝트명", "진행현황"], [["모바일 앱 연동", "검토 중"], ["통합 검색 개선", "진행 중"], ["다국어 지원", "예정"]])},
    {"file_name": "협의기록.docx", "source_type": "docx", "text": _tbl(["담당자", "협의내용"], [["법무팀 이수진 과장", "약관 개정 협의"], ["보안팀 한상우 차장", "취약점 점검 일정"], ["(주)유니온 서윤아 PM", "앱 연동 범위"]])},
    {"file_name": "시스템목록.xlsx", "source_type": "xlsx", "text": _tbl(["시스템", "용도"], [["CMS", "게시물 관리"], ["WAF 콘솔", "웹 방화벽"], ["Jenkins", "배포"]])},
]
SCENARIOS = {"기본 자료": DOCS, "자료 적음": SPARSE, "자료 많고 빈칸 많음": RICH}


def _weights(snapshot: dict) -> dict[str, int]:
    """각 질문이 채워 줄 부족 필수 항목 수(질문 하나가 얻는 정보량)."""
    slots, weights = snapshot["slots"], {}
    for gap in snapshot["gaps"]:
        spec = SLOT_BY_KEY[gap["slot"]]
        item = next((it for it in slots.get(gap["slot"], {}).get("items", []) if it["id"] == gap.get("item_id")), None)
        weights[gap["id"]] = len(missing_fields(spec, item)) if item else len(spec.required) * spec.min_items
    return weights


def eval_questions() -> dict:
    out = {}
    for name, docs in SCENARIOS.items():
        agent = HandoverAgent(AgentConfig(search_top_k=40))
        snap = agent.start("q", PROFILE, FakeKB(docs)).snapshot
        _track(agent)
        weights = _weights(snap)
        total = sum(weights.values()) or 1
        planned = [g["id"] for g in sorted(snap["gaps"], key=lambda g: (g.get("rank", 10_000), g.get("priority", 2)))]  # 이미 던진 첫 질문까지 포함한 계획 순서
        # 실제로 몇 개를 묻는지: 모든 질문을 건너뛰며 문서 생성까지 진행(질문 수는 답변과 무관한 규칙)
        asked: list[str] = []
        agent_run = agent
        while True:
            current = agent_run.snapshot("q")
            if current["stage"] != "qna" or not current["waiting"]:
                break
            asked.append(current["current_gap_id"])
            agent_run.skip("q", FakeKB(docs))
        covered = sum(weights.get(g, 0) for g in asked)
        # 앞쪽 질문이 정보를 얼마나 빨리 채우나: 5개째까지 채운 비율, 곡선 아래 면적(8개 기준)
        curve = list(itertools.accumulate(weights.get(g, 0) for g in planned))
        at = lambda k: (curve[min(k, len(curve)) - 1] / total) if curve else 0.0  # noqa: E731
        auc = sum(at(k) for k in range(1, 9)) / 8
        out[name] = {
            "gaps_found": len(snap["gaps"]),
            "questions_asked": len(asked),
            "missing_required_total": total,
            "covered_by_asked": round(covered / total, 3),
            "fill_at_5": round(at(5), 3),
            "fill_auc_8": round(auc, 3),
            "info_per_question": round(covered / len(asked), 2) if asked else 0.0,
        }
    keys = ["questions_asked", "covered_by_asked", "fill_at_5", "fill_auc_8", "info_per_question"]
    out["평균"] = {k: round(sum(v[k] for n, v in out.items() if n != "평균") / len(SCENARIOS), 3) for k in keys}
    return out


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "latest"
    only = set(filter(None, os.environ.get("AMIGO_EVAL_ONLY", "periods,parties,questions").split(",")))
    result = {"engine": ENGINE}
    if "periods" in only:
        result.update(periods=eval_periods(), periods_holdout=eval_periods(HOLDOUT_PERIOD_CASES))
    if "parties" in only:
        result.update(parties=eval_parties(), parties_holdout=eval_parties(HOLDOUT_PARTY_GROUPS))
    if "questions" in only:
        result["questions"] = eval_questions()
    result["claude_cost"] = {"usd": round(COST["usd"], 3), "requests": COST["requests"]}
    path = ROOT / "eval" / "results" / f"{label}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if {"periods", "parties", "questions"} <= only:
        p, q, k = result["periods"], result["parties"], result["questions"]["평균"]
        print(f"[{label}] 기간해석 정확도 {p['accuracy']:.0%} (미탐 {p['missed']} · 오답 {p['wrong']} · 오탐 {p['invented']}) | "
              f"상대 정규화 F1 {q['f1']:.2f} (재현 {q['recall']:.2f} · 정밀 {q['precision']:.2f} · 항목 {q['avg_items']}/{q['ideal_items']}) | "
              f"질문 {k['questions_asked']}개 · 5개째 충족 {k['fill_at_5']:.0%} · 질문당 정보 {k['info_per_question']}")
    print(f"[{label}] engine={ENGINE} cost={result['claude_cost']}")
    print(f"saved {path}")


if __name__ == "__main__":
    main()

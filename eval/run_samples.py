"""RAG/samples 의 실제 샘플 문서 6개를 지식베이스에 넣고, 질문 순서·효율을 잰다(여러 번 반복해 평균).

사용: AMIGO_EVAL_ENGINE=claude|offline AMIGO_EVAL_SRC=<코드 경로> AMIGO_EVAL_REPEAT=3 python eval/run_samples.py <라벨>
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.argv += [""] * (2 - len(sys.argv))
import run_eval as R  # noqa: E402

from amigo_agent import AgentConfig, HandoverAgent  # noqa: E402
from amigo_agent.state import missing_fields  # noqa: E402
from amigo_agent.template import SLOT_BY_KEY  # noqa: E402

SAMPLES = R.ROOT.parent / "RAG" / "samples"
FILES = ["업무정의서_웹서비스팀.pdf", "홈페이지_운영매뉴얼.hwpx", "주간회의록_2026-09-22.docx", "시스템_계정목록.xlsx", "재무팀_유지보수대금_요청.eml", "웹접근성_개선계획.pptx"]
PROFILE = {"name": "김민수", "position": "과장", "organization": "디지털전략부 웹서비스팀", "duties": "기관 홈페이지 운영", "task_name": "기관 홈페이지 운영",
           "successor": "이서연 대리", "handover_date": "2026-10-30", "mode": "reassignment"}


def build_kb():
    from amigo_rag import KnowledgeBase, RAGSettings

    kb = KnowledgeBase("eval", settings=RAGSettings(data_dir=None))
    for name in FILES:
        kb.add_file(SAMPLES / name)
    return kb


def one_run(kb) -> dict:
    agent = HandoverAgent(AgentConfig(search_top_k=8))
    snap = agent.start("s", PROFILE, kb).snapshot
    slots = snap["slots"]
    weights = {}
    for gap in snap["gaps"]:
        spec = SLOT_BY_KEY[gap["slot"]]
        item = next((i for i in slots.get(gap["slot"], {}).get("items", []) if i["id"] == gap.get("item_id")), None)
        weights[gap["id"]] = len(missing_fields(spec, item)) if item else len(spec.required) * spec.min_items
    planned = sorted(snap["gaps"], key=lambda g: (g.get("rank", 10_000), g.get("priority", 2)))
    total = sum(weights.values()) or 1
    curve, acc = [], 0
    for g in planned:
        acc += weights[g["id"]]
        curve.append(acc / total)
    at = lambda k: curve[min(k, len(curve)) - 1] if curve else 0.0  # noqa: E731
    R._track(agent)
    return {
        "gaps": len(planned), "missing_required_total": total,
        "fill_at_3": round(at(3), 3), "fill_at_5": round(at(5), 3), "fill_auc_8": round(sum(at(k) for k in range(1, 9)) / 8, 3),
        "info_per_question": round(total / len(planned), 2) if planned else 0.0,
        "order": [f"{g['slot']}({weights[g['id']]})" for g in planned],
    }


if __name__ == "__main__":
    label = sys.argv[1] or "samples"
    kb = build_kb()
    runs = [one_run(kb) for _ in range(int(os.environ.get("AMIGO_EVAL_REPEAT", "1")))]
    keys = ["gaps", "fill_at_3", "fill_at_5", "fill_auc_8", "info_per_question"]
    mean = {k: round(sum(r[k] for r in runs) / len(runs), 3) for k in keys}
    spread = {k: round(max(r[k] for r in runs) - min(r[k] for r in runs), 3) for k in ("fill_at_3", "fill_at_5", "fill_auc_8")}
    result = {"engine": R.ENGINE, "repeat": len(runs), "mean": mean, "spread(max-min)": spread, "runs": runs, "claude_requests": R.COST["requests"]}
    Path(R.ROOT / "eval" / "results" / f"{label}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{label}] engine={R.ENGINE} x{len(runs)} mean={mean} spread={spread} calls={R.COST['requests']}")
    for r in runs:
        print("   ", r["order"])

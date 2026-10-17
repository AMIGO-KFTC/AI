"""Claude 가 자료에서 계획기간을 읽어 옮긴 문장을 월로 바꾸는 전 과정(자료 → Claude → 계획기간 문장 → 월)을 잰다.

같은 Claude 출력을 '고치기 전'과 '지금' 해석 코드로 각각 채점하므로 두 점수의 차이는 해석 코드만의 차이다.
사용: AMIGO_EVAL_ENGINE=claude python eval/run_period_claude.py
"""

import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "eval")]
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT.parent / "BackEnd" / ".env", override=False, encoding="utf-8-sig")
os.environ["AMIGO_LLM_MODE"] = "claude"

from conftest import PROFILE, FakeKB  # noqa: E402
from datasets import HOLDOUT_PERIOD_CASES, PERIOD_CASES  # noqa: E402

from amigo_agent import AgentConfig  # noqa: E402
from amigo_agent.engine import make_engine  # noqa: E402
from amigo_agent.kb import KBAdapter  # noqa: E402
from amigo_agent.template import SLOT_BY_KEY  # noqa: E402
import amigo_agent.periods as now_periods  # noqa: E402

# 사람이 쓴 것처럼 문장 속에 섞은 새 예시(이번에 추가). 정답은 사람이 정했다.
EXTRA = [
    ("매년 4월 초에 시작해 6월 말까지 진행", [4, 5, 6]),
    ("2026년 9월부터 11월까지 3개월간", [9, 10, 11]),
    ("올해 3분기 중 완료 목표", [7, 8, 9]),
    ("1·2분기에 걸쳐 진행", [1, 2, 3, 4, 5, 6]),
    ("12월 중순 ~ 이듬해 2월 초 (연말연시 포함)", [12, 1, 2]),
    ("연 4회, 분기마다", [3, 6, 9, 12]),
    ("5월~7월 (장마 전 마무리)", [5, 6, 7]),
    ("상반기 중 착수, 하반기 종료", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]),
    ("공고 후 협의(일정 미정)", []),
    ("필요 시 수시로", list(range(1, 13))),
    ("매월 말일", list(range(1, 13))),
    ("매주 월요일", list(range(1, 13))),
]


def collect(cases):
    texts, gold = [], {}
    for i, (text, months) in enumerate(cases):
        name = f"단위업무{i + 1:02d}"
        texts.append((name, text))
        gold[name] = sorted(months)
    return texts, gold


def run(cases, per_doc=6):
    texts, gold = collect(cases)
    engine = make_engine(AgentConfig())
    spec = SLOT_BY_KEY["overview"]
    out = {}
    for start in range(0, len(texts), per_doc):
        rows = texts[start : start + per_doc]
        table = "| 단위업무명 | 계획기간 | 비고 |\n|---|---|---|\n" + "\n".join(f"| {n} | {t} | |" for n, t in rows)
        kb = FakeKB([{"file_name": "업무계획.xlsx", "source_type": "xlsx", "created_at": "2026-09-01", "text": table}])
        result = engine.analyze_slot(spec, PROFILE, KBAdapter(kb))
        for item in result.slot["items"]:
            fields = item.get("fields") or {}
            out[str(fields.get("name") or item.get("title"))] = str(fields.get("period", ""))
    return gold, texts, out


def score(plan, gold, got):
    exact = missed = wrong = invented = 0
    detail = []
    for name, months in gold.items():
        if name not in got:
            detail.append((name, "(Claude 가 행을 빠뜨림)", months, None))
            wrong += 1
            continue
        pred = plan(got[name])
        if pred == months:
            exact += 1
        else:
            (missed if months and not pred else invented if not months else wrong)  # noqa: B018
            if months and not pred:
                missed += 1
            elif not months:
                invented += 1
            else:
                wrong += 1
            detail.append((name, got[name], months, pred))
    return exact, missed, wrong, invented, detail


if __name__ == "__main__":
    cases = PERIOD_CASES + HOLDOUT_PERIOD_CASES + EXTRA
    # 중복 문장 제거
    seen, uniq = set(), []
    for t, m in cases:
        if t not in seen:
            seen.add(t)
            uniq.append((t, m))
    cache = ROOT / "eval/results/claude_periods.json"
    if os.environ.get("AMIGO_EVAL_CACHE") and cache.exists():  # Claude 를 다시 부르지 않고 저장된 출력으로 다시 채점
        saved = json.loads(cache.read_text(encoding="utf-8"))["claude_period_text"]
        texts, gold = collect(uniq)
        by_text = {t: n for n, t in texts}
        got = {by_text[t]: v for t, v in saved.items() if t in by_text}
    else:
        gold, texts, got = run(uniq)
    src = {n: t for n, t in texts}
    legacy_src = os.environ.get("AMIGO_EVAL_LEGACY_SRC", "/tmp/amigo_before/src")
    spec = importlib.util.spec_from_file_location("legacy_periods", f"{legacy_src}/amigo_agent/periods.py")
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    dropped = [src[k] for k in gold if k not in got]
    gold = {k: v for k, v in gold.items() if k in got}  # Claude 가 빠뜨린 행은 해석 코드의 책임이 아니므로 빼고 채점한다
    n = len(gold)
    print(f"문제 {n + len(dropped)}개 중 Claude 가 읽어 온 행 {n}개 (빠뜨린 행 {len(dropped)}개: {dropped[:6]}…)")
    for label, plan in (("고치기 전", legacy.plan_months), ("지금", now_periods.plan_months)):
        exact, missed, wrong, invented, detail = score(plan, gold, got)
        print(f"{label}: 정확 {exact}/{n} ({exact / n:.0%}) · 못 읽음 {missed} · 오답 {wrong} · 지어냄 {invented}")
        if label == "지금":
            for d in detail:
                print("   ", src[d[0]], "→ Claude:", d[1], "| 정답", d[2], "| 읽음", d[3])
    if not os.environ.get("AMIGO_EVAL_CACHE"):
        cache.write_text(json.dumps({"claude_period_text": {src[k]: v for k, v in got.items()}}, ensure_ascii=False, indent=1), encoding="utf-8")

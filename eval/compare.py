"""두 측정 결과(eval/results/<라벨>.json)를 나란히 비교한다. 사용: python eval/compare.py before after"""

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"


def load(label: str) -> dict:
    return json.loads((RESULTS / f"{label}.json").read_text(encoding="utf-8"))


def rows(d: dict) -> list[tuple[str, float, str]]:
    q = d["questions"]["평균"]
    return [
        ("기간 해석 정확도(개발용 59문제)", d["periods"]["accuracy"], "pct"),
        ("기간 해석 정확도(별도 25문제)", d["periods_holdout"]["accuracy"], "pct"),
        ("협의 상대 묶기 F1(개발용)", d["parties"]["f1"], "num"),
        ("협의 상대 묶기 F1(별도)", d["parties_holdout"]["f1"], "num"),
        ("잘못 합친 쌍(개발용+별도)", d["parties"]["wrong_merges"] + d["parties_holdout"]["wrong_merges"], "int"),
        ("질문 수(평균)", q["questions_asked"], "num1"),
        ("질문당 채우는 정보", q["info_per_question"], "num"),
        ("5번째 질문까지 채운 비율", q["fill_at_5"], "pct"),
        ("질문 순서 점수(AUC)", q["fill_auc_8"], "num"),
        ("질문한 정보 비율", q["covered_by_asked"], "pct"),
    ]


def fmt(value: float, kind: str) -> str:
    return {"pct": f"{value:.0%}", "num": f"{value:.2f}", "num1": f"{value:.1f}", "int": f"{int(value)}"}[kind]


if __name__ == "__main__":
    a, b = (load(x) for x in (sys.argv[1], sys.argv[2]))
    print(f"{'지표':<28}{sys.argv[1]:>10}{sys.argv[2]:>10}")
    for (name, before, kind), (_, after, _k) in zip(rows(a), rows(b)):
        print(f"{name:<28}{fmt(before, kind):>10}{fmt(after, kind):>10}")

"""계획기간 해석 회귀 시험: eval/datasets.py 의 정답 데이터를 그대로 쓴다(성능 측정과 같은 문제)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from datasets import HOLDOUT_PERIOD_CASES, PERIOD_CASES  # noqa: E402

from amigo_agent.periods import months_to_period, plan_months  # noqa: E402


@pytest.mark.parametrize("text,gold", PERIOD_CASES + HOLDOUT_PERIOD_CASES)
def test_plan_months(text, gold):
    assert plan_months(text) == sorted(gold)


def test_explicit_months_beat_quarterly_fallback():
    assert plan_months("분기별(1·4·7·10월) 점검") == [1, 4, 7, 10]
    assert plan_months("분기별") == [3, 6, 9, 12]


def test_roundtrip_with_months_to_period():
    for months in ([1], [3, 4, 5, 9], [1, 3, 5, 7, 9, 11], [11, 12, 1]):
        assert plan_months(months_to_period(months)) == sorted(months)

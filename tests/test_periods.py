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


# ------------------------------------------------------------------ 해를 넘기는 계획기간
from datetime import date  # noqa: E402

from amigo_agent.periods import plan_span, span_to_period  # noqa: E402
from amigo_agent.render import render_document  # noqa: E402


@pytest.mark.parametrize(
    "text,gold",
    [
        ("26년12월 ~ 27년1월", [(2026, 12), (2027, 1)]),
        ("2026.12~2027.01", [(2026, 12), (2027, 1)]),
        ("2026-12-01 ~ 2027-02-28", [(2026, 12), (2027, 1), (2027, 2)]),
        ("'26.12~'27.02", [(2026, 12), (2027, 1), (2027, 2)]),
        ("11월~2월", [(2026, 11), (2026, 12), (2027, 1), (2027, 2)]),
        ("2026.11~2월", [(2026, 11), (2026, 12), (2027, 1), (2027, 2)]),
        ("12월~2027.01", [(2026, 12), (2027, 1)]),
        ("연말연시", [(2026, 12), (2027, 1)]),
        ("2026.10~2027.03; 2027.05", [(2026, 10), (2026, 11), (2026, 12), (2027, 1), (2027, 2), (2027, 3), (2027, 5)]),
        ("3월~12월", [(2026, m) for m in range(3, 13)]),
    ],
)
def test_plan_span_crosses_year(text, gold):
    assert sorted(plan_span(text, 2026)[0]) == gold


def test_span_to_period_roundtrip():
    period = span_to_period([202611, 202612, 202701, 202703])
    assert period == "2026.11~2027.01; 2027.03"
    assert sorted(plan_span(period, 2026)[0]) == [(2026, 11), (2026, 12), (2027, 1), (2027, 3)]
    assert span_to_period([3, 4, 5]) == "3월~5월"


def _overview_doc(*periods: str) -> str:
    items = [{"id": f"u{i}", "title": f"업무{i}", "fields": {"name": f"업무{i}", "period": p, "note": "진행 중"}} for i, p in enumerate(periods)]
    return render_document(profile={"handover_date": "2026-10-26"}, slots={"overview": {"items": items}}, gaps=[], sources=[], version=1, today=date(2026, 10, 26))


def test_gantt_single_year_keeps_twelve_months():
    doc = _overview_doc("2026.03~2026.12", "매월 상시")
    assert '<th colspan="12">계획기간</th>' in doc and "년</th>" not in doc


def test_gantt_crossing_year_adds_year_header_and_continuous_bar():
    doc = _overview_doc("2026.03~2026.12", "26년12월 ~ 27년1월", "상시")
    assert '<th colspan="12">계획기간</th>' in doc  # 2026.03 ~ 2027.02 (12칸)
    assert '<tr><th colspan="10">2026년</th><th colspan="2">2027년</th></tr>' in doc
    row = next(line for line in doc.splitlines() if line.startswith("<tr><td>업무1</td>"))
    # 3월~11월 빈칸 9개 뒤 12월·1월이 이어진 막대 한 칸(colspan 2), 2월 빈칸
    assert row.count("<td></td>") == 10 and 'colspan="2">&nbsp;</td><td></td><td>' in row
    always = next(line for line in doc.splitlines() if line.startswith("<tr><td>업무2</td>"))
    assert 'colspan="12"' in always


def test_gantt_long_span_widens_table():
    doc = _overview_doc("2026.07~2027.12")
    assert '<th colspan="18">계획기간</th>' in doc
    assert '<tr><th colspan="6">2026년</th><th colspan="12">2027년</th></tr>' in doc


def test_tilde_pairs_do_not_become_strikethrough():
    import mistune

    items = [{"id": "o1", "title": "현안", "fields": {"name": "현안", "content": "리뉴얼(26년 12월~27년 1월) / 점검(11월~이듬해 2월)"}}]
    doc = render_document(
        profile={"form": "office", "handover_date": "2026-10-26"}, slots={"office_items": {"items": items}}, gaps=[], sources=[], version=1, today=date(2026, 10, 26)
    )
    assert "12월\\~27년" in doc
    assert "<del>" not in mistune.create_markdown(plugins=["strikethrough"])(doc)

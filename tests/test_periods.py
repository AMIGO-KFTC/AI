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


def test_gantt_crossing_year_uses_quarters():
    """계획기간이 올해(2026) 밖(2025·2027)에 걸치면 월별 대신 분기별로, 연도 행과 함께 그린다."""
    doc = _overview_doc("2026.03~2026.12", "26년12월 ~ 27년1월", "상시")
    assert '<th colspan="8">계획기간</th>' in doc  # 2026·2027 = 8분기
    assert '<tr><th colspan="4">2026년</th><th colspan="4">2027년</th></tr>' in doc
    assert "<th>1분기</th><th>2분기</th><th>3분기</th><th>4분기</th><th>1분기</th>" in doc and "월</th>" not in doc.split("<tr><td>")[0].split("계획기간")[1]
    row = next(line for line in doc.splitlines() if line.startswith("<tr><td>업무1</td>"))
    # 2026 4분기(12월)와 2027 1분기(1월)가 이어진 막대 한 칸(colspan 2), 나머지 빈칸
    assert row.count("<td></td>") == 6 and 'colspan="2">&nbsp;</td>' in row
    assert 'colspan="8"' in next(line for line in doc.splitlines() if line.startswith("<tr><td>업무2</td>"))


def test_gantt_three_years_and_dropped_years():
    """2025~2027 3년은 12분기. 2024년 이하·2028년 이상은 버린다."""
    doc = _overview_doc("2025.07~2027.12", "2024.01~2024.12", "2028.03~2028.09")
    assert '<th colspan="12">계획기간</th>' in doc and '<tr><th colspan="4">2025년</th><th colspan="4">2026년</th><th colspan="4">2027년</th></tr>' in doc
    row0 = next(line for line in doc.splitlines() if line.startswith("<tr><td>업무0</td>"))
    assert row0.count("<td></td>") == 2 and 'colspan="10"' in row0  # 2025 3분기부터 2027 4분기까지 10칸
    for name in ("업무1", "업무2"):  # 그리는 해 밖이라 막대가 없고 원문이 비고에 남는다
        row = next(line for line in doc.splitlines() if line.startswith(f"<tr><td>{name}</td>"))
        assert "bar" not in row and "계획기간: 202" in row


def test_gantt_within_this_year_stays_monthly():
    doc = _overview_doc("2026.03~2026.12", "상시")
    assert '<th colspan="12">계획기간</th>' in doc and "<th>1월</th>" in doc and "분기" not in doc


def test_tilde_pairs_do_not_become_strikethrough():
    import mistune

    items = [{"id": "o1", "title": "15. 기타 주요사항", "fields": {"name": "15. 기타 주요사항", "content": "리뉴얼(26년 12월~27년 1월) / 점검(11월~이듬해 2월)"}}]
    doc = render_document(
        profile={"form": "office", "handover_date": "2026-10-26"}, slots={"office_items": {"items": items}}, gaps=[], sources=[], version=1, today=date(2026, 10, 26)
    )
    assert "12월\\~27년" in doc
    assert "<del>" not in mistune.create_markdown(plugins=["strikethrough"])(doc)

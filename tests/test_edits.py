"""화면에서 고친 월별 막대가 상태에 확정되어, 문서를 다시 만들어도 유지되는지."""

import pytest

from amigo_agent import AgentStateError, HandoverAgent
from amigo_agent.edits import apply_gantt_edit
from amigo_agent.periods import months_to_period, plan_months
from amigo_agent.render import render_document
from amigo_agent.state import empty_slots, upsert_item

from conftest import PROFILE
from htmltables import html_tables, texts


def test_months_roundtrip():
    for months in ([1], [3, 4, 5], [1, 2, 3, 9], [1, 3, 5, 7, 9, 11], list(range(1, 13)), [11, 12]):
        assert plan_months(months_to_period(months)) == months
    assert months_to_period([]) == ""


def _slots():
    slots = empty_slots(["overview"])
    upsert_item(slots["overview"], item_id="", title="홈페이지 운영", fields={"unit": "홈페이지 운영", "period": "3월~5월"}, citations=[], origin="document")
    upsert_item(slots["overview"], item_id="", title="접근성 점검", fields={"unit": "접근성 점검", "period": "6월"}, citations=[], origin="document")
    return slots


def test_edit_replaces_period_and_beats_later_document_value():
    slots = _slots()
    assert apply_gantt_edit(slots, "홈페이지 운영", 0, [3, 4, 5, 6, 7]) == "overview"
    item = slots["overview"]["items"][0]
    assert item["fields"]["period"] == "3월~7월" and item["field_meta"]["period"]["rank"] == 1
    # 자료를 다시 분석해 예전 값이 들어와도 직접 고친 값이 이긴다
    upsert_item(slots["overview"], item_id=item["id"], title="홈페이지 운영", fields={"period": "3월~5월"}, citations=[], origin="document")
    assert item["fields"]["period"] == "3월~7월"
    assert apply_gantt_edit(slots, "없는 업무", 0, [1]) is None


def _bar_rows(markdown):
    """월별 일정표에서 {단위업무명: 막대가 칠해진 달 집합}."""
    table = next(t for t in html_tables(markdown) if len(t) > 1 and texts(t[1])[:1] == ["1월"])
    rows = {}
    for row in table[2:]:  # 머리글 2행(계획기간 / 1~12월) 다음부터
        month, months = 1, set()
        for cell in row[1:-1]:
            if cell["cls"] == "bar":
                months |= set(range(month, month + cell["colspan"]))
            month += cell["colspan"]
        rows[texts(row)[0]] = months
    return rows


def test_edit_gantt_survives_regeneration(kb):
    agent = HandoverAgent()
    agent.start("s1", PROFILE, kb)
    snap = agent.finish("s1", kb).snapshot
    name = snap["slots"]["overview"]["items"][0]["title"]
    version = snap["document_version"]
    assert _bar_rows(snap["document"])[name] != {2, 3}

    result = agent.edit_gantt("s1", name, 0, [2, 3])
    assert result["version"] == version and _bar_rows(result["markdown"])[name] == {2, 3}
    assert agent.snapshot("s1")["document"] == result["markdown"]

    # 자료를 더하거나 검토 의견으로 문서를 다시 만들어도 직접 고친 막대가 유지된다
    again = agent.send_message("s1", "외주 업체 계약은 매년 12월에 갱신합니다", kb).snapshot
    assert again["document_version"] == version + 1 and _bar_rows(again["document"])[name] == {2, 3}
    again = agent.notify_files("s1", kb, ["업무정의서.pdf"]).snapshot
    assert _bar_rows(again["document"])[name] == {2, 3}

    with pytest.raises(AgentStateError):
        agent.edit_gantt("s1", "없는 업무", 0, [1])


def test_edit_matches_the_name_shown_in_the_document():
    """문서 첫 열은 필드 값(name)이고 항목 제목과 다를 수 있다. 문서에 보이는 이름으로 찾아야 한다."""
    slots = empty_slots(["overview"])
    item = upsert_item(slots["overview"], item_id="", title="짧은 제목", fields={"name": "문서에 보이는 긴 업무 이름", "period": "3월"}, citations=[], origin="document")
    assert item["title"] != item["fields"]["name"]
    assert apply_gantt_edit(slots, "문서에 보이는 긴 업무 이름", 0, [4, 5]) == "overview"
    assert item["fields"]["period"] == "4월~5월"


@pytest.mark.parametrize("stored", ["[LGL-3] 준비서면 반박 논리 정리", "(AMG-12) 준비서면 반박 논리 정리", "준비서면 반박 논리 정리 (E3)", "준비서면 반박 논리 정리"])
def test_gantt_row_is_found_by_the_name_shown_in_the_document(stored):
    """문서에는 지라 키·근거 번호를 뗀 이름이 보인다. 화면은 그 이름으로 막대를 고치므로, 저장된 이름에 그런 꼬리표가 있어도 행을 찾아야 한다(못 찾으면 서버가 409)."""
    slots = empty_slots(["overview"])
    upsert_item(slots["overview"], item_id="", title=stored, fields={"name": stored, "period": "3월~5월"}, citations=[], origin="document")
    shown = "준비서면 반박 논리 정리"
    assert apply_gantt_edit(slots, shown, 0, [6, 7]) == "overview"
    assert plan_months(slots["overview"]["items"][0]["fields"]["period"]) == [6, 7]
    assert apply_gantt_edit(slots, "다른 업무", 0, [1]) is None  # 이름이 다르면 여전히 못 찾는다


def test_gantt_edit_keeps_working_after_render_strips_the_tags():
    """실제 흐름: 문서를 그린 뒤 그 문서의 첫 칸 이름으로 막대를 고친다."""
    import re

    slots = empty_slots(["overview"])
    upsert_item(slots["overview"], item_id="", title="[LGL-3] 소송 진행", fields={"name": "[LGL-3] 소송 진행", "period": "3월~5월"}, citations=[], origin="document")
    doc = render_document(profile={"name": "김민수", "handover_date": "2026-11-14"}, slots=slots, gaps=[], sources=[], version=1)
    shown = next(re.match(r"<tr><td>(.*?)</td>", r).group(1) for r in doc.splitlines() if r.startswith("<tr><td>") and "bar" in r)
    assert shown == "소송 진행" and "LGL-3" not in doc
    assert apply_gantt_edit(slots, shown, 0, [4]) == "overview"

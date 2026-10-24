"""단위업무 확인: 자료에서 찾은 단위업무 중 인계자가 입력하지 않은 것은 가장 먼저 예/아니요로 확인한다."""

import pytest
from conftest import PROFILE

from amigo_agent import HandoverAgent

ENTERED = {**PROFILE, "task_name": "홈페이지 콘텐츠 관리"}  # 인계자가 처음에 입력한 단위업무


@pytest.fixture(autouse=True)
def _confirm_units(monkeypatch):
    monkeypatch.setenv("AMIGO_CONFIRM_UNITS", "1")


def _units(snap):
    return {it["id"]: it["fields"].get("name") for it in snap["slots"]["overview"]["items"]}


def test_unit_check_comes_first_with_yes_no_choices(kb):
    result = HandoverAgent().start("u1", ENTERED, kb)
    snap = result.snapshot
    first = next(g for g in snap["gaps"] if g["id"] == snap["current_gap_id"])
    assert first.get("unit_check") and first["slot"] == "overview"
    assert "유지보수 계약 관리" in first["question"] and "단위업무가 맞나요" in first["question"]  # 입력하지 않은 업무만 묻는다
    question = result.messages[-1]
    assert question["meta"]["quick_replies"] == ["예, 맞아요", "아니요, 아니에요"]
    assert not any(g.get("unit_check") and "홈페이지 콘텐츠 관리" in g["question"] for g in snap["gaps"])  # 입력한 업무는 묻지 않는다


def test_yes_keeps_the_unit_and_no_removes_it_with_its_questions(kb):
    agent = HandoverAgent()
    snap = agent.start("u2", ENTERED, kb).snapshot
    first = next(g for g in snap["gaps"] if g["id"] == snap["current_gap_id"])
    unit_id = first["item_id"]

    result = agent.send_message("u2", "아니요, 아니에요", kb)
    assert unit_id not in _units(result.snapshot)  # 문서에서 뺀다
    assert not any(g.get("item_id") == unit_id and g["id"] != first["id"] for g in result.snapshot["gaps"])  # 그 업무에 딸린 질문도 뺀다
    assert "인계 대상이 아니라서 인수인계서에서 뺐어요" in result.messages[0]["content"]

    agent2 = HandoverAgent()
    snap = agent2.start("u3", ENTERED, kb).snapshot
    first = next(g for g in snap["gaps"] if g["id"] == snap["current_gap_id"])
    result = agent2.send_message("u3", "예, 맞아요", kb)
    item = next(it for it in result.snapshot["slots"]["overview"]["items"] if it["id"] == first["item_id"])
    assert item["confirmed"] and "단위업무로 확인했어요" in result.messages[0]["content"]


def test_unit_check_can_be_turned_off(kb, monkeypatch):
    monkeypatch.setenv("AMIGO_CONFIRM_UNITS", "0")
    snap = HandoverAgent().start("u4", ENTERED, kb).snapshot
    assert not any(g.get("unit_check") for g in snap["gaps"])

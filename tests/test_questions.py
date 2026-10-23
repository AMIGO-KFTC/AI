"""질문 순서 후처리: 엔진(Claude 포함)이 만든 순서와 상관없이 양식(template.py)의 장 순서로 배치한다."""

from amigo_agent.questions import keep_order, rank_by_slot_order
from amigo_agent.template import SLOT_KEYS


def _gap(gid, slot, status="open", rank=None, question=None):
    gap = {"id": gid, "slot": slot, "item_id": "", "status": status, "question": question or f"{gid} 질문"}
    if rank is not None:
        gap["rank"] = rank
    return gap


def test_questions_follow_the_template_slot_order_and_keep_engine_order_within_a_slot():
    # Claude 가 시스템·부서 질문을 먼저 정했고, 같은 장 안의 순서(r1, r2)는 엔진이 정한 대로다
    gaps = [_gap("sys", "systems"), _gap("dept", "dept_notes"), _gap("r1", "regular"), _gap("ov", "overview"), _gap("r2", "regular"), _gap("st", "stakeholders")]
    ordered = rank_by_slot_order(gaps)
    assert [g["id"] for g in ordered] == ["ov", "r1", "r2", "st", "sys", "dept"]
    assert [g["rank"] for g in ordered] == list(range(6))
    assert SLOT_KEYS.index("overview") < SLOT_KEYS.index("regular") < SLOT_KEYS.index("stakeholders") < SLOT_KEYS.index("systems") < SLOT_KEYS.index("dept_notes")


def test_no_weight_or_priority_changes_the_order():
    gaps = [{**_gap("sys", "systems"), "priority": 1, "weight": 9}, {**_gap("reg", "regular"), "priority": 3, "weight": 0}]
    assert [g["id"] for g in rank_by_slot_order(gaps)] == ["reg", "sys"]  # 정보량·우선순위와 무관하게 장 순서


def test_unknown_slot_goes_last():
    assert [g["id"] for g in rank_by_slot_order([_gap("x", "없는장"), _gap("ov", "overview")])] == ["ov", "x"]


def test_keep_order_inserts_new_questions_at_their_slot_position_and_can_pin_the_asked_one():
    previous = [_gap("a", "overview", "asked", 0), _gap("b", "regular", "open", 1), _gap("c", "systems", "open", 2)]
    new = [_gap("b2", "regular", question="b 질문"), _gap("c2", "systems", question="c 질문"), _gap("n1", "regular", question="새 정기업무 질문"), _gap("n2", "stakeholders", question="새 협의 질문")]
    for gap, old in zip(new[:2], previous[1:]):
        gap["question"] = old["question"]
    gaps = [*new]
    assert keep_order(previous, gaps) is False
    assert [g["id"] for g in sorted(gaps, key=lambda g: g["rank"])] == ["b2", "n1", "n2", "c2"]  # 기존 순서 유지 + 새 질문은 자기 장 자리에
    pinned = keep_order(previous, gaps, first=_gap("p", "systems", question=previous[2]["question"]))
    assert pinned is True and min(gaps, key=lambda g: g["rank"])["id"] == "c2"  # 방금 물은 질문이 맨 앞

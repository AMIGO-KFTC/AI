"""질문 순서 후처리: 엔진이 weight 를 안 줘도(Claude) 슬롯 상태로 세어 같은 기준으로 정렬한다."""

from amigo_agent.questions import gap_weight, rank_by_value
from amigo_agent.state import empty_slots, upsert_item


def _slots():
    slots = empty_slots(["regular", "systems"])
    upsert_item(slots["regular"], item_id="", title="월간 보고", fields={"name": "월간 보고"}, citations=[], origin="document")
    upsert_item(slots["systems"], item_id="", title="CMS", fields={"system": "CMS", "purpose": "게시"}, citations=[], origin="document")
    return slots


def test_weight_counts_missing_required_fields_and_ordering_follows_it():
    slots = _slots()
    regular, systems = slots["regular"]["items"][0], slots["systems"]["items"][0]
    gaps = [  # Claude 가 시스템 질문을 먼저 정했고 weight 는 주지 않았다
        {"id": "g1", "slot": "systems", "item_id": systems["id"], "priority": 2, "status": "open"},
        {"id": "g2", "slot": "regular", "item_id": regular["id"], "priority": 2, "status": "open"},
    ]
    assert gap_weight(gaps[1], slots) > gap_weight(gaps[0], slots)
    ordered = rank_by_value(gaps, slots)
    assert [g["id"] for g in ordered] == ["g2", "g1"] and [g["rank"] for g in ordered] == [0, 1]


def test_bonus_only_for_party_and_system_slots_and_zero_weight_goes_last():
    gaps = [
        {"id": "a", "slot": "dept_notes", "item_id": "", "priority": 1, "weight": 2, "status": "open"},  # Claude 가 priority 1 을 붙여도 가산점 없음
        {"id": "b", "slot": "systems", "item_id": "", "priority": 2, "weight": 1, "status": "open"},
        {"id": "c", "slot": "overview", "item_id": "", "priority": 1, "weight": 0, "status": "open"},  # 필수 항목이 이미 찬 항목
        {"id": "d", "slot": "dept_notes", "item_id": "", "priority": 2, "weight": 2, "status": "open"},
    ]
    assert [g["id"] for g in rank_by_value(gaps)] == ["b", "a", "d", "c"]  # 1+2.5 > 2 = 2 > 0, 동점(a,d)은 원래 순서

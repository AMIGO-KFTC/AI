"""질문 순서 후처리: 엔진(규칙/Claude)이 만든 질문을 '한 번에 채우는 정보량' 기준으로 다시 정렬한다.

Claude 는 질문 목록과 순서를 자기 판단으로 정하므로 실행할 때마다 순서가 달라진다. 질문 하나가 채워 줄 부족한 필수 항목 수(weight)를
슬롯 상태에서 직접 세어 같은 기준으로 정렬하면, 어느 엔진을 쓰든 같은 자료에서 같은 순서가 나온다.
점수가 같으면 엔진이 정한 순서를 그대로 둔다.
"""

from __future__ import annotations

from typing import Any

from .state import Gap, SlotState, find_item, missing_fields
from .template import SLOT_BY_KEY

PRIORITY_BONUS = 2.5  # 협의 상대·시스템 접근 권한은 자료에 거의 없어 꼭 물어야 하는 정보라 가산점을 준다
BONUS_SLOTS = ("stakeholders", "leave_contacts", "systems")  # Claude 는 질문마다 priority 1 을 붙이는 경향이 있어 priority 값이 아니라 슬롯으로 정한다


def gap_weight(gap: Gap, slots: dict[str, SlotState]) -> int:
    """이 질문이 채워 줄 부족한 필수 항목 수(이미 다 찬 항목을 묻는 질문은 0). 항목을 못 찾으면 1."""
    if "weight" in gap:
        return int(gap["weight"])
    spec = SLOT_BY_KEY.get(gap.get("slot", ""))
    if spec is None:
        return 1
    items = (slots.get(spec.key) or {}).get("items", [])
    item = find_item(items, item_id=gap.get("item_id", ""))
    if item is not None:
        return len(missing_fields(spec, item))
    return max(1, len(spec.required) * spec.min_items) if not items else 1


def gap_score(gap: Gap, weight: int) -> float:
    """채워 줄 정보가 없는 질문(0)은 가산점도 없이 맨 뒤로 간다."""
    return weight + (PRIORITY_BONUS if weight > 0 and gap.get("slot") in BONUS_SLOTS else 0)


def rank_by_value(gaps: list[Gap], slots: dict[str, SlotState] | None = None) -> list[Gap]:
    """질문에 weight 를 채우고 정보량 점수 순으로 rank(0부터)를 다시 매긴다(제자리 수정, 정렬된 목록 반환)."""
    slots = slots or {}
    for gap in gaps:
        gap["weight"] = gap_weight(gap, slots)
    order = sorted(range(len(gaps)), key=lambda i: (-gap_score(gaps[i], gaps[i]["weight"]), i))
    for rank, index in enumerate(order):
        gaps[index]["rank"] = rank
    return [gaps[i] for i in order]


__all__ = ["gap_score", "gap_weight", "rank_by_value"]

"""팀 필수 항목 보장: 관리자가 '필수'로 지정한 팀 항목이 자료에서 확인되지 않으면 질문이 반드시 나오게 한다.

프롬프트(prompts.team_guidelines_block)는 모델에게 '필수 항목이 자료에 없으면 질문으로 남기라'고 부탁할 뿐이라,
모델이 질문을 만들지 않거나 장마다 질문을 자르는 단계, 중복 제거·순서 정하기 단계에서 필수 항목 질문이 사라질 수 있다.
이 모듈은 분석과 질문 계획이 끝난 뒤에 규칙으로 확인해, 다뤄지지 않은 필수 항목의 질문을 직접 넣고 맨 앞에 둔다.

- 다뤄졌는지: 어느 장이든 항목의 제목이나 이름·주제 필드에 필수 항목 이름이 들어 있으면 다뤄진 것으로 본다.
- 이미 질문이 있는지: 같은 필수 항목의 질문(team_item)이 한 번이라도 있었으면(답변·건너뜀 포함) 다시 만들지 않는다.
- 같은 장의 비슷한 질문: 부서별 추가 자료 장에서 그 항목 이름을 언급하는 열린 질문은 팀 항목 질문이 대신한다(같은 걸 두 번 묻지 않게).
- 어느 장에 두는지: 부서별 추가 자료(dept_notes) 장. 답변은 그 장의 '추가 자료' 항목으로 기록된다.
"""

from __future__ import annotations

from typing import Any

from .prompts import clean_team_guidelines
from .state import Gap, SlotState, new_id, normalize_title, open_gaps

TEAM_ITEM_SLOT = "dept_notes"
NAME_FIELDS = ("topic", "name")  # 항목의 이름·주제를 담는 필드(부서별 추가 자료는 topic, 그 밖의 장은 name)


def required_team_items(profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """팀 지침 중 필수로 지정된 항목(이름·작성 안내)."""
    data = clean_team_guidelines((profile or {}).get("team_guidelines"))
    return [item for item in data["items"] if item["required"]]


def is_covered(name: str, slots: dict[str, SlotState], slot_keys: list[str]) -> bool:
    """필수 항목 이름이 어느 장의 항목 제목·이름·주제에 들어 있으면 이미 다뤄진 것으로 본다."""
    key = normalize_title(name)
    if not key:
        return True  # 이름으로 판단할 수 없으면 묻지 않는다
    for slot_key in slot_keys:
        for item in (slots.get(slot_key) or {}).get("items", []):
            fields = item.get("fields") or {}
            texts = [item.get("title", ""), *(str(fields.get(f, "")) for f in NAME_FIELDS)]
            if any(key in normalize_title(text) for text in texts):
                return True
    return False


def team_item_question(name: str, instruction: str) -> str:
    """인계자가 기억으로 바로 답할 수 있는 한 문장. 해당 내용이 없으면 '없음'으로 닫을 수 있게 한다."""
    hint = f" ({instruction})" if instruction else ""
    return f"우리 팀 필수 항목 '{name}'에 대해 알려 주세요.{hint} 해당하는 내용이 없으면 '없음'이라고 답해 주세요."


def ensure_team_item_gaps(
    profile: dict[str, Any] | None,
    slots: dict[str, SlotState],
    gaps: list[Gap],
    *,
    slot_keys: list[str],
    limit: int,
) -> list[Gap]:
    """다뤄지지 않은 필수 팀 항목의 질문이 열린 질문에 들어 있게 하고, 필수 항목 질문을 맨 앞으로 옮긴다.

    열린 질문(필수 항목 먼저, rank 다시 매김) 뒤에 나머지를 붙인 새 목록을 돌려준다. 필수 항목이 없거나 모두 다뤄졌으면 gaps 를 그대로 돌려준다.
    limit 는 한 번에 만들 수 있는 필수 항목 질문 수의 상한(질문 수 상한).
    """
    required = required_team_items(profile)
    if not required or TEAM_ITEM_SLOT not in slot_keys:
        return gaps
    asked_before = {g.get("team_item") for g in gaps if g.get("team_item")}
    created = 0
    for item in required:
        name = item["name"]
        if name in asked_before:  # 이미 질문한 적이 있으면(답변·건너뜀 포함) 다시 만들지 않는다
            continue
        if is_covered(name, slots, slot_keys):
            continue
        if created >= limit:
            break
        key = normalize_title(name)
        # 같은 장에서 이 항목 이름을 언급하는 열린 질문(모델이 만든 질문, 규칙 엔진의 일반 질문이 예시로 이름을 든 경우)은
        # 팀 항목 질문이 대신한다. 작성 안내와 '없음' 안내가 들어 있는 질문이 하나만 나가게 한다. 다른 장의 질문은 그대로 둔다.
        gaps = [
            g
            for g in gaps
            if not (g.get("status") == "open" and g.get("slot") == TEAM_ITEM_SLOT and not g.get("team_item") and key in normalize_title(f"{g.get('question', '')} {g.get('description', '')}"))
        ]
        gaps.append(
            {
                "id": new_id("gap"),
                "slot": TEAM_ITEM_SLOT,
                "item_id": "",
                "description": f"팀 필수 항목 '{name}' 내용이 자료에서 확인되지 않았습니다.",
                "question": team_item_question(name, item["instruction"]),
                "priority": 1,
                "ask_for_document": False,
                "status": "open",
                "team_item": name,
            }
        )
        created += 1
    opened = open_gaps(gaps)
    ordered = [g for g in opened if g.get("team_item")] + [g for g in opened if not g.get("team_item")]
    for rank, gap in enumerate(ordered):
        gap["rank"] = rank
    return ordered + [g for g in gaps if g.get("status") != "open"]


__all__ = ["ensure_team_item_gaps", "is_covered", "required_team_items", "team_item_question"]

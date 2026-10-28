"""사용자가 완성된 문서 화면에서 직접 고친 값을 에이전트 상태에 '인계자 확정값'으로 반영한다.

월별 일정표의 막대를 고치면 그 단위 업무의 계획기간(period)을 새 월 목록으로 바꾼다. 값은 인계자 답변과 같은 순위(1순위)로 들어가므로,
이후 검토 의견·자료 추가로 문서를 다시 만들어도 자료에서 읽은 옛 값에 덮어써지지 않는다(precedence.py).
"""

from __future__ import annotations

from .periods import span_to_period
from .precedence import user_meta
from .state import SlotState, apply_field, item_label
from .template import SLOT_BY_KEY

EDIT_LABEL = "문서에서 직접 수정"


def apply_gantt_edit(slots: dict[str, SlotState], name: str, nth: int, months: list[int]) -> str | None:
    """월별 일정표에서 이름이 `name` 인 nth(0부터) 번째 행의 계획기간을 `months` 로 바꾼다. 바꾼 슬롯 키를 반환하고, 행을 못 찾으면 None."""
    seen = 0
    for key, slot in slots.items():
        spec = SLOT_BY_KEY.get(key)
        if spec is None or spec.layout != "gantt":
            continue
        for item in slot.get("items", []):
            shown = str((item.get("fields") or {}).get(spec.title_field) or item_label(spec, item))  # 문서(render.py)의 첫 열과 같은 이름
            if shown.strip() != name.strip():
                continue
            if seen < nth:
                seen += 1
                continue
            period = span_to_period(months)
            item.get("fields", {}).pop("period", None)  # 직접 고친 값이 기존 값(같은 값이어도)을 확실히 대체한다
            (item.get("field_meta") or {}).pop("period", None)
            apply_field(item, "period", period or "해당 월 없음", user_meta(EDIT_LABEL))
            item["origin"] = "user" if item.get("origin") == "user" else "mixed"
            item["confirmed"] = True
            return key
    return None

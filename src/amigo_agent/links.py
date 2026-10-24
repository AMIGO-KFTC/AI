"""다른 장에 붙는 장(협업관계 → 프로젝트)이 붙는 장의 이름을 정확히 쓰도록, 붙는 장의 항목 이름을 분석 프롬프트에 준다.

두 장은 병렬로 분석하면 서로의 이름을 알 수 없어 연결 필드가 어긋나므로, 붙는 장을 먼저 분석한 뒤(nodes.analyze_children) 이 이름 목록을 준다.
이름이 어긋나면 문서의 카드(양식)에 들어가지 못하고 어느 항목인지 묻는 질문('확인 필요 사항')으로 남는다.
"""

from __future__ import annotations

from .state import SlotState, item_label
from .template import SLOT_BY_KEY, SlotSpec


def parent_names(slots: dict[str, SlotState], spec: SlotSpec) -> list[str]:
    """spec 이 붙는 장의 항목 이름(문서 카드의 제목에 보이는 이름)."""
    parent = SLOT_BY_KEY[spec.embed_into]
    names: list[str] = []
    for item in (slots.get(spec.embed_into) or {}).get("items", []):
        name = str((item.get("fields") or {}).get(parent.title_field) or item_label(parent, item)).strip()
        if name and name not in names:
            names.append(name)
    return names


def link_names_block(slots: dict[str, SlotState], spec: SlotSpec) -> str:
    """붙는 장의 항목 이름 목록(프롬프트 구간). spec 이 다른 장에 붙지 않으면 빈 문자열."""
    if not spec.embed_into or not spec.embed_link:
        return ""
    parent = SLOT_BY_KEY[spec.embed_into]
    names = parent_names(slots, spec)
    label = spec.label(spec.embed_link)
    head = f"## 연결할 이름 (이 문서의 '{parent.title}' 장에 이미 적힌 {parent.label(parent.title_field)})"
    if not names:
        return f"{head}\n(아직 없음) '{label}'({spec.embed_link}) 는 비워 두고 gaps 로 남깁니다."
    lines = "\n".join(f"- {name}" for name in names)
    return (
        f"{head}\n{lines}\n"
        f"인수인계서는 정해진 양식(카드)에 항목을 넣으므로 '{label}'({spec.embed_link}) 에는 위 목록 중 하나를 글자 그대로 씁니다. "
        "새 이름을 짓거나 줄이거나 바꿔 쓰지 않습니다. 맞는 것이 없으면 비워 두고 gaps 로 남깁니다."
    )

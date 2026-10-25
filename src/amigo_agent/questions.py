"""질문 순서 후처리: 엔진(규칙/Claude)이 만든 질문을 인수인계서 양식(template.py)의 순서대로 배치한다.

순서는 장 순서 → 장 안의 항목(세부업무) 순서 → 항목 안의 표 행 순서다. 한 세부업무에 대한 질문이 모두 끝나야 다음 세부업무로 넘어가고,
한 세부업무 안에서도 양식 표의 행 순서(업무 개요 → 수행 시기 → 수행 절차 → … )대로 묻는다. 카드 안에 들어가는 표(협업관계·추진 경과)의
질문은 그 표가 들어가는 행 자리에서 묻는다. 같은 칸을 묻는 질문끼리는 들어온 순서를 그대로 둔다.
어느 엔진을 쓰든 같은 자료에서 같은 순서가 나온다.
"""

from __future__ import annotations

from typing import Any

from .state import Gap, normalize_title
from .template import SLOT_BY_KEY, SLOT_KEYS

_SLOT_ORDER = {key: i for i, key in enumerate(SLOT_KEYS)}


def _slot_index(gap: Gap) -> int:
    spec = SLOT_BY_KEY.get(gap.get("slot", ""))
    key = spec.embed_into if spec is not None and spec.embed_into else gap.get("slot", "")
    return _SLOT_ORDER.get(key, len(_SLOT_ORDER))


def _parent_item(gap: Gap, slots: dict[str, Any]) -> tuple[str, str]:
    """질문이 속한 (장, 항목 id). 카드 안 표의 질문은 그 카드(받는 장의 항목)에 속한다."""
    spec = SLOT_BY_KEY.get(gap.get("slot", ""))
    if spec is None or not spec.embed_into:
        return gap.get("slot", ""), gap.get("item_id", "")
    if gap.get("parent_id"):
        return spec.embed_into, gap["parent_id"]
    child = next((it for it in (slots.get(spec.key) or {}).get("items", []) if it.get("id") == gap.get("item_id")), None)
    link = normalize_title(str(((child or {}).get("fields") or {}).get(spec.embed_link) or ""))
    parent = SLOT_BY_KEY[spec.embed_into]
    for item in (slots.get(spec.embed_into) or {}).get("items", []):
        name = normalize_title(str((item.get("fields") or {}).get(parent.title_field) or item.get("title") or ""))
        if link and name and (link == name or link in name or name in link):
            return spec.embed_into, item.get("id", "")
    return spec.embed_into, ""


def template_key(gap: Gap, slots: dict[str, Any]) -> tuple[int, int, float]:
    """(장 순서, 항목 순서, 표 행 순서). 항목이 정해지지 않은 질문(장 전체를 묻는 질문)은 그 장의 맨 뒤에 둔다."""
    slot_key, item_id = _parent_item(gap, slots)
    items = (slots.get(slot_key) or {}).get("items", [])
    item_index = next((i for i, it in enumerate(items) if it.get("id") == item_id), len(items))
    parent = SLOT_BY_KEY.get(slot_key)
    spec = SLOT_BY_KEY.get(gap.get("slot", ""))
    keys = [f.key for f in parent.fields] if parent else []
    if spec is not None and spec.embed_into and spec.embed_after in keys:
        row = keys.index(spec.embed_after) + 0.5  # 카드 안 표는 그 표가 들어가는 행 바로 다음
    elif gap.get("field") in keys:
        row = float(keys.index(gap["field"]))
    else:
        row = -1.0  # 항목 자체를 묻는 질문은 그 항목의 맨 앞
    return _slot_index(gap), item_index, row


def rank_by_template(gaps: list[Gap], slots: dict[str, Any] | None = None) -> list[Gap]:
    """질문의 rank(0부터)를 양식 순서(장 → 항목 → 표 행)대로 다시 매긴다(제자리 수정, 정렬된 목록 반환)."""
    slots = slots or {}
    ordered = sorted(gaps, key=lambda g: template_key(g, slots))  # 파이썬 정렬은 안정적이라 같은 칸 질문의 순서가 유지된다
    for rank, gap in enumerate(ordered):
        gap["rank"] = rank
    return ordered


def rank_by_slot_order(gaps: list[Gap]) -> list[Gap]:
    """항목 정보 없이 장 순서로만 rank 를 매긴다(이전 호출부 호환)."""
    return rank_by_template(gaps, {})


def keep_order(previous: list[Gap], gaps: list[Gap], first: Gap | None = None, slots: dict[str, Any] | None = None) -> bool:
    """재분석으로 질문 목록이 다시 만들어져도 이미 정한 질문 순서(rank)를 유지한다(제자리 수정).

    같은 장·같은 문장의 질문은 이전 순서를 그대로 이어받고, 자료 때문에 새로 생긴 질문만 양식 순서에 맞는 자리에 끼워 넣는다.
    first 가 주어지고 그 질문이 아직 남아 있으면 맨 앞에 둔다(방금 물은 질문을 다시 묻기 위함). 맨 앞에 둔 경우 True.
    """
    slots = slots or {}
    ident = lambda g: (g.get("slot"), g.get("question"))  # noqa: E731
    old_rank = {ident(g): g["rank"] for g in previous if g.get("status") in ("open", "asked") and g.get("rank") is not None}
    ordered = sorted((g for g in gaps if ident(g) in old_rank and g.get("status") == "open"), key=lambda g: old_rank[ident(g)])
    for gap in (g for g in gaps if ident(g) not in old_rank and g.get("status") == "open"):
        key = template_key(gap, slots)
        index = next((i for i, other in enumerate(ordered) if template_key(other, slots) > key), len(ordered))
        ordered.insert(index, gap)
    moved = False
    if first is not None:
        match = next((g for g in ordered if ident(g) == ident(first)), None)
        if match is not None:
            ordered.remove(match)
            ordered.insert(0, match)
            moved = True
    for rank, gap in enumerate(ordered):
        gap["rank"] = rank
    return moved


__all__ = ["keep_order", "rank_by_slot_order", "rank_by_template", "template_key"]

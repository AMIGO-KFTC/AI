"""질문 순서 후처리: 엔진(규칙/Claude)이 만든 질문을 인수인계서 양식(template.py)의 장 순서대로 배치한다.

Claude 는 질문 목록과 순서를 자기 판단으로 정하므로 실행할 때마다 순서가 달라진다. 질문을 장 순서(SLOTS 의 순서)로 다시 정하면
어느 엔진을 쓰든 같은 자료에서 같은 순서가 나온다. 같은 장 안에서는 엔진이 만든 순서를 그대로 둔다.
"""

from __future__ import annotations

from .state import Gap
from .template import SLOT_KEYS

_SLOT_ORDER = {key: i for i, key in enumerate(SLOT_KEYS)}


def _slot_index(gap: Gap) -> int:
    return _SLOT_ORDER.get(gap.get("slot", ""), len(_SLOT_ORDER))


def rank_by_slot_order(gaps: list[Gap]) -> list[Gap]:
    """질문의 rank(0부터)를 양식의 장 순서대로 다시 매긴다. 같은 장 안에서는 들어온 순서를 따른다(제자리 수정, 정렬된 목록 반환)."""
    ordered = sorted(gaps, key=_slot_index)  # 파이썬 정렬은 안정적이라 같은 장 안의 순서가 유지된다
    for rank, gap in enumerate(ordered):
        gap["rank"] = rank
    return ordered


def keep_order(previous: list[Gap], gaps: list[Gap], first: Gap | None = None) -> bool:
    """재분석으로 질문 목록이 다시 만들어져도 이미 정한 질문 순서(rank)를 유지한다(제자리 수정).

    같은 장·같은 문장의 질문은 이전 순서를 그대로 이어받고, 자료 때문에 새로 생긴 질문만 장 순서에 맞는 자리(같은 장의 마지막)에 끼워 넣는다.
    first 가 주어지고 그 질문이 아직 남아 있으면 맨 앞에 둔다(방금 물은 질문을 다시 묻기 위함). 맨 앞에 둔 경우 True.
    """
    ident = lambda g: (g.get("slot"), g.get("question"))  # noqa: E731
    old_rank = {ident(g): g["rank"] for g in previous if g.get("status") in ("open", "asked") and g.get("rank") is not None}
    ordered = sorted((g for g in gaps if ident(g) in old_rank and g.get("status") == "open"), key=lambda g: old_rank[ident(g)])
    for gap in (g for g in gaps if ident(g) not in old_rank and g.get("status") == "open"):
        index = next((i for i, other in enumerate(ordered) if _slot_index(other) > _slot_index(gap)), len(ordered))
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


__all__ = ["keep_order", "rank_by_slot_order"]

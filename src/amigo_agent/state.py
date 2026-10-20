"""LangGraph 상태 정의와 슬롯/공백 조작 도우미.

체크포인터(SQLite 등)에 그대로 저장되므로 상태에는 JSON 으로 표현 가능한 dict/list/str 만 넣는다.
"""

from __future__ import annotations

import operator
import re
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from .precedence import RANK_REFERENCE, RANK_USER, beats, classify, now_ts, reason, user_meta
from .template import SLOT_BY_KEY, SLOT_KEYS, SlotSpec


class Citation(TypedDict, total=False):
    source_id: str
    label: str  # 사람이 읽는 출처 표기 (예: "업무정의서.pdf p.3")
    chunk_id: str
    quote: str  # 근거 문장 일부
    rank: int  # 정보 충돌 우선순위(1 인계자 답변 · 2 공식 문서 · 3 회의록·메일 · 4 날짜 없는 자료)
    ts: str  # 자료 작성(발신) 시각, 분 단위 ISO. 날짜가 없으면 생략
    doc_class: str  # user | official | minutes | mail | reference


class SlotItem(TypedDict, total=False):
    id: str
    title: str
    fields: dict[str, str]
    citations: list[Citation]
    origin: str  # "document" | "user"
    confirmed: bool
    field_meta: dict[str, dict[str, Any]]  # 필드별로 현재 값의 출처 순위·시각·출처 표기
    conflicts: list[dict[str, Any]]  # 자동으로 처리한 정보 충돌 내역(상태에만 남고 문서에는 싣지 않음)
    candidates: dict[str, list[dict[str, Any]]]  # 아직 확정하지 않은 필드별 후보 값(resolve_conflicts 노드가 확정하고 지움)


class SlotState(TypedDict, total=False):
    key: str
    items: list[SlotItem]
    coverage: str  # "sufficient" | "partial" | "missing"
    summary: str
    note: str


class Gap(TypedDict, total=False):
    id: str
    slot: str
    item_id: str
    description: str
    question: str
    priority: int  # 1(높음) ~ 3(낮음)
    rank: int  # 질문 계획(Claude)이 정한 전체 순서. 없으면 priority·장 순서로 정렬
    ask_for_document: bool
    status: str  # open | asked | answered | resolved | skipped


class Turn(TypedDict, total=False):
    role: str  # assistant | user
    kind: str  # summary | question | confirm | info | answer | document
    content: str
    at: str


def merge_slots(left: dict | None, right: dict | None) -> dict:
    """병렬 분석 노드들이 각자 반환한 슬롯을 합친다(같은 키는 새 값으로 교체)."""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class AgentState(TypedDict, total=False):
    profile: dict[str, Any]
    stage: str
    sources: list[dict[str, Any]]
    slots: Annotated[dict[str, SlotState], merge_slots]
    gaps: list[Gap]
    current_gap_id: str
    pending: dict[str, Any]  # 교차 확인 대기 중인 내용 {"gap_id", "refs": [[slot, item_id], ...]}
    ack: str  # 다음 질문 앞에 붙일 짧은 응답
    event: dict[str, Any]  # interrupt 로 받은 사용자 입력
    transcript: Annotated[list[Turn], operator.add]
    question_count: int
    finish_requested: bool
    doc_dirty: bool
    document: str
    document_version: int
    last_intent: str
    next_action: str  # choose_next 가 정한 다음 경로(ask | compose)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def turn(role: str, kind: str, content: str) -> Turn:
    return {"role": role, "kind": kind, "content": content, "at": now_iso()}


# --------------------------------------------------------------------------- 슬롯 평가
def evaluate_coverage(spec: SlotSpec, items: list[SlotItem]) -> str:
    """항목 수와 필수 필드 충족 여부로 충족 수준을 판정한다(LLM 판정의 하한선 역할)."""
    if not items:
        return "missing"
    complete = [it for it in items if not missing_fields(spec, it)]
    if len(complete) >= spec.min_items and len(complete) == len(items):
        return "sufficient"
    return "partial"


def missing_fields(spec: SlotSpec, item: SlotItem) -> list[str]:
    fields = item.get("fields") or {}
    return [key for key in spec.required if not str(fields.get(key, "")).strip()]


def combine_coverage(rule: str, judged: str | None) -> str:
    """규칙 기반 판정과 LLM 판정 중 더 보수적인(낮은) 쪽을 택한다."""
    order = {"missing": 0, "partial": 1, "sufficient": 2}
    if judged not in order:
        return rule
    return rule if order[rule] <= order[judged] else judged


def item_label(spec: SlotSpec, item: SlotItem) -> str:
    fields = item.get("fields") or {}
    return str(item.get("title") or fields.get(spec.title_field) or "항목")


def normalize_title(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", (text or "").lower())


def find_item(items: list[SlotItem], *, item_id: str = "", title: str = "", fuzzy: bool = True) -> SlotItem | None:
    if item_id:
        for item in items:
            if item.get("id") == item_id:
                return item
    key = normalize_title(title)
    if key:
        for item in items:
            other = normalize_title(item.get("title", ""))
            if other and (other == key or (fuzzy and min(len(other), len(key)) >= 4 and (other in key or key in other))):
                return item
    return None


def citation_meta(citations: list[Citation], origin: str) -> dict[str, Any]:
    """이번에 들어온 값의 출처 순위. 근거 중 가장 강한 것(순위, 같으면 최신)을 쓴다."""
    best: dict[str, Any] | None = None
    for c in citations:
        if c.get("rank") is None:
            continue
        meta = {"rank": int(c["rank"]), "ts": c.get("ts"), "doc_class": c.get("doc_class", ""), "label": c.get("label", "")}
        if best is None or beats(meta, best):
            best = meta
    if best is not None:
        return best
    label = citations[0].get("label", "") if citations else ""
    if origin == "user":
        return user_meta(label)
    return {"rank": RANK_REFERENCE, "ts": None, "doc_class": "reference", "label": label}


def _field_default_meta(item: SlotItem) -> dict[str, Any]:
    """출처 정보가 없는 값(이전 버전 상태 등)은 항목의 출처로 순위를 추정한다."""
    if item.get("origin") == "user":
        return {"rank": RANK_USER, "ts": None, "doc_class": "user", "label": "인계자 답변"}
    return {"rank": RANK_REFERENCE, "ts": None, "doc_class": "reference", "label": ""}


def apply_field(item: SlotItem, key: str, value: str, meta: dict[str, Any], *, defer: bool = False) -> None:
    """필드 값을 넣는다. 기존 값과 다르면 우선순위로 이기는 쪽을 쓰고, 충돌 내역을 항목에 남긴다.

    defer=True 이면 판정하지 않고 후보로만 모아 둔다. 그래프의 resolve_conflicts 노드가 `resolve_slots` 로 확정한다.
    """
    fields = item.setdefault("fields", {})
    metas = item.setdefault("field_meta", {})
    old = str(fields.get(key, "")).strip()
    old_meta = metas.get(key) or _field_default_meta(item)
    if defer:
        candidates = item.setdefault("candidates", {}).setdefault(key, [])
        if old and not candidates:  # 이미 확정된 값도 후보로 함께 겨룬다
            candidates.append({"value": old, **old_meta})
        candidates.append({"value": value, **meta})
        if not old:
            fields[key], metas[key] = value, meta  # 확정 전까지 임시로 첫 값을 보여 준다
        return
    if not old:
        fields[key], metas[key] = value, meta
        return
    if old == value:
        if beats(meta, old_meta):
            metas[key] = meta
        return
    if beats(meta, old_meta):
        winner, loser, applied, dropped = meta, old_meta, value, old
        fields[key], metas[key] = value, meta
    else:
        winner, loser, applied, dropped = old_meta, meta, old, value
    record = {
        "field": key,
        "applied": applied,
        "applied_label": winner.get("label", ""),
        "dropped": dropped,
        "dropped_label": loser.get("label", ""),
        "reason": reason(winner, loser),
    }
    conflicts = item.setdefault("conflicts", [])
    sig = (record["field"], record["applied"], record["dropped"])
    if not any((c["field"], c["applied"], c["dropped"]) == sig for c in conflicts):
        conflicts.append(record)


def merge_item_fields(target: SlotItem, source: SlotItem, *, defer: bool = False) -> None:
    """source 항목의 값을 target 에 합친다(재분석 때 기존 항목을 새 분석 결과에 합칠 때). 필드별 우선순위를 따른다."""
    metas = source.get("field_meta") or {}
    for key, value in (source.get("fields") or {}).items():
        if str(value).strip():
            apply_field(target, key, str(value).strip(), metas.get(key) or _field_default_meta(source), defer=defer)
    known = {(c["field"], c["applied"], c["dropped"]) for c in target.get("conflicts", [])}
    for c in source.get("conflicts", []):
        if (c["field"], c["applied"], c["dropped"]) not in known:
            target.setdefault("conflicts", []).append(c)


def upsert_item(
    slot_state: SlotState,
    *,
    item_id: str,
    title: str,
    fields: dict[str, str],
    citations: list[Citation],
    origin: str,
    confirmed: bool = False,
    defer: bool = False,
) -> SlotItem:
    """항목을 추가하거나(새 항목) 기존 항목의 빈/변경 필드를 갱신한다. 갱신된 항목을 반환.

    기존 값과 다른 값이 들어오면 정보 충돌 우선순위(precedence.py)에 따라 이기는 값을 쓴다.
    defer=True 이면 충돌을 바로 판정하지 않고 후보로 모아 두며, 그래프의 resolve_conflicts 노드가 확정한다.
    """
    spec = SLOT_BY_KEY[slot_state["key"]]
    items = slot_state.setdefault("items", [])
    clean = {k: str(v).strip() for k, v in fields.items() if spec.field(k) and str(v).strip()}
    if title and spec.title_field not in clean:
        clean[spec.title_field] = title.strip()
    # 협의 상대는 제목이 부분적으로 겹쳐도('재무팀' ⊂ '재무팀 박지훈') 다른 사람일 수 있어 정확히 같을 때만 같은 항목으로 본다(같은 상대는 parties.py 가 합친다)
    existing = find_item(items, item_id=item_id, title=title or clean.get(spec.title_field, ""), fuzzy=spec.key not in ("stakeholders", "leave_contacts"))
    if existing is None:
        existing = {
            "id": new_id(spec.key),
            "title": title.strip() or clean.get(spec.title_field, "항목"),
            "fields": {},
            "field_meta": {},
            "citations": [],
            "origin": origin,
            "confirmed": confirmed,
        }
        items.append(existing)
    elif title.strip() and existing.get("origin") == "user":
        existing["title"] = title.strip()
    elif title.strip() and len(existing.get("title", "")) > 25 and len(title.strip()) < len(existing["title"]):
        existing["title"] = title.strip()  # 문장형 제목보다 짧은 항목명을 우선한다
    meta = citation_meta(citations, origin)
    for key, value in clean.items():
        apply_field(existing, key, value, meta, defer=defer)
    known = {(c.get("source_id"), c.get("chunk_id"), c.get("label")) for c in existing.get("citations", [])}
    for citation in citations:
        sig = (citation.get("source_id"), citation.get("chunk_id"), citation.get("label"))
        if sig not in known:
            existing.setdefault("citations", []).append(citation)
            known.add(sig)
    if origin == "user":
        existing["origin"] = "user" if existing.get("origin") == "user" else "mixed"
        existing["confirmed"] = confirmed
    slot_state["coverage"] = evaluate_coverage(spec, items)
    return existing


def resolve_item(item: SlotItem) -> bool:
    """항목에 모인 후보 값을 우선순위로 확정하고 후보를 지운다. 확정할 후보가 있었으면 True."""
    candidates = item.pop("candidates", None)
    if not candidates:
        return False
    fields = item.setdefault("fields", {})
    metas = item.setdefault("field_meta", {})
    for key, entries in candidates.items():
        fields.pop(key, None)  # 임시 값을 지우고 후보를 들어온 순서대로 다시 겨룬다
        metas.pop(key, None)
        for entry in entries:
            meta = {k: v for k, v in entry.items() if k != "value"}
            apply_field(item, key, str(entry["value"]), meta)
    return True


def resolve_slots(slots: dict[str, SlotState]) -> list[str]:
    """모든 슬롯의 후보 값을 확정한다. 확정이 일어난 슬롯 키를 반환."""
    changed: list[str] = []
    for key, slot in slots.items():
        touched = [resolve_item(item) for item in slot.get("items", [])]
        if any(touched):
            changed.append(key)
    return changed


def empty_slots(keys: tuple[str, ...] | list[str] | None = None) -> dict[str, SlotState]:
    return {key: {"key": key, "items": [], "coverage": "missing", "summary": "", "note": ""} for key in (keys or SLOT_KEYS)}


def open_gaps(gaps: list[Gap]) -> list[Gap]:
    order = {key: i for i, key in enumerate(SLOT_KEYS)}
    candidates = [g for g in gaps if g.get("status") == "open"]
    return sorted(candidates, key=lambda g: (g.get("rank", 10_000), g.get("priority", 2), order.get(g.get("slot", ""), 99)))

"""LangGraph 상태 정의와 슬롯/공백 조작 도우미.

체크포인터(SQLite 등)에 그대로 저장되므로 상태에는 JSON 으로 표현 가능한 dict/list/str 만 넣는다.
"""

from __future__ import annotations

import operator
import re
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from .template import SLOT_BY_KEY, SLOT_KEYS, SlotSpec


class Citation(TypedDict, total=False):
    source_id: str
    label: str  # 사람이 읽는 출처 표기 (예: "업무정의서.pdf p.3")
    chunk_id: str
    quote: str  # 근거 문장 일부


class SlotItem(TypedDict, total=False):
    id: str
    title: str
    fields: dict[str, str]
    citations: list[Citation]
    origin: str  # "document" | "user"
    confirmed: bool


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


def find_item(items: list[SlotItem], *, item_id: str = "", title: str = "") -> SlotItem | None:
    if item_id:
        for item in items:
            if item.get("id") == item_id:
                return item
    key = normalize_title(title)
    if key:
        for item in items:
            other = normalize_title(item.get("title", ""))
            if other and (other == key or (min(len(other), len(key)) >= 4 and (other in key or key in other))):
                return item
    return None


def upsert_item(
    slot_state: SlotState,
    *,
    item_id: str,
    title: str,
    fields: dict[str, str],
    citations: list[Citation],
    origin: str,
    confirmed: bool = False,
) -> SlotItem:
    """항목을 추가하거나(새 항목) 기존 항목의 빈/변경 필드를 갱신한다. 갱신된 항목을 반환."""
    spec = SLOT_BY_KEY[slot_state["key"]]
    items = slot_state.setdefault("items", [])
    clean = {k: str(v).strip() for k, v in fields.items() if spec.field(k) and str(v).strip()}
    if title and spec.title_field not in clean:
        clean[spec.title_field] = title.strip()
    existing = find_item(items, item_id=item_id, title=title or clean.get(spec.title_field, ""))
    if existing is None:
        existing = {
            "id": new_id(spec.key),
            "title": title.strip() or clean.get(spec.title_field, "항목"),
            "fields": {},
            "citations": [],
            "origin": origin,
            "confirmed": confirmed,
        }
        items.append(existing)
    elif title.strip() and existing.get("origin") == "user":
        existing["title"] = title.strip()
    elif title.strip() and len(existing.get("title", "")) > 25 and len(title.strip()) < len(existing["title"]):
        existing["title"] = title.strip()  # 문장형 제목보다 짧은 항목명을 우선한다
    existing.setdefault("fields", {}).update(clean)
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


def empty_slots() -> dict[str, SlotState]:
    return {key: {"key": key, "items": [], "coverage": "missing", "summary": "", "note": ""} for key in SLOT_KEYS}


def open_gaps(gaps: list[Gap]) -> list[Gap]:
    order = {key: i for i, key in enumerate(SLOT_KEYS)}
    candidates = [g for g in gaps if g.get("status") == "open"]
    return sorted(candidates, key=lambda g: (g.get("rank", 10_000), g.get("priority", 2), order.get(g.get("slot", ""), 99)))

"""분석·해석·작성 '엔진'.

LangGraph 노드(nodes.py)는 흐름만 담당하고, 실제 판단은 엔진에 맡긴다.
  - ClaudeEngine  : Claude(Anthropic SDK) + search_documents 도구 호출 + 구조화 출력
  - OfflineEngine : API 키 없이 동작하는 규칙 기반 엔진(데모·테스트·사내망 개발용)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import AgentConfig
from ..kb import Evidence, KBAdapter
from ..state import Gap, SlotState, Turn
from ..template import SlotSpec


@dataclass
class SlotResult:
    slot: SlotState
    gaps: list[Gap]


@dataclass
class InterpretInput:
    profile: dict[str, Any]
    message: str
    stage: str
    current_gap: Gap | None
    pending: dict[str, Any] | None
    slots: dict[str, SlotState]
    open_gaps: list[Gap]
    transcript: list[Turn]


@dataclass
class Interpretation:
    intent: str  # answer | confirm | correct | skip | finish | question | other
    updates: list[dict[str, Any]] = field(default_factory=list)  # {"slot","item_id","title","fields","citations"}
    resolved_gap_ids: list[str] = field(default_factory=list)
    reply: str = ""


class Engine(Protocol):
    name: str

    def analyze_slot(self, spec: SlotSpec, profile: dict[str, Any], kb: KBAdapter) -> SlotResult: ...

    def plan_questions(self, profile: dict[str, Any], gaps: list[Gap]) -> tuple[list[str], set[str]]: ...

    def summarize(self, profile: dict[str, Any], slots: dict[str, SlotState], gaps: list[Gap], sources: list[dict]) -> str: ...

    def interpret(self, data: InterpretInput, kb: KBAdapter) -> Interpretation: ...

    def compose_extras(self, profile: dict[str, Any], slots: dict[str, SlotState]) -> dict[str, Any]: ...


def retrieve_for_slot(spec: SlotSpec, profile: dict[str, Any], kb: KBAdapter, top_k: int = 6, limit: int = 12) -> list[Evidence]:
    """슬롯의 기본 검색어들로 근거를 모은다(중복 제거, 점수순)."""
    queries = list(spec.queries)
    duties = str(profile.get("duties") or "").strip()
    if duties and spec.key in ("duties", "projects"):
        queries.insert(0, duties)
    seen: dict[str, Evidence] = {}
    for query in queries:
        for ev in kb.search(query, top_k=top_k):
            key = ev.chunk_id or ev.text[:80]
            if key not in seen or ev.score > seen[key].score:
                seen[key] = ev
    ranked = sorted(seen.values(), key=lambda e: e.score, reverse=True)
    return ranked[:limit]


def make_engine(config: AgentConfig, client: Any | None = None) -> Engine:
    if client is not None or config.resolved_mode() == "claude":
        from .claude import ClaudeEngine

        return ClaudeEngine(config, client=client)
    from .offline import OfflineEngine

    return OfflineEngine(config)


__all__ = ["Engine", "Interpretation", "InterpretInput", "SlotResult", "make_engine", "retrieve_for_slot"]

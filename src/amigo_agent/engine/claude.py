"""Claude 엔진: Agentic RAG(검색 도구 자율 호출) + 구조화 출력.

STAGE 1 슬롯 분석 : 기본 검색 결과를 근거로 주고, 모자라면 모델이 search_documents 를 스스로 호출해 추가 탐색
STAGE 2 요약      : 장별 분석 결과를 2~3문장 안내문으로
STAGE 3 답변 해석 : 의도 분류 + 항목 갱신 + 교차 확인 메시지(필요하면 자료 재검색)
STAGE 4 문서 합성 : 업무 개요와 핵심 당부사항(표·근거 번호는 render.py 가 결정적으로 생성)
"""

from __future__ import annotations

import logging
from functools import cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from ..config import AgentConfig
from ..kb import EvidenceRegistry, KBAdapter
from ..llm import SEARCH_TOOL, ClaudeLLM, LLMOutputError
from ..prompts import (
    ANALYSIS_SYSTEM,
    COMPOSE_SYSTEM,
    INTERPRET_SYSTEM,
    PLAN_SYSTEM,
    SUMMARY_SYSTEM,
    analysis_user,
    compose_user,
    interpret_user,
    plan_user,
    summary_user,
)
from ..state import Gap, SlotState, combine_coverage, evaluate_coverage, new_id, upsert_item
from ..template import ALL_FIELD_KEYS, SLOT_BY_KEY, SLOT_KEYS, SlotSpec
from . import Interpretation, InterpretInput, SlotResult, retrieve_for_slot

logger = logging.getLogger(__name__)

_STRICT = ConfigDict(extra="forbid")
_PRIORITY = {"high": 1, "medium": 2, "low": 3}


class GapProposal(BaseModel):
    model_config = _STRICT
    description: str = Field(description="부족한 정보가 무엇인지 한 줄")
    question: str = Field(description="인계자에게 할 질문 한 문장(한 가지 주제만)")
    priority: Literal["high", "medium", "low"]
    related_item_title: str = Field(description="관련 항목 제목. 장 전체에 대한 질문이면 빈 문자열")
    ask_for_document: bool = Field(description="파일로 받는 편이 나은 정보면 true")


@cache
def slot_output_model(slot_key: str) -> type[BaseModel]:
    """슬롯마다 필드가 다르므로 출력 스키마를 동적으로 만든다(모든 필드 필수, 모르면 빈 문자열)."""
    spec = SLOT_BY_KEY[slot_key]
    field_defs: dict[str, Any] = {f.key: (str, Field(description=f"{f.label}{'. ' + f.hint if f.hint else ''}")) for f in spec.fields}
    fields_model = create_model(f"{slot_key.title()}Fields", __config__=_STRICT, **field_defs)
    item_model = create_model(
        f"{slot_key.title()}Item",
        __config__=_STRICT,
        title=(str, Field(description="항목을 대표하는 짧은 제목")),
        fields=(fields_model, ...),
        evidence_ids=(list[str], Field(description="근거 ID 목록. 예: ['E1', 'E4']")),
    )
    return create_model(
        f"{slot_key.title()}Analysis",
        __config__=_STRICT,
        items=(list[item_model], ...),
        coverage=(Literal["sufficient", "partial", "missing"], ...),
        summary=(str, ...),
        gaps=(list[GapProposal], ...),
    )


class FieldValue(BaseModel):
    model_config = _STRICT
    key: Literal[ALL_FIELD_KEYS]  # type: ignore[valid-type]
    value: str


class ItemUpdate(BaseModel):
    model_config = _STRICT
    slot: Literal[SLOT_KEYS]  # type: ignore[valid-type]
    item_id: str
    title: str
    fields: list[FieldValue]
    evidence_ids: list[str]


class InterpretOutput(BaseModel):
    model_config = _STRICT
    intent: Literal["answer", "confirm", "correct", "skip", "finish", "question", "other"]
    updates: list[ItemUpdate]
    resolved_gap_ids: list[str]
    reply: str


class DuplicatePair(BaseModel):
    model_config = _STRICT
    drop_id: str
    keep_id: str


class PlanOutput(BaseModel):
    model_config = _STRICT
    duplicates: list[DuplicatePair]
    order: list[str]


class SummaryOutput(BaseModel):
    model_config = _STRICT
    message: str


class ComposeOutput(BaseModel):
    model_config = _STRICT
    overview: str
    key_points: list[str]


class ClaudeEngine:
    name = "claude"

    def __init__(self, config: AgentConfig, client: Any | None = None) -> None:
        self.config = config
        self.llm = ClaudeLLM(config, client=client)

    # ------------------------------------------------------------------ 공통: 검색 도구
    def _search_handler(self, kb: KBAdapter, registry: EvidenceRegistry):
        def handler(name: str, args: dict[str, Any]) -> str:
            if name != "search_documents":
                raise ValueError(f"알 수 없는 도구: {name}")
            query = str(args.get("query", "")).strip()
            results = kb.search(query, top_k=self.config.search_top_k)
            if not results:
                return f"'{query}' 에 대한 검색 결과가 없습니다."
            return registry.format(results, max_chars=900)

        return handler

    # ------------------------------------------------------------------ STAGE 1
    def analyze_slot(self, spec: SlotSpec, profile: dict[str, Any], kb: KBAdapter) -> SlotResult:
        registry = EvidenceRegistry()
        evidences = retrieve_for_slot(spec, profile, kb, top_k=self.config.search_top_k)
        output = self.llm.structured(
            system=ANALYSIS_SYSTEM,
            user=analysis_user(spec, profile, registry.format(evidences), self.config.max_gaps_per_slot + 1),
            output_model=slot_output_model(spec.key),
            effort=self.config.effort_analysis,
            tools=[SEARCH_TOOL],
            tool_handler=self._search_handler(kb, registry),
            max_tool_rounds=self.config.max_tool_rounds,
        )
        slot: SlotState = {"key": spec.key, "items": [], "coverage": "missing", "summary": output.summary, "note": ""}
        for item in output.items:
            fields = item.fields.model_dump()
            citations = registry.citations(item.evidence_ids, hint=item.title)
            if not citations:  # 근거 없는 항목은 기록하지 않는다(환각 방지)
                logger.info("drop item without evidence: %s", item.title)
                continue
            upsert_item(slot, item_id="", title=item.title, fields=fields, citations=citations, origin="document")
        slot["coverage"] = combine_coverage(evaluate_coverage(spec, slot["items"]), output.coverage)
        gaps: list[Gap] = []
        for proposal in output.gaps[: self.config.max_gaps_per_slot]:
            related = next((it for it in slot["items"] if proposal.related_item_title and it.get("title") == proposal.related_item_title), None)
            gaps.append(
                {
                    "id": new_id("gap"),
                    "slot": spec.key,
                    "item_id": related["id"] if related else "",
                    "description": proposal.description,
                    "question": proposal.question,
                    "priority": _PRIORITY.get(proposal.priority, 2),
                    "ask_for_document": proposal.ask_for_document,
                    "status": "open",
                }
            )
        if slot["coverage"] != "sufficient" and not gaps:
            gaps.append(
                {
                    "id": new_id("gap"),
                    "slot": spec.key,
                    "item_id": "",
                    "description": f"{spec.title} 정보 보완 필요",
                    "question": spec.default_question,
                    "priority": 2,
                    "ask_for_document": False,
                    "status": "open",
                }
            )
        return SlotResult(slot=slot, gaps=gaps)

    def plan_questions(self, profile: dict[str, Any], gaps: list[Gap]) -> tuple[list[str], set[str]]:
        """장별 질문 후보의 중복을 없애고 전체 우선순위를 정한다 → (물을 순서, 뺄 질문 id)."""
        if len(gaps) < 2:
            return [g["id"] for g in gaps], set()
        output = self.llm.structured(
            system=PLAN_SYSTEM,
            user=plan_user(profile, gaps),
            output_model=PlanOutput,
            effort=self.config.effort_chat,
            max_tokens=4000,
        )
        known = {g["id"] for g in gaps}
        drops = {d.drop_id for d in output.duplicates if d.drop_id in known and d.keep_id in known and d.drop_id != d.keep_id}
        order = [gid for gid in dict.fromkeys(output.order) if gid in known and gid not in drops]
        return order, drops

    # ------------------------------------------------------------------ STAGE 2
    def summarize(self, profile, slots, gaps, sources) -> str:
        output = self.llm.structured(
            system=SUMMARY_SYSTEM,
            user=summary_user(profile, slots, sources),
            output_model=SummaryOutput,
            effort=self.config.effort_chat,
            max_tokens=4000,
        )
        return output.message.strip()

    # ------------------------------------------------------------------ STAGE 3
    def interpret(self, data: InterpretInput, kb: KBAdapter) -> Interpretation:
        registry = EvidenceRegistry()
        output = self.llm.structured(
            system=INTERPRET_SYSTEM,
            user=interpret_user(
                profile=data.profile,
                stage=data.stage,
                current_gap=data.current_gap,
                pending_text=_pending_text(data),
                slots=data.slots,
                open_gaps=data.open_gaps,
                transcript=data.transcript,
                message=data.message,
            ),
            output_model=InterpretOutput,
            effort=self.config.effort_chat,
            max_tokens=8000,
            tools=[SEARCH_TOOL],
            tool_handler=self._search_handler(kb, registry),
            max_tool_rounds=2,
        )
        updates = []
        for up in output.updates:
            spec = SLOT_BY_KEY[up.slot]
            fields = {fv.key: fv.value for fv in up.fields if spec.field(fv.key)}
            if not fields and not up.title:
                continue
            updates.append(
                {
                    "slot": up.slot,
                    "item_id": up.item_id,
                    "title": up.title,
                    "fields": fields,
                    "citations": registry.citations(up.evidence_ids, hint=up.title),
                }
            )
        return Interpretation(intent=output.intent, updates=updates, resolved_gap_ids=list(output.resolved_gap_ids), reply=output.reply.strip())

    # ------------------------------------------------------------------ STAGE 4
    def compose_extras(self, profile, slots) -> dict[str, Any]:
        output = self.llm.structured(
            system=COMPOSE_SYSTEM,
            user=compose_user(profile, slots),
            output_model=ComposeOutput,
            effort=self.config.effort_compose,
            max_tokens=8000,
        )
        return {"overview": output.overview.strip(), "key_points": [p.strip() for p in output.key_points if p.strip()][:6]}


def _pending_text(data: InterpretInput) -> str:
    if not data.pending:
        return ""
    lines = []
    for slot_key, item_id in data.pending.get("refs", []):
        spec = SLOT_BY_KEY.get(slot_key)
        item = next((it for it in (data.slots.get(slot_key) or {}).get("items", []) if it.get("id") == item_id), None)
        if spec and item:
            fields = ", ".join(f"{spec.label(k)}={v}" for k, v in (item.get("fields") or {}).items() if v)
            lines.append(f"- [{spec.title}] id={item_id} {item.get('title')}: {fields}")
    return "\n".join(lines)


__all__ = ["ClaudeEngine", "InterpretOutput", "slot_output_model", "LLMOutputError"]

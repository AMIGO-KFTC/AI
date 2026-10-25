"""Claude 엔진: Agentic RAG(검색 도구 자율 호출) + 구조화 출력.

STAGE 1 슬롯 분석 : 기본 검색 결과를 근거로 주고, 모자라면 모델이 search_documents 를 스스로 호출해 추가 탐색
STAGE 2 요약      : 장별 분석 결과를 2~3문장 안내문으로
STAGE 3 답변 해석 : 의도 분류 + 항목 갱신 + 교차 확인 메시지(필요하면 자료 재검색)
STAGE 4 문서 합성 : 모델을 쓰지 않는다(표·근거 번호를 포함한 문서 전체를 render.py 가 결정적으로 생성)
"""

from __future__ import annotations

import logging
from functools import cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from ..config import AgentConfig
from ..kb import Evidence, EvidenceRegistry, KBAdapter
from ..llm import SEARCH_TOOL, ClaudeLLM, LLMOutputError
from ..prompts import (
    ANALYSIS_SYSTEM,
    INTERPRET_SYSTEM,
    PLAN_SYSTEM,
    SUMMARY_SYSTEM,
    AUTO_FILL_SYSTEM,
    analysis_user,
    interpret_user,
    plan_user,
    summary_user,
    auto_fill_user,
)
from ..state import Gap, SlotState, combine_coverage, evaluate_coverage, missing_fields, new_id, upsert_item
from ..template import ALL_FIELD_KEYS, SLOT_BY_KEY, SLOT_KEYS, SlotSpec, slots_for
from ..textkit import scrub_credentials
from . import Interpretation, InterpretInput, SlotResult, retrieve_for_slot

logger = logging.getLogger(__name__)

_STRICT = ConfigDict(extra="forbid")
_PRIORITY = {"high": 1, "medium": 2, "low": 3}


class GapProposal(BaseModel):
    model_config = _STRICT
    description: str = Field(description="부족한 정보가 무엇인지 한 줄")
    question: str = Field(description="인계자에게 할 질문 한 문장(한 가지 주제만)")
    priority: Literal["high", "medium", "low"]
    related_item_title: str = Field(description="묻는 칸이 있는 항목의 제목. 장에 항목이 하나도 없을 때만 빈 문자열")
    field: str = Field(description="묻는 필드 키(이 장의 필드 중 하나). 장에 항목이 하나도 없을 때만 빈 문자열")
    element: str = Field(description="칸에 값은 있지만 빠진 세부사항만 묻는 질문이면 그 세부사항 이름(예: '판단 근거'). 칸 전체를 묻는 질문이면 빈 문자열")
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


class SummaryOutput(BaseModel):
    model_config = _STRICT
    message: str


class AutoValue(BaseModel):
    model_config = _STRICT
    target_id: str
    value: str


class AutoFillOutput(BaseModel):
    model_config = _STRICT
    values: list[AutoValue]


def _no_relevant_message(query: str, results: list[Evidence]) -> str:
    """검색은 됐지만 질문과 관련 있어 보이는 조각이 없을 때 모델에게 알리는 도구 결과.

    조각 본문은 싣지 않고(근거로 잘못 쓰지 않도록) 가장 가까운 자료 이름만 알려 준다.
    표현이 달라 놓쳤을 수 있으니 다른 표현으로 한 번 더 찾아보게 안내한다.
    """
    nearest = ", ".join(dict.fromkeys(r.citation for r in results[:3]))
    return (
        f"'{query}' 와 관련 있다고 판단되는 검색 결과가 없습니다. 자료에 없는 내용일 가능성이 큽니다.\n"
        f"(가장 가까운 자료: {nearest} — 질문과 직접 관련이 낮아 근거로 쓸 수 없습니다.)\n"
        "다른 표현(동의어·약어·구체적인 이름)으로 딱 한 번만 더 찾아보세요. 그래도 없으면 더 찾지 말고, 자료에 없는 것으로 보고 "
        "빈 값으로 두거나 인계자에게 물어볼 질문(gaps)에 넣으세요. 자료에 없는 내용을 일반 상식으로 채우지 마세요."
    )


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
            if self.config.hide_irrelevant:
                # relevant 가 None 이면 지식베이스가 판정하지 못한 것이므로 그대로 보여 준다.
                relevant = [r for r in results if r.relevant is not False]
                if not relevant:
                    return _no_relevant_message(query, results)
                results = relevant
            return registry.format(results, max_chars=900)

        return handler

    # ------------------------------------------------------------------ STAGE 1
    def analyze_slot(self, spec: SlotSpec, profile: dict[str, Any], kb: KBAdapter) -> SlotResult:
        registry = EvidenceRegistry()
        evidences = retrieve_for_slot(spec, profile, kb, top_k=self.config.search_top_k)
        output = self.llm.structured(
            system=ANALYSIS_SYSTEM,
            user=analysis_user(spec, profile, registry.format(evidences)),
            output_model=slot_output_model(spec.key),
            effort=self.config.effort_analysis,
            tools=[SEARCH_TOOL],
            tool_handler=self._search_handler(kb, registry),
            max_tool_rounds=self.config.max_tool_rounds,
        )
        slot: SlotState = {"key": spec.key, "items": [], "coverage": "missing", "summary": output.summary, "note": ""}
        for item in output.items:
            fields = {k: scrub_credentials(str(v))[0] for k, v in item.fields.model_dump().items()}  # 자료에 비밀번호가 있어도 옮기지 않는다
            citations = registry.citations(item.evidence_ids, hint=item.title)
            if not citations:  # 근거 없는 항목은 기록하지 않는다(환각 방지)
                logger.info("drop item without evidence: %s", item.title)
                continue
            upsert_item(slot, item_id="", title=item.title, fields=fields, citations=citations, origin="document", defer=True)
        slot["coverage"] = combine_coverage(evaluate_coverage(spec, slot["items"]), output.coverage)
        gaps: list[Gap] = []
        for proposal in output.gaps:
            related = next((it for it in slot["items"] if proposal.related_item_title and it.get("title") == proposal.related_item_title), None)
            # 양식의 칸을 묻는 질문만 남긴다(어느 칸·세부사항을 물을지는 nodes.allowed_gap 이 인계자 정보에 맞춘 뒤 다시 거른다).
            # 장 전체를 묻는 질문은 코드가 만든다(nodes._empty_slot_gaps).
            if related is None or spec.field(proposal.field) is None:
                logger.info("drop question outside template cells: %s", proposal.question)
                continue
            gaps.append(
                {
                    "id": new_id("gap"),
                    "slot": spec.key,
                    "item_id": related["id"],
                    "field": proposal.field,
                    "element": proposal.element.strip(),
                    "description": proposal.description,
                    "question": proposal.question,
                    "priority": _PRIORITY.get(proposal.priority, 2),
                    "ask_for_document": proposal.ask_for_document,
                    "status": "open",
                }
            )
        return SlotResult(slot=slot, gaps=gaps)

    def plan_questions(self, profile: dict[str, Any], gaps: list[Gap]) -> tuple[list[str], set[str]]:
        """장별 질문 후보의 중복을 찾는다 → (남은 질문 id, 뺄 질문 id). 묻는 순서는 모델이 아니라 코드가 양식 순서로 정한다(questions.rank_by_template)."""
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
        return [g["id"] for g in gaps if g["id"] not in drops], drops

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

    def fill_auto(self, profile, slots, targets) -> dict[tuple[str, str, str], str]:
        """미질의 칸(개요·계획기간·진행상황 요약 등)을 지금까지 수집된 항목(자료 분석 + 인계자 답변)만으로 쓴다(문서를 만들 때)."""
        if not targets:
            return {}
        output = self.llm.structured(
            system=AUTO_FILL_SYSTEM,
            user=auto_fill_user(profile, slots, targets),
            output_model=AutoFillOutput,
            effort=self.config.effort_chat,
            max_tokens=8000,
        )
        by_id = {f"t{i}": t for i, t in enumerate(targets)}
        out: dict[tuple[str, str, str], str] = {}
        for v in output.values:
            t = by_id.get(v.target_id)
            if t is not None and v.value.strip():
                out[(t["slot"], t["item_id"], t["field"])] = v.value.strip()
        return out

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
        usable = {s.key for s in slots_for(data.profile)}
        dropped = 0
            fields = {fv.key: scrub_credentials(fv.value)[0] for fv in up.fields if spec.field(fv.key)}
            if up.slot not in usable:  # 이 인수인계서에 없는 장은 문서에 나오지 않으므로 반영하지 않는다
                dropped += 1
                continue
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
        reply = output.reply.strip()
        if dropped and not updates:  # 모델이 '넣었다'고 답해도 실제로 반영된 것이 없으면 그대로 알린다
            reply = "말씀하신 내용을 넣을 항목을 찾지 못해 반영하지 못했어요. 어느 항목(예: 주요 협업관계)에 넣을지 알려 주시겠어요?"
        return Interpretation(intent="other" if dropped and not updates else output.intent, updates=updates, resolved_gap_ids=list(output.resolved_gap_ids), reply=reply)


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

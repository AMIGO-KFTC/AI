"""LangGraph 노드 함수.

    STAGE 1 분석   prepare → analyze_slot(슬롯별 병렬, Send) → resolve_conflicts → collect_gaps
    STAGE 2 요약   summarize
    STAGE 3 Q&A    choose_next → ask → wait_user(interrupt) → interpret / skip_gap / reanalyze → (resolve_conflicts) → …
    STAGE 4 생성   compose → wait_user(검토 의견 대기) → interpret → compose …

노드는 상태를 직접 고치지 않고 바뀐 부분만 dict 로 돌려준다. 화면에 보여줄 메시지·진행률은
get_stream_writer() 로 이벤트를 흘려보내고(백엔드가 받아서 DB 에 저장), 대화 기록은 transcript 에도 남긴다.
"""

from __future__ import annotations

import copy
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from langgraph.types import Send, interrupt

from .config import AgentConfig
from .engine import Engine, Interpretation, InterpretInput, SlotResult
from .engine.offline import OfflineEngine
from .kb import KBAdapter
from .parties import consolidate_parties, remap_ids
from .field_guides import AUX, element_askable, first_ask_example, gate_question, match_element
from .questions import keep_order, rank_by_template
from .relevance import bigrams, is_none_answer
from .llm import LLMError, LLMOutputError
from .precedence import RANK_USER, now_ts
from .render import render_document, same_name, scope_of
from .state import (
    AgentState,
    Gap,
    Turn,
    combine_coverage,
    empty_slots,
    evaluate_coverage,
    find_item,
    item_label,
    merge_item_fields,
    missing_fields,
    new_id,
    normalize_title,
    open_gaps,
    resolve_slots,
    turn,
    upsert_item,
)
from .dept_info import clean_dept_info, has_info, targets as dept_targets
from .template import FieldSpec
from .template import (
    ASK_AUX,
    ASK_MAIN,
    COVERAGE_LABELS,
    PERIOD_CHOICES,
    SLOT_BY_KEY,
    STAGE_LABEL,
    STAGE_NUMBER,
    OFFICE_QUESTION,
    OFFICE_SLOT,
    has_seeds,
    is_office,
    profile_units,
    seed_items,
    slots_for,
)
from .textkit import CREDENTIAL_MASK, scrub_credentials

logger = logging.getLogger(__name__)

QUICK_REPLIES_QNA = ["모르겠어요 (건너뛰기)", "지금 문서 생성"]
QUICK_REPLIES_CONFIRM = ["네, 맞아요", "수정할게요"]
QUICK_REPLIES_YESNO = ["예", "아니요"]  # 예/아니요로 먼저 묻는 질문의 두 가지 선택지
AUX_NOTE = "꼭 답하지 않아도 돼요. 알고 계시면 적어 주세요."  # 보조질문 안내(화면은 질문 왼편에 '보조질문' 태그를 붙인다)
MAX_CONFIRM_ROUNDS = 2  # 정리한 내용과 한 번 고친 내용까지 확인을 묻고, 그다음 고침은 바로 반영
# 정리한 내용이 맞는지 물었을 때 이렇게만 답하면 LLM 에 묻지 않고 바로 확정한다(빠른 답변 단추 포함).
PLAIN_CONFIRMS = frozenset({"네", "네.", "네!", "예", "예.", "응", "넵", "넹", "맞아요", "맞아요.", "맞습니다", "맞습니다.", "네, 맞아요", "네 맞아요", "네, 맞아요.", "네 맞습니다", "네, 맞습니다", "그렇습니다", "확인", "확인했어요", "ok", "okay", "yes"})


def is_plain_confirm(text: str) -> bool:
    """'네, 맞아요' 처럼 확인만 하는 짧은 답인지. 새 정보가 섞인 답('네, 그리고 내선은 3311')은 아니다."""
    return " ".join(text.split()).strip().lower() in PLAIN_CONFIRMS


@dataclass
class AgentContext:
    kb: KBAdapter
    engine: Engine
    config: AgentConfig


# --------------------------------------------------------------------------- 이벤트 도우미
def _emit(event: dict[str, Any]) -> None:
    get_stream_writer()(event)


def _stage(stage: str) -> None:
    _emit({"type": "stage", "stage": stage, "number": STAGE_NUMBER.get(stage, 0), "label": STAGE_LABEL.get(stage, stage)})


def _progress(message: str, **extra: Any) -> None:
    _emit({"type": "progress", "message": message, **extra})


def _say(kind: str, content: str, **meta: Any) -> Turn:
    _emit({"type": "message", "role": "assistant", "kind": kind, "content": content, "meta": meta})
    return turn("assistant", kind, content)


def _offline(ctx: AgentContext) -> OfflineEngine:
    return OfflineEngine(ctx.config)


def _analyze(ctx: AgentContext, slot_key: str, profile: dict[str, Any]) -> SlotResult:
    spec = SLOT_BY_KEY[slot_key]
    try:
        return ctx.engine.analyze_slot(spec, profile, ctx.kb)
    except LLMOutputError as exc:  # 형식 오류만 규칙 기반으로 대체(인증·네트워크 오류는 그대로 알린다)
        logger.warning("slot %s analysis fell back to offline: %s", slot_key, exc)
        result = _offline(ctx).analyze_slot(spec, profile, ctx.kb)
        result.slot["note"] = "AI 분석 결과를 해석하지 못해 간이 분석 결과를 사용했습니다."
        return result


def _slot_with_gaps(result: SlotResult) -> dict[str, Any]:
    slot = dict(result.slot)
    slot["proposed_gaps"] = result.gaps
    return slot


# =========================================================================== STAGE 1
def prepare(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    _stage("analyzing")
    sources = runtime.context.kb.list_sources()
    active = [s.key for s in slots_for(state.get("profile"))]
    _progress(f"자료 {len(sources)}건을 분석하고 있어요.", current=0, total=len(active))
    return {
        "stage": "analyzing",
        "sources": sources,
        "slots": empty_slots(active),
        "gaps": [],
        "question_count": 0,
        "document": "",
        "document_version": 0,
        "pending": {},
        "ack": "",
        "current_gap_id": "",
        "finish_requested": False,
        "doc_dirty": False,
    }


def fan_out(state: AgentState) -> list[Send]:
    """슬롯별 분석을 병렬로 실행한다(LangGraph Send 를 이용한 map-reduce)."""
    # 다른 장에 붙는 장(협업관계)은 붙는 장의 이름이 나온 뒤에 분석한다(analyze_children)
    return [Send("analyze_slot", {"slot_key": key, "profile": state.get("profile", {})}) for key in (s.key for s in slots_for(state.get("profile"))) if not SLOT_BY_KEY[key].embed_into]


def analyze_slot(payload: dict[str, Any], runtime: Runtime[AgentContext]) -> dict[str, Any]:
    key = payload["slot_key"]
    spec = SLOT_BY_KEY[key]
    _progress(f"'{spec.title}' 분석 중", slot=key, status="running")
    result = _analyze(runtime.context, key, payload.get("profile", {}))
    settle_items(spec, payload.get("profile", {}), result.slot)
    slot = _slot_with_gaps(result)
    _emit({"type": "slot", "key": key, "slot": result.slot})
    _progress(f"'{spec.title}' 분석 완료 · {COVERAGE_LABELS.get(slot['coverage'], '')}", slot=key, status="done")
    return {"slots": {key: slot}}


def analyze_children(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """다른 장에 붙는 장(주요 협업관계)을 분석한다. 붙는 장(프로젝트)의 항목 이름을 프롬프트에 주어 연결 필드를 그 이름과 글자 그대로 쓰게 한다."""
    profile = state.get("profile", {})
    children = [s.key for s in slots_for(profile) if SLOT_BY_KEY[s.key].embed_into]
    if not children:
        return {}
    slots = state.get("slots") or {}
    out: dict[str, Any] = {}
    for key in children:
        spec = SLOT_BY_KEY[key]
        _progress(f"'{spec.title}' 분석 중", slot=key, status="running")
        result = _analyze(runtime.context, key, {**profile, "_link_slots": slots})
        fix_party_names(spec, result.slot.get("items") or [], slots)
        settle_items(spec, profile, result.slot)
        slot = _slot_with_gaps(result)
        _emit({"type": "slot", "key": key, "slot": result.slot})
        _progress(f"'{spec.title}' 분석 완료 · {COVERAGE_LABELS.get(slot['coverage'], '')}", slot=key, status="done")
        out[key] = slot
    return {"slots": out}


ORG_RE = re.compile(r"(?:\(주\)|㈜|주식회사)\s*\S+|\S+(?:팀|부|과|실|국|처|센터|본부|기관|공사|공단|위원회|회사)$")
PARTY_NAME_SLOTS = ("partners", "stakeholders", "contacts")  # 협업 대상 표: 이름 칸에는 사람·팀·기관 이름만 둔다


def fix_party_names(spec: Any, items: list[dict], slots: dict[str, Any]) -> None:
    """협업관계 표의 '이름' 칸에는 사람 이름 또는 팀·기관명만 둔다.

    모델이 업무명·시스템명(다른 장의 항목 이름)을 이름 칸에 쓰면(예: '도메인·SSL 인증서 관리 (총무팀)'),
    괄호 속 팀·기관명이 있으면 그것으로 바꾸고, 없으면 소속 이름으로 둔다(이 표는 묻지 않으므로 비워 두지 않는다).
    """
    if spec.key not in PARTY_NAME_SLOTS:
        return
    titles = {normalize_title(str(it.get("title") or "")) for key, slot in slots.items() if key != spec.key for it in (slot or {}).get("items", [])}
    titles.discard("")
    for item in items:
        fields = item.setdefault("fields", {})
        name = str(fields.get("name") or "").strip()
        bare = normalize_title(re.sub(r"\(.*?\)", "", name))
        if not bare or not any(len(t) >= 4 and (t in bare or bare in t) for t in titles):
            continue
        inner = [m.strip() for m in re.findall(r"\(([^)]*)\)", name) if ORG_RE.search(m.strip())]
        fields["name"] = inner[0] if inner else str(fields.get("org") or "").strip()
        if inner and not str(fields.get("org") or "").strip():
            fields["org"] = inner[0]
        item["title"] = fields["name"] or item.get("title", "")


def settle_items(spec: Any, profile: dict[str, Any], slot: dict[str, Any]) -> None:
    """분석한 장의 항목을 인계자 정보에 맞춘다(제자리 수정).

    - 인계자가 고른 단위업무·입력한 세부업무가 있으면 그 이름의 항목만 쓴다: 자료에서 찾은 같은 업무는 입력한 이름으로 맞추고,
      자료에 없는 것은 빈 항목으로 미리 만든다(빈 칸은 질문으로 채운다). 입력하지 않은 업무는 문서에 넣지 않는다.
      (그 범주의 세부업무를 하나도 입력하지 않았으면 자료에서 찾은 항목을 그대로 쓴다.)
    - 질의응답으로만 채우는 칸(장기휴가 참고자료)은 자료에서 읽은 값을 지운다.
    - 협업 대상 표의 내부/외부 구분을 정한다(우리 기관 소속이면 내부).
    """
    items = slot.setdefault("items", [])
    seeds = seed_items(spec, profile)
    if spec.seed == "office" or (spec.seed and has_seeds(profile) and (seeds or spec.seed == "units")):
        settled: list[dict[str, Any]] = []
        for seed in seeds:
            match = next((it for it in items if it not in settled and normalize_title(item_label(spec, it)) == normalize_title(seed["name"])), None)
            match = match or next((it for it in items if it not in settled and same_name(item_label(spec, it), seed["name"])), None)
            if match is None:
                match = {"id": new_id(spec.key), "title": seed["name"], "fields": {}, "field_meta": {}, "citations": [], "origin": "user", "confirmed": True}
            fields = match.setdefault("fields", {})
            for key, value in seed.items():
                fields[key] = value
                (match.setdefault("field_meta", {}))[key] = {"rank": RANK_USER, "ts": None, "doc_class": "user", "label": "인계자 입력"}
                (match.get("candidates") or {}).pop(key, None)
            match["title"] = seed["name"]
            settled.append(match)
        dropped = [item_label(spec, it) for it in items if it not in settled]
        if dropped:
            logger.info("drop %s items not entered by the giver: %s", spec.key, dropped)
        items[:] = settled
    for f in spec.fields:
        if not f.answers_only:
            continue
        for item in items:
            meta = (item.get("field_meta") or {}).get(f.key) or {}
            if meta.get("rank") != RANK_USER:
                (item.get("fields") or {}).pop(f.key, None)
                (item.get("field_meta") or {}).pop(f.key, None)
                (item.get("candidates") or {}).pop(f.key, None)
    if spec.split_field:
        for item in items:
            fields = item.setdefault("fields", {})
            fields[spec.split_field] = scope_of(fields, profile)
    if spec.key in SAME_TEAM_EXCLUDED and own_team(profile):
        kept = [it for it in items if not same_team(it.get("fields") or {}, profile)]
        if len(kept) != len(items):
            logger.info("drop %s rows of the giver's own team: %d", spec.key, len(items) - len(kept))
        items[:] = kept
    if spec.key == "dept_notes":
        _seed_dept_targets(spec, profile, items)
    slot["coverage"] = evaluate_coverage(spec, items)


SAME_TEAM_EXCLUDED = ("partners", "stakeholders", "contacts")  # 협업관계·주요 연락망 표에는 인계자와 같은 팀 사람을 넣지 않는다(노션 10.25)


def own_team(profile: dict[str, Any]) -> str:
    """인계자의 팀 이름(소속 '디지털전략부 · 웹서비스팀' 의 마지막 부분)."""
    parts = [p for p in re.split(r"[\s·/>]+", str((profile or {}).get("organization") or "")) if p]
    return parts[-1] if parts else ""


def same_team(fields: dict[str, Any], profile: dict[str, Any]) -> bool:
    """협업 대상이 인계자와 같은 팀인지: 소속(없으면 이름)이 인계자 팀 이름과 같다(부서 이름이 붙어 있어도 된다)."""
    team = normalize_title(own_team(profile))
    org = normalize_title(str(fields.get("org") or "")) or normalize_title(str(fields.get("name") or ""))
    return bool(team) and bool(org) and (org == team or org.endswith(team))


def unit_names(profile: dict[str, Any]) -> list[str]:
    """인계자의 업무 단위 이름들(단위업무 + 세부업무). 단위업무를 고르지 않은 예전 세션은 단위 업무명·담당 업무를 쓴다."""
    names = [n for u in profile_units(profile) for n in (u["name"], *u["regular"], *u["irregular"]) if n]
    if not names:
        names = [str(profile.get(key) or "") for key in ("task_name", "duties")]
    return [n for n in names if n.strip()]


def related_to_units(text: str, profile: dict[str, Any]) -> bool:
    """부서별 추가 자료(인수인계 목록의 서류·물품)가 인계자의 업무 단위와 관련 있는지: 이름이 글자쌍 2개 이상 겹친다."""
    target = bigrams(text)
    return any(len(target & bigrams(name)) >= 2 for name in unit_names(profile))


def _seed_dept_targets(spec: Any, profile: dict[str, Any], items: list[dict[str, Any]]) -> None:
    """소속 부·팀 인수인계 목록 중 인계자의 업무 단위와 관련된 서류·물품만 '부서별 추가 자료' 항목으로 미리 만든다.

    자료에서 관리 내용을 찾지 못한 항목은 다른 칸처럼 빈 칸 질문(노션 질문 예시 · 1질문 1암묵지 · 빠진 세부사항만)으로 묻는다.
    """
    info = clean_dept_info((profile or {}).get("dept_notes"))
    if not has_info(info):
        return
    for target in dept_targets(info):
        name = target["name"]
        if not related_to_units(name, profile) or any(same_name(item_label(spec, it), name) for it in items):
            continue
        items.append({"id": new_id(spec.key), "title": name, "fields": {"topic": name}, "field_meta": {"topic": {"rank": RANK_USER, "ts": None, "doc_class": "user", "label": "부서 인수인계 목록"}},
                      "citations": [], "origin": "user", "confirmed": True})


def _object_particle(word: str) -> str:
    """목적격 조사(을/를). 마지막 글자에 받침이 있으면 '을'."""
    last = word.strip()[-1:] or "가"
    return "을" if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 else "를"


def _field_question(spec: Any, item: dict[str, Any], key: str) -> dict[str, Any]:
    """빈 칸 하나를 묻는 코드 질문의 문장과 묻는 방식(예/아니요·주기 선택).
    노션 양식의 구체적 질문 예시(첫 필수 세부사항을 묻는 문장)가 있으면 그 문장으로, 없으면 보완 질문 예시 문장 그대로 묻는다(first_ask_example)."""
    name = item_label(spec, item)
    label = spec.label(key)
    field = spec.field(key)
    example = first_ask_example(spec.key, key)
    question = f"'{name}'의 {label}에 대해 여쭤볼게요.\n{example}" if example else f"'{name}'의 {label}{_object_particle(label)} 알려 주시겠어요?"
    out: dict[str, Any] = {"question": question}
    if field is not None and field.picker == "period":
        out.update(
            question=f"'{name}'의 {label}를 알려 주세요. 이 업무는 얼마나 자주 하나요?\n정기적으로 하지 않는 업무라면 달력에서 수행 기간을 골라 주세요.",
            picker="period",
        )
    elif field is not None and field.gate and gate_question(spec.key, key):
        out.update(question=f"'{name}' — {gate_question(spec.key, key)}", gate="ask", follow=question)
    return out


def _gap(spec: Any, item_id: str, description: str, question: str, **extra: Any) -> Gap:
    return {
        "id": new_id("gap"),
        "slot": spec.key,
        "item_id": item_id,
        "field": "",
        "description": description,
        "question": question,
        "priority": 2,
        "ask_for_document": False,
        "status": "open",
        **extra,
    }


def _missing_field_gaps(slots: dict[str, Any], active: list[Any], gaps: list[Gap]) -> list[Gap]:
    """양식의 묻는 항목(필수 질문·보조질문)이 비어 있는데 그 칸을 묻는 질문이 없으면 코드가 질문을 만든다(모델이 빠뜨려도 양식의 모든 칸을 묻게).

    질문 하나에는 칸 하나만 묻는다. 노션 양식에 그 칸의 질문 예시가 있으면 그 문장으로 묻고, 예/아니요 항목은 예/아니요로 먼저 묻는다.
    """
    made: list[Gap] = []
    for spec in active:
        if spec.embed_into:
            continue
        for item in (slots.get(spec.key) or {}).get("items", []):
            asked_fields = {g.get("field") for g in gaps if g.get("item_id") == item.get("id") and g.get("field")}
            name = item_label(spec, item)
            for key in missing_fields(spec, item, aux=True):
                if key in asked_fields or key == spec.title_field:
                    continue
                aux = spec.ask_of(key, item.get("fields")) == ASK_AUX
                made.append(_gap(spec, item["id"], f"'{name}'의 {spec.label(key)} 비어 있음", field=key, aux=aux, **_field_question(spec, item, key)))
    return made


def _children_of(child: Any, slots: dict[str, Any], parent_item: dict[str, Any]) -> list[dict[str, Any]]:
    parent = SLOT_BY_KEY[child.embed_into]
    name = str((parent_item.get("fields") or {}).get(parent.title_field) or item_label(parent, parent_item))
    return [c for c in (slots.get(child.key) or {}).get("items", []) if same_name(str((c.get("fields") or {}).get(child.embed_link) or ""), name)]


def _embedded_gaps(slots: dict[str, Any], active: list[Any], gaps: list[Gap]) -> list[Gap]:
    """카드 안 표(협업관계·추진 경과)의 질문. 표의 칸은 자료로 채우고, 다음만 카드(정기업무·프로젝트)마다 한 번 묻는다.

    - 협업관계: 협업할 때 유의해야 할 사람이 있는지 예/아니요로 묻고, 예면 누구와 무엇을 유의해야 하는지 묻는다.
    - 추진 경과: 자료에 경과가 없으면 경과를 묻고, 지금 진행 중인 단계가 있으면 그 단계가 맞는지 확인한다(아니요면 실제 진행 상황을 묻는다).
    """
    made: list[Gap] = []
    asked = {(g.get("slot"), g.get("parent_id")) for g in gaps if g.get("parent_id")}
    for child in active:
        if not child.embed_into or child.embed_into not in slots:
            continue
        parent = SLOT_BY_KEY[child.embed_into]
        for parent_item in (slots.get(child.embed_into) or {}).get("items", []):
            if (child.key, parent_item.get("id")) in asked:
                continue
            name = item_label(parent, parent_item)
            rows = _children_of(child, slots, parent_item)
            if child.gate_question:
                if any(str((r.get("fields") or {}).get("note") or "").strip() for r in rows):
                    continue  # 자료에서 유의사항을 이미 찾았다
                made.append(
                    _gap(child, "", f"'{name}'의 {child.embed_label} 유의사항 확인", child.gate_question.format(name=name),
                         field="note", parent_id=parent_item["id"], gate="ask", follow=child.follow_question.format(name=name))
                )
            elif child.key == "milestones":
                current = [r for r in rows if "진행" in str((r.get("fields") or {}).get("status") or "")]
                if not rows:
                    made.append(_gap(child, "", f"'{name}'의 추진 경과가 자료에 없음", f"'{name}' 프로젝트에 대해 여쭤볼게요.\n{first_ask_example('milestones', '*')}", parent_id=parent_item["id"]))
                elif current:
                    row = current[-1].get("fields") or {}
                    content = re.sub(r"\s*(함|임|중)?\.?\s*$", "", str(row.get("content") or ""))  # 문서 문체('~함.')는 질문 문장에서 뺀다
                    stage = content + (f"({row['when']})" if row.get("when") else "")
                    made.append(
                        _gap(child, "", f"'{name}'의 현재 진행 단계 확인",
                             f"자료상으로는 '{name}' 프로젝트가 현재 '{stage}' 단계로 보입니다.\n현재 진행 상태가 맞나요?",
                             parent_id=parent_item["id"], gate="confirm", follow=f"'{name}' 프로젝트의 실제 현재 진행 상황을 알려 주시겠어요? 지금 어느 단계이고 다음에 무엇을 하나요?")
                    )
    return made


def _empty_slot_gaps(profile: dict[str, Any], slots: dict[str, Any], active: list[Any], gaps: list[Gap]) -> list[Gap]:
    """장에 항목이 하나도 없을 때 그 장의 항목을 묻는다. 인계자가 단위업무·세부업무를 입력했으면 입력한 것만 쓰므로 묻지 않는다(주요 연락망은 보조질문)."""
    made: list[Gap] = []
    asked = {g.get("slot") for g in gaps if not g.get("item_id") and not g.get("parent_id")}
    for spec in active:
        if not spec.empty_ask or spec.key in asked or (slots.get(spec.key) or {}).get("items"):
            continue
        if spec.seed and has_seeds(profile):
            continue
        if spec.key == "dept_notes" and has_info(clean_dept_info(profile.get("dept_notes"))):
            continue  # 인수인계 목록이 있는데 업무 단위와 관련된 것이 없으면 묻지 않는다
        made.append(_gap(spec, "", f"{spec.title} 정보 보완 필요", spec.default_question, aux=spec.empty_ask == ASK_AUX))
    return made


def _office_gaps(profile: dict[str, Any], slots: dict[str, Any], gaps: list[Gap]) -> list[Gap]:
    """팀장용 원내 양식: 목록별 질문은 하지 않고, 인수자에게 필요한 현안·주의점(암묵지)만 한 번 포괄적으로 묻는다. 답은 '기타 주요사항' 등 맞는 목록에 정리한다."""
    if not is_office(profile) or any(g.get("slot") == OFFICE_SLOT.key for g in gaps):
        return []
    items = (slots.get(OFFICE_SLOT.key) or {}).get("items", [])
    target = next((it for it in items if item_label(OFFICE_SLOT, it).startswith("15.")), items[-1] if items else None)
    if target is None:
        return []
    return [_gap(OFFICE_SLOT, target["id"], "현안·미결사항·주의점(암묵지) 확인", OFFICE_QUESTION, field="content", aux=True)]


def allowed_gap(gap: Gap, slots: dict[str, Any], profile: dict[str, Any]) -> bool:
    """엔진(모델)이 제안한 질문 중 양식의 묻는 칸에 맞는 것만 남긴다.

    - 묻지 않는 장·칸(미질의·내부 값, 카드 안 표, 질의응답으로만 채우는 칸)과 항목을 정하지 않은 질문은 버린다(필요한 질문은 코드가 만든다).
    - 칸이 통째로 비었으면 코드가 노션 질문 예시 문장으로 묻는다. 모델 질문은 자료에서 구체적인 사례를 찾아 묻는 칸(중점 관리사항·의사결정 등)만 남긴다.
    - 칸에 값이 있으면 빠진 세부사항(element)만 묻는 질문이어야 한다. 노션 표가 묻는 세부사항을 정해 둔 칸(수행 절차)은 그 세부사항만 묻는다.
    """
    spec = SLOT_BY_KEY.get(gap.get("slot", ""))
    if spec is None or spec.embed_into or not gap.get("item_id") or not gap.get("field"):
        return False
    item = find_item((slots.get(spec.key) or {}).get("items", []), item_id=gap["item_id"])
    field = spec.field(gap["field"])
    if item is None or field is None or field.answers_only or field.key == spec.title_field:
        return False
    ask = spec.ask_of(field.key, item.get("fields"))
    if ask not in (ASK_MAIN, ASK_AUX):
        return False
    filled = bool(str((item.get("fields") or {}).get(field.key) or "").strip())
    if filled:
        element = str(gap.get("element") or "").strip()
        return bool(element) and element_askable(spec.key, field.key, element)
    if field.specific:
        return True
    return ask == ASK_MAIN and not field.gate and not field.picker and not first_ask_example(spec.key, field.key)


def element_is_aux(slot_key: str, field_key: str, element: str) -> bool:
    """빠진 세부사항이 노션 양식의 '(보조)' 세부사항이면 보조질문으로 묻는다(예: 수행 절차의 검토 기준·승인 절차).
    '(필수)'(노션 굵은 글씨) 세부사항은 반드시 채워야 하므로 필수 질문으로 묻는다."""
    return match_element(slot_key, field_key, element).startswith(AUX)


def collect_gaps(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    gaps: list[Gap] = []
    profile = state.get("profile", {})
    slots = state.get("slots") or {}
    active = slots_for(profile)
    for key in (s.key for s in active):
        for g in copy.deepcopy(slots.get(key, {}).get("proposed_gaps", [])):
            if allowed_gap(g, slots, profile):
                field = SLOT_BY_KEY[g["slot"]].field(g.get("field", ""))
                g["aux"] = element_is_aux(g["slot"], g.get("field", ""), g.get("element", "")) or (bool(g.get("element")) and field is not None and field.ask == ASK_AUX)
                g["proposed"] = True
                gaps.append(g)
    gaps.extend(_missing_field_gaps(slots, active, gaps))  # 양식의 묻는 칸이 빈 채로 남지 않게 묻는다
    gaps.extend(_embedded_gaps(slots, active, gaps))  # 카드 안 표: 협업 유의사항·추진 경과 확인
    gaps.extend(_empty_slot_gaps(profile, slots, active, gaps))
    gaps.extend(_office_gaps(profile, slots, gaps))
    ordered = open_gaps(gaps)
    try:  # 장마다 따로 만든 질문 후보에서 중복을 뺀다(순서는 아래에서 양식 순서로 정한다)
        _, drops = runtime.context.engine.plan_questions(profile, ordered)
    except LLMError as exc:
        logger.warning("question planning failed, keep template order: %s", exc)
        drops = set()
    planned = [g for g in ordered if g["id"] not in drops or not g.get("proposed")]  # 코드가 칸마다 만든 질문은 중복 판정으로 빼지 않는다
    rank_by_template(planned, slots)  # 장 → 세부업무 → 표 행 순서(한 세부업무를 다 묻고 다음 세부업무로)
    _progress("자료 분석을 마쳤어요.", current=len(active), total=len(active))
    return {"gaps": open_gaps(planned)}


def resolve_conflicts(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """같은 필드에 모인 후보 값의 충돌을 우선순위(precedence.py)로 확정한다.

    분석(analyze_slot)·답변 해석(interpret)·재분석(reanalyze)은 값을 '후보'로만 모으고, 이 노드가
    인계자 최신 답변 > 공식 문서 > 회의록·메일 > 날짜 없는 자료 순으로 하나를 고른다. 처리 내역은 항목의
    `conflicts` 에 남는다(최종 문서에는 적용한 값만 쓴다). 같은 함수를 세 지점에 노드로 등록해 쓴다.
    """
    slots = copy.deepcopy(state.get("slots") or {})
    gaps = copy.deepcopy(state.get("gaps") or [])
    merged = merge_same_parties(slots, gaps)  # '재무팀 박지훈 차장' 과 '박지훈 차장님' 처럼 같은 협의 상대는 한 항목으로
    changed = list(dict.fromkeys(resolve_slots(slots) + merged))
    if not changed:
        return {}
    for key in changed:
        _emit({"type": "slot", "key": key, "slot": {k: v for k, v in slots[key].items() if k != "proposed_gaps"}})
    update: dict[str, Any] = {"slots": {key: slots[key] for key in changed}}
    if merged and gaps:
        update["gaps"] = gaps
    return update


PARTY_SLOTS = ("stakeholders",)


def merge_same_parties(slots: dict[str, Any], gaps: list[Any]) -> list[str]:
    """협업 대상 표에서 같은 프로젝트의 같은 상대로 읽히는 행을 합치고, 합쳐진 행을 가리키던 질문의 연결을 바꾼다. 바뀐 슬롯 키를 돌려준다."""
    changed = []
    for key in PARTY_SLOTS:
        items = (slots.get(key) or {}).get("items")
        if not items or len(items) < 2:
            continue
        spec = SLOT_BY_KEY[key]
        before = len(items)
        groups: dict[str, list[dict]] = {}
        for item in items:  # 프로젝트가 다르면 같은 사람이라도 다른 행이다
            groups.setdefault(normalize_title(str((item.get("fields") or {}).get(spec.embed_link) or "")), []).append(item)
        merged: list[dict] = []
        for group in groups.values():
            remap = consolidate_parties(group, party_key="name")
            remap_ids(gaps, remap)
            remap_ids(slots[key].get("proposed_gaps"), remap)
            merged.extend(group)
        if len(merged) == before:
            continue
        slots[key]["items"] = [it for it in items if any(it is m for m in merged)]
        slots[key]["coverage"] = evaluate_coverage(spec, slots[key]["items"])
        changed.append(key)
    return changed


# =========================================================================== STAGE 2
def summarize(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    _stage("summary")
    slots = state.get("slots") or {}
    gaps = state.get("gaps") or []
    sources = state.get("sources") or []
    try:
        narrative = ctx.engine.summarize(state.get("profile", {}), slots, gaps, sources)
    except LLMOutputError:
        narrative = _offline(ctx).summarize(state.get("profile", {}), slots, gaps, sources)
    lines = []
    for spec in slots_for(state.get("profile")):
        slot = slots.get(spec.key) or {}
        coverage = slot.get("coverage", "missing")
        icon = {"sufficient": "🟢", "partial": "🟡", "missing": "🔴"}.get(coverage, "⚪")
        detail = (slot.get("summary") or "").strip()
        if len(detail) > 90:
            detail = detail[:89] + "…"
        lines.append(f"- {icon} **{spec.title}** · {COVERAGE_LABELS.get(coverage, coverage)} ({len(slot.get('items', []))}건){' — ' + detail if detail else ''}")
    remaining = len(open_gaps(gaps))
    if remaining:
        closing = (
            f"부족한 부분을 채우기 위해 **한 번에 하나씩** 질문드릴게요(모두 {remaining}개). "
            "모르는 내용은 '건너뛰기', 관련 파일이 있으면 언제든 첨부해 주세요."
        )
    else:
        closing = "자료만으로 필요한 정보가 충족되었어요. 바로 인수인계서를 작성할게요."
    message = f"{narrative}\n\n" + "\n".join(lines) + f"\n\n{closing}"
    coverage = {k: (slots.get(k) or {}).get("coverage", "missing") for k in (s.key for s in slots_for(state.get("profile")))}
    summary_turn = _say("summary", message, coverage=coverage)
    return {"stage": "qna", "transcript": [summary_turn]}


# =========================================================================== STAGE 3
def choose_next(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    if state.get("finish_requested"):
        return {"next_action": "compose"}
    resume = state.get("resume_review") or ""
    if resume:  # 문서 검토 중 고른 질문에 답(또는 건너뜀)했으면 남은 질문 없이 바로 문서를 다시 만든다
        target = next((g for g in state.get("gaps") or [] if g.get("id") == resume), None)
        if target is not None and target.get("status") in ("open", "asked"):
            return {"next_action": "ask"}
        ack = state.get("ack") or ""
        done = _say("info", (ack + "\n\n" if ack else "") + "고친 내용으로 인수인계서를 다시 만들게요.")
        return {"next_action": "compose", "ack": "", "resume_review": "", "transcript": [done]}
    remaining = open_gaps(state.get("gaps") or [])
    if remaining:  # 질문 수는 제한하지 않는다(남은 질문을 모두 묻는다)
        return {"next_action": "ask"}
    ack = state.get("ack") or ""
    text = "질문을 모두 마쳤어요. 건너뛴 내용은 화면 오른쪽 '확인 필요 사항'에 남겨 두고 인수인계서를 작성할게요."
    done = _say("info", (ack + "\n\n" if ack else "") + text)
    return {"next_action": "compose", "ack": "", "transcript": [done]}


def route_next(state: AgentState) -> Literal["ask", "compose"]:
    return "ask" if state.get("next_action") == "ask" else "compose"


def ask(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    gaps = copy.deepcopy(state.get("gaps") or [])
    gap = open_gaps(gaps)[0]
    spec = SLOT_BY_KEY[gap["slot"]]
    if state.get("stage") != "qna":
        _stage("qna")
    ack = state.get("ack") or ""
    content = (ack + "\n\n" if ack else "") + gap["question"]  # 장 이름은 메시지 meta 의 slot 으로 화면 태그에 표시되므로 본문 앞에 붙이지 않는다
    if gap.get("ask_for_document"):
        content += "\n\n📎 관련 자료(계약서·매뉴얼 등)가 있다면 파일로 올려 주셔도 됩니다."
    for g in gaps:
        if g["id"] == gap["id"]:
            g["status"] = "asked"
    count = state.get("question_count", 0) + 1
    meta: dict[str, Any] = {"quick_replies": QUICK_REPLIES_QNA, "allow_files": True}
    if gap.get("gate"):  # 예/아니요로 먼저 묻는 질문은 두 선택지만 보인다
        meta.update(quick_replies=QUICK_REPLIES_YESNO, allow_files=False, choice="yesno")
    if gap.get("picker") == "period":  # 주기를 고르거나, 정기적이지 않으면 달력에서 기간을 고른다
        meta.update(picker="period", choices=list(PERIOD_CHOICES), allow_files=False)
    if gap.get("aux"):
        meta.update(aux=True, aux_note=AUX_NOTE)
    question = _say(
        "question",
        content,
        gap_id=gap["id"],
        slot=gap["slot"],
        number=count,
        remaining=len(open_gaps(gaps)),
        **meta,
    )
    return {"gaps": gaps, "current_gap_id": gap["id"], "question_count": count, "ack": "", "stage": "qna", "transcript": [question]}


def wait_user(state: AgentState) -> dict[str, Any]:
    """사람의 입력을 기다린다(HITL). 재개 값: {"type": "message"|"skip"|"finish"|"files", ...}"""
    event = interrupt({"waiting_for": "user", "stage": state.get("stage", "")})
    return {"event": event if isinstance(event, dict) else {"type": "message", "text": str(event)}}


def route_event(state: AgentState) -> Literal["interpret", "skip_gap", "reanalyze", "request_finish"]:
    kind = (state.get("event") or {}).get("type")
    return {"skip": "skip_gap", "files": "reanalyze", "finish": "request_finish"}.get(kind, "interpret")


def request_finish(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    note = _say("info", "지금까지 정리한 내용으로 인수인계서를 작성할게요.")
    return {"finish_requested": True, "pending": {}, "transcript": [note]}


def skip_gap(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    gaps = copy.deepcopy(state.get("gaps") or [])
    current = state.get("current_gap_id")
    for g in gaps:
        if g["id"] == current and g.get("status") in ("asked", "open", "answered"):
            g["status"] = "skipped"
    user = turn("user", "skip", "(건너뛰기)")
    return {
        "gaps": gaps,
        "pending": {},
        "ack": "건너뛰었어요. 이 내용은 화면 오른쪽 '확인 필요 사항'으로 남겨 둘게요.",
        "last_intent": "skip",
        "transcript": [user],
    }


def route_after_skip(state: AgentState) -> Literal["choose_next", "wait_user"]:
    return "wait_user" if state.get("stage") == "review" else "choose_next"


SKIP_ACK = "건너뛰었어요. '확인 필요 사항'으로 남겨 둘게요."
MOVING_ON_RE = re.compile(r"넘어가|다음 질문|건너뛰|건너뛸")
REASK_HINT = "답하기 어려우시면 '모르겠어요'라고 하시거나 건너뛰기를 눌러 주세요."


def interpret(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    raw_text = str((state.get("event") or {}).get("text", "")).strip()
    # 비밀번호는 AI·지식베이스에 남기지 않는다(보안 서약). 값은 가리고, 출력본에 수기로 적도록 안내한다.
    text, redacted = scrub_credentials(raw_text)
    user_turn = turn("user", "answer", text)
    gaps = copy.deepcopy(state.get("gaps") or [])
    slots = copy.deepcopy(state.get("slots") or {})
    current = next((g for g in gaps if g["id"] == state.get("current_gap_id")), None)
    pending = state.get("pending") or {}
    stage = state.get("stage", "qna")
    data = InterpretInput(
        profile=state.get("profile", {}),
        message=text,
        stage=stage,
        current_gap=current if stage != "review" else None,
        pending=pending or None,
        slots=slots,
        open_gaps=open_gaps(gaps),
        transcript=list(state.get("transcript") or []),
    )
    if stage != "review" and current and not pending and current.get("gate"):
        answered = _answer_gate(ctx, state, user_turn, current, gaps, slots, text)  # 예/아니요 선택지 답은 바로 처리한다
        if answered is not None:
            return answered
        if not current.get("gate"):  # '예, …'처럼 내용까지 답했으면 이어지는 질문의 답으로 해석한다
            data.current_gap = current

    if stage != "review" and current and not pending and current.get("picker") == "period":
        picked = _answer_period(ctx, state, user_turn, current, gaps, slots, text)  # 고른 주기·기간은 바로 기록한다
        if picked is not None:
            return picked

    if stage != "review" and current and not pending and current.get("field") and current.get("item_id") and is_none_answer(text):
        # '그런 것은 없어'처럼 해당하는 것이 없다는 답: 모델에 맡기지 않고 그 칸을 '해당 없음'으로 채운 뒤 다음 질문으로 넘어간다
        return _record_none(ctx, state, user_turn, current, gaps, slots, text)

    if pending and stage != "review" and is_plain_confirm(text):
        # 확인 단추·'네' 는 해석할 것이 없다. LLM 이 확인 답변을 새 정보로 오해해 같은 정리를 다시 되묻는 일도 막는다.
        result = Interpretation(intent="confirm", reply="확인했습니다.")
    else:
        try:
            result = ctx.engine.interpret(data, ctx.kb)
        except LLMOutputError:
            result = _offline(ctx).interpret(data, ctx.kb)
        if result.intent == "confirm" and result.updates and pending and stage != "review":
            # 맞다고 하면서 정리한 내용을 그대로 되돌려 준 경우: 새 정보가 아니므로 확정으로 처리한다.
            result = Interpretation(intent="confirm", reply=result.reply, resolved_gap_ids=result.resolved_gap_ids)

    out: dict[str, Any] = {"last_intent": result.intent, "transcript": [user_turn]}
    intent = result.intent
    if redacted:
        out["transcript"].append(
            _say("info", f"🔒 비밀번호는 보안상 기록하지 않았어요({CREDENTIAL_MASK}). ID/PW 는 완성된 인수인계서를 출력한 뒤 시스템 접근 계정 표에 직접 적어 주세요.")
        )

    if intent == "finish":
        out["transcript"].append(_say("info", result.reply or "지금까지 정리한 내용으로 인수인계서를 작성할게요."))
        out.update(finish_requested=True, pending={})
        return out

    if intent == "skip" and stage != "review":
        if current:
            current["status"] = "skipped"
        # 모델이 만든 문장에는 새 질문이 섞일 수 있어(한 메시지에 질문 두 개) 정해진 문구만 쓴다.
        out.update(gaps=gaps, pending={}, ack=SKIP_ACK)
        return out

    if intent == "confirm" and not result.updates:
        if stage == "review":
            out["transcript"].append(_say("info", result.reply or "좋습니다. 문서 탭에서 PDF 나 Word 로 내려받으실 수 있어요."))
            return out
        for slot_key, item_id in (pending or {}).get("refs", []):
            item = find_item((slots.get(slot_key) or {}).get("items", []), item_id=item_id)
            if item:
                item["confirmed"] = True
        resolved = set(result.resolved_gap_ids) | {pending.get("gap_id", "") if pending else "", current["id"] if current else ""}
        for g in gaps:
            if g["id"] in resolved and g.get("status") in ("asked", "answered", "open"):
                g["status"] = "resolved"
        # 직전 확인 메시지에서 이미 정리해 보여 줬으므로, 다음 질문 앞에 다시 정리·확인 문구를 붙이지 않는다.
        out.update(gaps=gaps, slots=slots, pending={}, ack="")
        out["last_intent"] = "confirm"
        return out

    if result.updates:
        refs = _apply_updates(ctx, state, slots, result, text, current)
        resolved = set(result.resolved_gap_ids)
        if current and stage != "review":
            resolved.add(current["id"])
        for g in gaps:
            if g["id"] in resolved and g.get("status") in ("asked", "open"):
                g["status"] = "answered"
        out.update(slots={k: slots[k] for k in {r[0] for r in refs}}, gaps=gaps)
        if stage == "review":
            out["transcript"].append(_say("info", result.reply or "말씀하신 내용을 반영해서 문서를 다시 작성할게요."))
            out["doc_dirty"] = True
        else:
            rounds = int(pending.get("rounds", 0)) + 1 if pending else 0
            if rounds >= MAX_CONFIRM_ROUNDS:
                # 같은 내용을 여러 번 고쳐도 확인이 끝나지 않으면 마지막 정리로 반영하고 다음 질문으로 넘어간다(무한 반복 방지).
                out.update(pending={}, last_intent="confirm", ack="고쳐 주신 내용으로 반영해 둘게요. 문서 초안에서도 다시 고치실 수 있어요.")
                return out
            out["transcript"].append(
                _say("confirm", result.reply or "정리한 내용이 맞는지 확인해 주세요.", quick_replies=QUICK_REPLIES_CONFIRM, refs=refs)
            )
            keys = {item_id: list((up.get("fields") or {}).keys()) for (_, item_id), up in zip(refs, result.updates)}
            out["pending"] = {"gap_id": current["id"] if current else "", "refs": refs, "keys": keys, "rounds": rounds}
        return out

    # 되묻기·잡담·해석 불가: 답한 뒤 같은 질문에 대한 답을 기다린다. 질문이 있는 단계에서는 질문을 다시 보여 주고, 건너뛰는 방법도 알려 준다.
    reply = result.reply or "말씀하신 내용을 정확히 이해하지 못했어요. 조금 더 구체적으로 알려 주시겠어요?"
    if stage != "review" and current and result.intent == "other":
        # 모델(Opus)의 설명은 그대로 쓴다. '다음 질문으로 넘어가겠습니다'처럼 사실과 다른 문장만 정해진 문구로 바꾼다.
        if MOVING_ON_RE.search(reply):
            reply = "말씀하신 내용만으로는 답을 알 수 없어서 넘어가지 않았어요. 다시 여쭤볼게요."
        question = current.get("question", "")
        asked_again = "?" in reply or len(bigrams(question) & bigrams(reply)) >= len(bigrams(question)) * 0.5
        if question and not asked_again:  # 모델이 질문을 다시 했으면 원래 질문은 덧붙이지 않는다(한 메시지에 질문 하나)
            reply += f"\n\n{question}"
        reply += f"\n\n{REASK_HINT}"
    out["transcript"].append(_say("info", reply, quick_replies=QUICK_REPLIES_QNA if stage != "review" else []))
    return out


NONE_VALUE = "해당 없음"


def _record_value(ctx: AgentContext, state: AgentState, current: Gap, slots: dict, text: str, value: str) -> dict[str, Any]:
    """묻는 칸에 값을 바로 기록한다(모델 해석 없이). 바뀐 장을 돌려준다."""
    spec = SLOT_BY_KEY[current["slot"]]
    item = find_item((slots.get(spec.key) or {}).get("items", []), item_id=current["item_id"])
    update = {"slot": spec.key, "item_id": current["item_id"], "title": item_label(spec, item) if item else "", "fields": {current["field"]: value}, "citations": []}
    _apply_updates(ctx, state, slots, Interpretation(intent="answer", updates=[update]), text, current)
    return {spec.key: slots[spec.key]}


def _record_none(ctx: AgentContext, state: AgentState, user_turn: Turn, current: Gap, gaps: list[Gap], slots: dict, text: str) -> dict[str, Any]:
    """묻는 칸에 해당하는 것이 없다는 답을 '해당 없음'으로 기록하고 질문을 마친다(확인을 다시 묻지 않는다).

    칸에 이미 값이 있고 빠진 세부사항만 물었으면(element) 값은 그대로 두고 질문만 마친다.
    """
    spec = SLOT_BY_KEY[current["slot"]]
    item = find_item((slots.get(spec.key) or {}).get("items", []), item_id=current["item_id"])
    current["status"] = "resolved"
    if current.get("element") and str(((item or {}).get("fields") or {}).get(current["field"]) or "").strip():
        return {"last_intent": "confirm", "transcript": [user_turn], "gaps": gaps, "pending": {}, "ack": ""}
    changed = _record_value(ctx, state, current, slots, text, NONE_VALUE)
    ack = f"'{spec.label(current['field'])}'은(는) '{NONE_VALUE}'으로 기록했어요."
    return {"last_intent": "confirm", "transcript": [user_turn], "gaps": gaps, "slots": changed, "pending": {}, "ack": ack}


def _remember_first(gap: Gap) -> None:
    """예/아니요·주기 선택 질문을 이어지는 질문으로 바꾸기 전의 모습을 남긴다(질문 목록에서 다시 답하면 처음 질문부터 다시 묻는다)."""
    gap.setdefault("first", {key: gap.get(key, "") for key in ("question", "gate", "picker", "element")})


YES_RE = re.compile(r"^\s*(예|네|넵|응|어|맞|있|그래|yes|ok|okay)", re.I)
NO_RE = re.compile(r"^\s*(아니|아뇨|없|노\b|no\b)|아니에요|아닙니다|아니야|없어요|없습니다|없음|않아요|않습니다", re.I)
YESNO_WORD_RE = re.compile(r"^\s*(예|네|넵|응|어|맞아요|맞습니다|있어요|있습니다|있음|있어|그래요)[\s,.!~]*", re.I)


def _answer_gate(ctx: AgentContext, state: AgentState, user_turn: Turn, current: Gap, gaps: list[Gap], slots: dict, text: str) -> dict[str, Any] | None:
    """예/아니요로 먼저 묻는 질문의 답.

    - ask(있는지 묻기): 예 → 이어지는 주관식 질문(follow)을 바로 묻는다, 아니요 → 칸을 '해당 없음'으로 기록(칸이 없는 협업 유의사항은 질문만 마침).
    - confirm(맞는지 확인): 예 → 확인 끝, 아니요 → 이어지는 주관식 질문을 바로 묻는다.
    '예, …'처럼 내용까지 함께 답하면 질문을 이어지는 질문으로 바꾸고 None 을 돌려 모델이 그 내용을 해석하게 한다. 예/아니요가 아니면 None.
    """
    is_no = bool(NO_RE.search(text))
    is_yes = not is_no and bool(YES_RE.search(text))
    if not (is_yes or is_no):
        return None
    kind = current["gate"]
    if (kind == "ask" and is_yes) or (kind == "confirm" and is_no):
        _remember_first(current)
        current.update(gate="", question=current.get("follow") or current.get("question", ""), status="asked")
        rest = YESNO_WORD_RE.sub("", text).strip()
        if is_yes and len(rest) >= 6:
            return None  # 내용까지 답했다: 이어지는 질문의 답으로 해석
        current["status"] = "open"  # 다음 순서로 이어지는 질문을 바로 묻는다(같은 순서 자리)
        return {"last_intent": "confirm", "transcript": [user_turn], "gaps": gaps, "pending": {}, "ack": ""}
    if kind == "ask" and current.get("field") and current.get("item_id"):
        return _record_none(ctx, state, user_turn, current, gaps, slots, text)
    current["status"] = "resolved"
    ack = "확인했어요." if kind == "confirm" else ""
    return {"last_intent": "confirm", "transcript": [user_turn], "gaps": gaps, "pending": {}, "ack": ack}


PERIOD_RANGE_RE = re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})\s*~\s*(\d{4})[-./](\d{1,2})[-./](\d{1,2})")


def _answer_period(ctx: AgentContext, state: AgentState, user_turn: Turn, current: Gap, gaps: list[Gap], slots: dict, text: str) -> dict[str, Any] | None:
    """수행 시기 질문의 선택 답: 주기(매일·매주·매월·매년)를 고르면 기록하고 그 주기 안의 구체적인 시기·기한을 이어서 묻는다.
    달력에서 기간을 고르면('2026-03-02 ~ 2026-05-29') 그 기간을 기록하고 질문을 마친다. 고른 답이 아니면 None(모델이 해석)."""
    compact = text.strip().rstrip(".!")
    choice = next((c for c in PERIOD_CHOICES if compact == c or compact == f"{c} 수행"), None)
    match = PERIOD_RANGE_RE.search(text)
    if not choice and not match:
        return None
    spec = SLOT_BY_KEY[current["slot"]]
    item = find_item((slots.get(spec.key) or {}).get("items", []), item_id=current["item_id"])
    name = item_label(spec, item) if item else "이 업무"
    if match:
        y1, m1, d1, y2, m2, d2 = match.groups()
        period = f"{y1}.{int(m1):02d}.{int(d1):02d} ~ {y2}.{int(m2):02d}.{int(d2):02d}"
        changed = _record_value(ctx, state, current, slots, text, f"비정기 수행(기간: {period})함.")
        current["status"] = "resolved"
        return {"last_intent": "confirm", "transcript": [user_turn], "gaps": gaps, "slots": changed, "pending": {}, "ack": f"수행 시기를 '{period}'로 기록했어요."}
    changed = _record_value(ctx, state, current, slots, text, f"수행 주기: {choice} 수행함.")
    _remember_first(current)
    current.update(  # 같은 칸의 빠진 세부사항(구체적인 시기·완료 기한)을 이어서 묻는다
        picker="",
        element="주요 제출일 및 완료 기한",
        status="open",
        question=f"'{name}'은(는) {choice} 언제 하나요? 반드시 지켜야 할 제출일이나 완료 기한이 있으면 함께 알려 주세요(예: {choice} 5영업일까지 취합 완료).",
    )
    return {"last_intent": "confirm", "transcript": [user_turn], "gaps": gaps, "slots": changed, "pending": {}, "ack": f"수행 주기를 '{choice}'로 기록했어요."}


def _apply_updates(ctx: AgentContext, state: AgentState, slots: dict, result: Interpretation, text: str, current: Gap | None) -> list[list[str]]:
    """해석 결과를 슬롯에 반영하고, 인계자 답변 원문을 지식베이스에도 저장한다(이후 검색·근거로 활용)."""
    question = current.get("question", "") if current else ""
    first_slot = SLOT_BY_KEY[result.updates[0]["slot"]].title
    source_id = None
    try:
        body = (f"[질문] {question}\n" if question else "") + f"[인계자 답변] {text}"
        source_id = ctx.kb.add_text(body, source_name=f"인계자 답변 - {first_slot}", source_type="chat", metadata={"gap_id": current["id"] if current else ""})
    except Exception as exc:  # 지식베이스 저장 실패가 대화를 막지 않게 한다
        logger.warning("failed to store answer in KB: %s", exc)
    user_citation = {
        "source_id": source_id or "chat",
        "label": f"인계자 답변 ({date.today().isoformat()} 대화)",
        "chunk_id": "",
        "quote": text if len(text) <= 120 else text[:119] + "…",
        "rank": RANK_USER,
        "ts": now_ts(),
        "doc_class": "user",
    }
    refs: list[list[str]] = []
    for update in result.updates:
        slot_key = update["slot"]
        if SLOT_BY_KEY[slot_key].seed == "office":  # 원내 양식의 목록 이름은 정해져 있어 답변으로 바꾸지 않는다(내용만 고친다)
            update = {**update, "title": "" if update.get("item_id") else update.get("title", ""), "fields": {k: v for k, v in (update.get("fields") or {}).items() if k != "name"}}
        slot = slots.setdefault(slot_key, {"key": slot_key, "items": [], "coverage": "missing"})
        item = upsert_item(
            slot,
            item_id=update.get("item_id", ""),
            title=update.get("title", ""),
            fields=update.get("fields") or {},
            citations=list(update.get("citations") or []) + [user_citation],
            origin="user",
            confirmed=False,
            defer=True,  # 문서 값과의 충돌은 resolve_conflicts 노드가 우선순위로 확정한다
        )
        slot["coverage"] = evaluate_coverage(SLOT_BY_KEY[slot_key], slot["items"])
        refs.append([slot_key, item["id"]])
    return refs


def route_after_interpret(state: AgentState) -> Literal["choose_next", "wait_user", "compose"]:
    if state.get("finish_requested"):
        return "compose"
    if state.get("stage") == "review":
        return "compose" if state.get("doc_dirty") else "wait_user"
    if state.get("pending"):
        return "wait_user"
    if state.get("last_intent") in ("confirm", "skip"):
        return "choose_next"
    return "wait_user"


def reanalyze(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """대화 중 추가된 자료를 반영해 부족했던 슬롯을 다시 분석한다(재분석)."""
    ctx = runtime.context
    event = state.get("event") or {}
    names = [str(n) for n in event.get("names", [])]
    _progress(f"새 자료 {len(names)}건을 분석에 반영하고 있어요.", status="running")
    slots = copy.deepcopy(state.get("slots") or {})
    gaps = copy.deepcopy(state.get("gaps") or [])
    previous = copy.deepcopy(gaps)
    profile = state.get("profile", {})
    targets = [s.key for s in slots_for(profile)]  # 새 자료는 어느 장에든 정보를 더할 수 있으므로 전체를 다시 본다
    parents = [k for k in targets if not SLOT_BY_KEY[k].embed_into]

    with ThreadPoolExecutor(max_workers=max(1, ctx.config.max_concurrency)) as pool:
        results = dict(zip(parents, pool.map(lambda k: _analyze(ctx, k, profile), parents)))
    for key in parents:
        settle_items(SLOT_BY_KEY[key], profile, results[key].slot)
    link_slots = {**slots, **{k: results[k].slot for k in parents}}
    for key in (k for k in targets if SLOT_BY_KEY[k].embed_into):  # 카드 안 표는 받는 장의 항목 이름을 알고 분석한다
        results[key] = _analyze(ctx, key, {**profile, "_link_slots": link_slots})
        fix_party_names(SLOT_BY_KEY[key], results[key].slot.get("items") or [], link_slots)

    changes = []
    for key in targets:
        spec = SLOT_BY_KEY[key]
        old = slots.get(key) or {"key": key, "items": []}
        new = dict(results[key].slot)
        old_titles = {i.get("title") for i in old.get("items", [])}
        for item in old.get("items", []):
            if item.get("origin") == "document":
                continue
            # 인계자가 알려 준 내용은 재분석 결과보다 우선한다(같은 항목이면 필드를 덮어써서 합친다)
            match = find_item(new.get("items", []), title=item.get("title", ""))
            if match is None:
                new.setdefault("items", []).append(item)
                continue
            merge_item_fields(match, item, defer=True)  # 값이 다르면 우선순위(인계자 답변 > 공식 문서 > 회의록·메일 > 날짜 없는 자료)로 정한다
            known = {(c.get("label"), c.get("quote")) for c in match.get("citations", [])}
            match.setdefault("citations", []).extend(c for c in item.get("citations", []) if (c.get("label"), c.get("quote")) not in known)
            match["origin"] = "mixed"
            match["confirmed"] = bool(item.get("confirmed"))
            match["id"] = item.get("id", match.get("id"))
        settle_items(spec, profile, new)  # 인계자가 입력한 세부업무만, 질의응답으로만 채우는 칸 비우기, 내부/외부 구분
        new["coverage"] = combine_coverage(evaluate_coverage(spec, new.get("items", [])), results[key].slot.get("coverage"))
        added = [i for i in new.get("items", []) if i.get("title") not in old_titles]
        if added:
            changes.append(f"{spec.title} +{len(added)}건")
        slots[key] = new
        _emit({"type": "slot", "key": key, "slot": new})
        kept = [g for g in gaps if g.get("slot") != key or g.get("status") in ("resolved", "skipped", "answered")]
        seen = {g.get("question") for g in kept}
        fresh = [g for g in results[key].gaps if g.get("question") not in seen]
        gaps = kept + [dict(g, proposed=True, aux=element_is_aux(g["slot"], g.get("field", ""), g.get("element", "")) or bool(g.get("aux"))) for g in fresh]
    gaps = [g for g in gaps if not g.get("proposed") or g.get("status") != "open" or allowed_gap(g, slots, profile)]
    active = [s for s in slots_for(profile) if s.key in slots]
    gaps += _missing_field_gaps(slots, active, gaps)  # 재분석 뒤에도 빈 칸은 묻는다
    gaps += _embedded_gaps(slots, active, gaps)
    gaps += _empty_slot_gaps(profile, slots, active, gaps)

    asked = next((g for g in previous if g["id"] == state.get("current_gap_id") and g.get("status") == "asked"), None)
    for g in gaps:  # 답을 기다리던 질문은 새 분석 결과와 함께 다시 고른다
        if g.get("status") == "asked":
            g["status"] = "open"
    reasked = keep_order(previous, gaps, first=asked, slots=slots)  # 질문 순서는 그대로 두고, 방금 물은 질문을 먼저 다시 묻는다
    sources = ctx.kb.list_sources() or state.get("sources", [])
    summary = f"새 자료({', '.join(names[:3])}{' 외' if len(names) > 3 else ''})를 반영했어요."
    summary += f" 추가로 찾은 내용: {', '.join(changes)}." if changes else " 기존 분석에 더할 새 정보는 찾지 못했어요."
    out: dict[str, Any] = {"slots": slots, "gaps": open_gaps(gaps) + [g for g in gaps if g.get("status") != "open"], "sources": sources}
    user = turn("user", "files", f"(자료 추가: {', '.join(names)})")
    if state.get("stage") == "review":
        out.update(doc_dirty=True, transcript=[user, _say("info", summary + " 문서를 다시 작성할게요.")])
    elif state.get("pending"):
        out["transcript"] = [user, _say("info", summary + " 직전에 정리한 내용이 맞는지 먼저 확인해 주세요.", quick_replies=QUICK_REPLIES_CONFIRM)]
    else:
        out.update(ack=summary, current_gap_id="", transcript=[user])
        if reasked:  # 같은 질문을 다시 묻는 것이라 질문 번호를 올리지 않는다
            out["question_count"] = max(0, int(state.get("question_count") or 0) - 1)
    return out


def route_after_reanalyze(state: AgentState) -> Literal["choose_next", "wait_user", "compose"]:
    if state.get("stage") == "review":
        return "compose"
    if state.get("pending"):
        return "wait_user"
    return "choose_next"


# =========================================================================== STAGE 4
def auto_targets(profile: dict[str, Any], slots: dict[str, Any]) -> list[dict[str, Any]]:
    """문서를 만들 때 채울 미질의 칸: 비어 있는 미질의 칸과, 업무 단위 요약의 진행상황 요약(인계자가 직접 고친 값이 아니면 매번 다시 쓴다)."""
    out: list[dict[str, Any]] = []
    for spec in slots_for(profile):
        for item in (slots.get(spec.key) or {}).get("items", []):
            fields = item.get("fields") or {}
            for key in spec.auto:
                if (spec.field(key) or FieldSpec("", "")).placeholder:
                    continue  # 안내만 보여 주고 인계자가 직접 적는 칸(정기업무 기타(참고자료 등))은 지어 채우지 않는다
                meta = (item.get("field_meta") or {}).get(key) or {}
                refresh = spec.key == "overview" and key == "note" and meta.get("rank") != RANK_USER
                if (str(fields.get(key) or "").strip() and not refresh) or (spec.embed_into and key != "note"):
                    continue  # 카드 안 표의 행은 자료에 있는 값만 둔다(비고만 채울 수 있다)
                out.append({"slot": spec.key, "item_id": item.get("id", ""), "field": key, "label": spec.label(key), "title": item_label(spec, item), "current": str(fields.get(key) or "")})
    return out


def _fill_auto(ctx: AgentContext, profile: dict[str, Any], slots: dict[str, Any]) -> None:
    """미질의 칸(업무 개요·프로젝트 개요·계획기간·진행상황 요약 등)을 문서를 만들 때 지금까지 수집된 자료·답변으로 채운다(묻지 않는 칸).

    인계자가 직접 고친 값(순위가 인계자 답변)은 그대로 둔다. 요약을 못 만들면 자료에서 읽은 값을 둔다.
    """
    targets = [t for t in auto_targets(profile, slots) if not (SLOT_BY_KEY[t["slot"]].embed_into)]
    if not targets:
        return
    try:
        values = ctx.engine.fill_auto(profile, slots, targets)
    except LLMError as exc:
        logger.warning("auto fill failed, keep values: %s", exc)
        return
    for t in targets:
        value = values.get((t["slot"], t["item_id"], t["field"]))
        item = find_item((slots.get(t["slot"]) or {}).get("items", []), item_id=t["item_id"])
        if value and item is not None:
            item.setdefault("fields", {})[t["field"]] = value


def compose(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    _stage("composing")
    _progress("인수인계서를 작성하고 있어요.", status="running")
    slots = copy.deepcopy(state.get("slots") or {})
    _fill_auto(ctx, state.get("profile", {}), slots)
    version = int(state.get("document_version") or 0) + 1
    markdown = render_document(
        profile=state.get("profile", {}),
        slots=slots,
        gaps=state.get("gaps") or [],
        sources=state.get("sources") or [],
        version=version,
    )
    _emit({"type": "document", "markdown": markdown, "version": version})
    _stage("review")
    note = _say(
        "document",
        f"인수인계서 초안(v{version})을 작성했어요. 문서 탭에서 확인해 주세요.\n"
        "고칠 내용이나 빠진 내용을 채팅으로 알려 주시면 반영해서 다시 작성할게요.",
        version=version,
    )
    return {
        "slots": slots,
        "stage": "review",
        "document": markdown,
        "document_version": version,
        "doc_dirty": False,
        "finish_requested": False,
        "resume_review": "",
        "pending": {},
        "transcript": [note],
    }

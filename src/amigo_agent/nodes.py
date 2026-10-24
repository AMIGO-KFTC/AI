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
from .questions import keep_order, rank_by_slot_order
from .relevance import grounded_updates, is_explicit_skip, off_topic_reason
from .llm import LLMError, LLMOutputError
from .precedence import RANK_USER, now_ts
from .render import render_document, unlinked_children
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
    new_id,
    open_gaps,
    resolve_slots,
    turn,
    upsert_item,
)
from .template import COVERAGE_LABELS, SLOT_BY_KEY, STAGE_LABEL, STAGE_NUMBER, slots_for
from .textkit import CREDENTIAL_MASK, scrub_credentials

logger = logging.getLogger(__name__)

QUICK_REPLIES_QNA = ["모르겠어요 (건너뛰기)", "지금 문서 생성"]
QUICK_REPLIES_CONFIRM = ["네, 맞아요", "수정할게요"]
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
    # 다른 장에 붙는 장(협업관계·비상 대응)은 붙는 장의 이름이 나온 뒤에 분석한다(analyze_children)
    return [Send("analyze_slot", {"slot_key": key, "profile": state.get("profile", {})}) for key in (s.key for s in slots_for(state.get("profile"))) if not SLOT_BY_KEY[key].embed_into]


def analyze_slot(payload: dict[str, Any], runtime: Runtime[AgentContext]) -> dict[str, Any]:
    key = payload["slot_key"]
    spec = SLOT_BY_KEY[key]
    _progress(f"'{spec.title}' 분석 중", slot=key, status="running")
    result = _analyze(runtime.context, key, payload.get("profile", {}))
    slot = _slot_with_gaps(result)
    _emit({"type": "slot", "key": key, "slot": result.slot})
    _progress(f"'{spec.title}' 분석 완료 · {COVERAGE_LABELS.get(slot['coverage'], '')}", slot=key, status="done")
    return {"slots": {key: slot}}


def analyze_children(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """다른 장에 붙는 장(주요 협업관계·비상 대응)을 분석한다. 붙는 장(프로젝트·세부 업무)의 항목 이름을 프롬프트에 주어 연결 필드를 그 이름과 글자 그대로 쓰게 한다."""
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
        slot = _slot_with_gaps(result)
        _emit({"type": "slot", "key": key, "slot": result.slot})
        _progress(f"'{spec.title}' 분석 완료 · {COVERAGE_LABELS.get(slot['coverage'], '')}", slot=key, status="done")
        out[key] = slot
    return {"slots": out}


MAX_LINK_GAPS = 2  # 장마다 만드는 연결 질문 수


def _link_gaps(slots: dict[str, Any], active: list[Any], gaps: list[Gap]) -> list[Gap]:
    """협업관계·비상 대응 항목이 붙는 장(프로젝트·세부 업무)의 어느 항목에도 맞지 않으면, 어느 항목인지 묻는 질문을 만든다(문서는 양식에 없는 표를 싣지 않는다)."""
    linked_asked = {g.get("item_id") for g in gaps if g.get("link_field")}  # 이미 연결을 묻는 항목
    any_asked = {g.get("item_id") for g in gaps if g.get("item_id")}
    made: list[Gap] = []
    per_slot: dict[str, int] = {}
    for child, item in unlinked_children(slots, active):
        value = str((item.get("fields") or {}).get(child.embed_link) or "").strip()
        # 연결 값이 비어 있으면 그 값을 묻는 기존 질문이 이미 있는 것이므로 건너뛰고, 값이 있는데 맞는 카드가 없을 때만(연락처 같은 다른 질문과 별개로) 따로 묻는다
        if item.get("id") in linked_asked or (not value and item.get("id") in any_asked) or per_slot.get(child.key, 0) >= MAX_LINK_GAPS:
            continue
        per_slot[child.key] = per_slot.get(child.key, 0) + 1
        parent = SLOT_BY_KEY[child.embed_into]
        names = [str((p.get("fields") or {}).get(parent.title_field) or p.get("title") or "") for p in (slots.get(child.embed_into) or {}).get("items", [])]
        names = [n for n in names if n][:6]
        who = (item.get("fields") or {}).get("party") or item.get("title") or "항목"
        body = str((item.get("fields") or {}).get("discussed") or "")[:40]
        choices = f" ({', '.join(names)} 중에서 알려 주세요)" if names else ""
        made.append(
            {
                "id": new_id("gap"),
                "slot": child.key,
                "item_id": item["id"],
                "description": f"'{who}'의 {child.embed_label}이(가) 어느 {parent.label(parent.title_field)}에 해당하는지 모름",
                "question": f"'{who}'의 {child.embed_label}{f'({body})' if body else ''}은(는) 어느 {parent.label(parent.title_field)}에 해당하나요?{choices}",
                "priority": 1,
                "link_field": child.embed_link,
                "ask_for_document": False,
                "status": "open",
            }
        )
    return made


def collect_gaps(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    gaps: list[Gap] = []
    active = slots_for(state.get("profile"))
    for key in (s.key for s in active):
        gaps.extend(copy.deepcopy((state.get("slots") or {}).get(key, {}).get("proposed_gaps", [])))
    gaps.extend(_link_gaps(state.get("slots") or {}, active, gaps))  # 어느 프로젝트·세부 업무인지 맞출 수 없는 항목은 묻는다
    ordered = open_gaps(gaps)
    try:  # 장마다 따로 만든 질문 후보에서 중복을 뺀다(순서는 아래에서 양식의 장 순서로 정한다)
        order, drops = runtime.context.engine.plan_questions(state.get("profile", {}), ordered)
    except LLMError as exc:
        logger.warning("question planning failed, keep slot order: %s", exc)
        order, drops = [], set()
    ranks = {gid: i for i, gid in enumerate(order)}
    planned = [g for g in ordered if g["id"] not in drops]
    for g in planned:
        if g["id"] in ranks:
            g["rank"] = ranks[g["id"]]
    rank_by_slot_order(planned)  # 엔진이 정한 순서 위에 양식의 장 순서로 후처리(규칙 엔진·Claude 모두 같은 순서가 되게)
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
    """협의 상대 슬롯에서 같은 상대로 읽히는 항목을 합치고, 합쳐진 항목을 가리키던 질문의 연결을 바꾼다. 바뀐 슬롯 키를 돌려준다."""
    changed = []
    for key in PARTY_SLOTS:
        items = (slots.get(key) or {}).get("items")
        if not items or len(items) < 2:
            continue
        before = len(items)
        remap = consolidate_parties(items)
        if len(items) == before:
            continue
        remap_ids(gaps, remap)
        remap_ids(slots[key].get("proposed_gaps"), remap)
        spec = SLOT_BY_KEY[key]
        slots[key]["coverage"] = evaluate_coverage(spec, items)
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
            f"부족한 부분을 채우기 위해 **한 번에 하나씩** 질문드릴게요(최대 {min(remaining, ctx.config.max_questions)}개). "
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
    remaining = open_gaps(state.get("gaps") or [])
    if remaining and state.get("question_count", 0) < runtime.context.config.max_questions:
        return {"next_action": "ask"}
    ack = state.get("ack") or ""
    if remaining:
        text = "주요 질문을 모두 드렸어요. 남은 내용은 문서의 '확인 필요 사항'에 표시하고 인수인계서를 작성할게요."
    else:
        text = "필요한 정보가 모두 모였어요. 인수인계서를 작성할게요."
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
    question = _say(
        "question",
        content,
        gap_id=gap["id"],
        slot=gap["slot"],
        number=count,
        remaining=len(open_gaps(gaps)),
        quick_replies=QUICK_REPLIES_QNA,
        allow_files=True,
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
        "ack": "건너뛰었어요. 이 내용은 문서의 '확인 필요 사항'으로 남겨 둘게요.",
        "last_intent": "skip",
        "transcript": [user],
    }


def route_after_skip(state: AgentState) -> Literal["choose_next", "wait_user"]:
    return "wait_user" if state.get("stage") == "review" else "choose_next"


SKIP_ACK = "건너뛰었어요. '확인 필요 사항'으로 남겨 둘게요."
MOVING_ON_RE = re.compile(r"넘어가|다음 질문|건너뛰|건너뛸")
REASK_HINT = "답하기 어려우시면 '모르겠어요'라고 하시거나 건너뛰기를 눌러 주세요."


def _reask(user_turn: Turn, current: Gap, gaps: list[Gap], pending: dict[str, Any], lead: str) -> dict[str, Any]:
    """기록하지도 건너뛰지도 않고 같은 질문을 다시 묻는다(질문 번호·상태는 그대로)."""
    current["reasked"] = int(current.get("reasked", 0)) + 1
    reply = f"{lead}\n\n{current.get('question', '')}\n\n{REASK_HINT}"
    return {"last_intent": "other", "transcript": [user_turn, _say("info", reply, quick_replies=QUICK_REPLIES_QNA, gap_id=current["id"])], "gaps": gaps, "pending": pending}


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

    if stage != "review" and current and result.intent == "skip" and not is_explicit_skip(text):
        # 모델이 '모름'으로 보고 건너뛰려 해도, 인계자가 모른다·건너뛴다고 분명히 말하지 않았으면('ㅇㅇ'·'음') 건너뛰지 않고 다시 묻는다.
        return _reask(user_turn, current, gaps, pending, "말씀하신 내용만으로는 답을 알 수 없어서 넘어가지 않았어요. 다시 여쭤볼게요.")

    if stage != "review" and current and result.intent in ("answer", "correct") and result.updates and not current.get("link_field"):
        # 모델이 답으로 정리했더라도 질문과 상관없는 말이거나 인계자의 말에 근거가 없는 값이면 기록하지 않는다(코드로 한 번 더 검사).
        item = find_item((slots.get(current.get("slot", "")) or {}).get("items", []), item_id=current.get("item_id", ""))
        reason = off_topic_reason(text, current, item_label(SLOT_BY_KEY[current["slot"]], item) if item else "")
        grounded = [] if reason else grounded_updates(result.updates, text)
        if not grounded:
            return _reask(user_turn, current, gaps, pending, "말씀하신 내용이 이 질문과 관련 있는 답으로 보이지 않아 기록하지 않았어요. 다시 여쭤볼게요.")
        result.updates = grounded

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
        # 모델 문장이 '다음 질문으로 넘어가겠습니다'처럼 사실과 다르거나 질문을 자기 말로 바꿔 다시 물으면(질문이 두 번 나옴) 정해진 문구로 바꾼다
        if MOVING_ON_RE.search(reply) or "?" in reply:
            reply = "말씀하신 내용만으로는 답을 알 수 없어서 넘어가지 않았어요. 다시 여쭤볼게요."
        question = current.get("question", "")
        if question and question not in reply:  # 모델이 이미 질문을 다시 물었으면 질문은 덧붙이지 않는다
            reply += f"\n\n{question}"
        reply += f"\n\n{REASK_HINT}"
    out["transcript"].append(_say("info", reply, quick_replies=QUICK_REPLIES_QNA if stage != "review" else []))
    return out


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
    targets = [s.key for s in slots_for(state.get("profile"))]  # 새 자료는 어느 장에든 정보를 더할 수 있으므로 전체를 다시 본다

    with ThreadPoolExecutor(max_workers=max(1, ctx.config.max_concurrency)) as pool:
        results = dict(zip(targets, pool.map(lambda k: _analyze(ctx, k, state.get("profile", {})), targets)))

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
        new["coverage"] = combine_coverage(evaluate_coverage(spec, new.get("items", [])), results[key].slot.get("coverage"))
        added = [i for i in new.get("items", []) if i.get("title") not in old_titles]
        if added:
            changes.append(f"{spec.title} +{len(added)}건")
        slots[key] = new
        _emit({"type": "slot", "key": key, "slot": new})
        kept = [g for g in gaps if g.get("slot") != key or g.get("status") in ("resolved", "skipped", "answered")]
        seen = {g.get("question") for g in kept}
        fresh = [g for g in results[key].gaps if g.get("question") not in seen]
        gaps = kept + fresh

    asked = next((g for g in previous if g["id"] == state.get("current_gap_id") and g.get("status") == "asked"), None)
    for g in gaps:  # 답을 기다리던 질문은 새 분석 결과와 함께 다시 고른다
        if g.get("status") == "asked":
            g["status"] = "open"
    reasked = keep_order(previous, gaps, first=asked)  # 질문 순서는 그대로 두고, 방금 물은 질문을 먼저 다시 묻는다
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
def compose(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    _stage("composing")
    _progress("인수인계서를 작성하고 있어요.", status="running")
    slots = state.get("slots") or {}
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
        "stage": "review",
        "document": markdown,
        "document_version": version,
        "doc_dirty": False,
        "finish_requested": False,
        "pending": {},
        "transcript": [note],
    }

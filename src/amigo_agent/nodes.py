"""LangGraph 노드 함수.

    STAGE 1 분석   prepare → analyze_slot(슬롯별 병렬, Send) → collect_gaps
    STAGE 2 요약   summarize
    STAGE 3 Q&A    choose_next → ask → wait_user(interrupt) → interpret / skip_gap / reanalyze → …
    STAGE 4 생성   compose → wait_user(검토 의견 대기) → interpret → compose …

노드는 상태를 직접 고치지 않고 바뀐 부분만 dict 로 돌려준다. 화면에 보여줄 메시지·진행률은
get_stream_writer() 로 이벤트를 흘려보내고(백엔드가 받아서 DB 에 저장), 대화 기록은 transcript 에도 남긴다.
"""

from __future__ import annotations

import copy
import logging
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
from .llm import LLMOutputError
from .render import render_document
from .state import (
    AgentState,
    Gap,
    Turn,
    combine_coverage,
    empty_slots,
    evaluate_coverage,
    find_item,
    open_gaps,
    turn,
    upsert_item,
)
from .template import COVERAGE_LABELS, SLOT_BY_KEY, SLOT_KEYS, SLOTS, STAGE_LABEL, STAGE_NUMBER

logger = logging.getLogger(__name__)

QUICK_REPLIES_QNA = ["모르겠어요 (건너뛰기)", "지금 문서 생성"]
QUICK_REPLIES_CONFIRM = ["네, 맞아요", "수정할게요"]


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
    _progress(f"자료 {len(sources)}건을 분석하고 있어요.", current=0, total=len(SLOTS))
    return {
        "stage": "analyzing",
        "sources": sources,
        "slots": empty_slots(),
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
    return [Send("analyze_slot", {"slot_key": key, "profile": state.get("profile", {})}) for key in SLOT_KEYS]


def analyze_slot(payload: dict[str, Any], runtime: Runtime[AgentContext]) -> dict[str, Any]:
    key = payload["slot_key"]
    spec = SLOT_BY_KEY[key]
    _progress(f"'{spec.title}' 분석 중", slot=key, status="running")
    result = _analyze(runtime.context, key, payload.get("profile", {}))
    slot = _slot_with_gaps(result)
    _emit({"type": "slot", "key": key, "slot": result.slot})
    _progress(f"'{spec.title}' 분석 완료 · {COVERAGE_LABELS.get(slot['coverage'], '')}", slot=key, status="done")
    return {"slots": {key: slot}}


def collect_gaps(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    gaps: list[Gap] = []
    for key in SLOT_KEYS:
        gaps.extend(copy.deepcopy((state.get("slots") or {}).get(key, {}).get("proposed_gaps", [])))
    ordered = open_gaps(gaps)
    _progress("자료 분석을 마쳤어요.", current=len(SLOTS), total=len(SLOTS))
    return {"gaps": ordered}


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
    for spec in SLOTS:
        slot = slots.get(spec.key) or {}
        coverage = slot.get("coverage", "missing")
        icon = {"sufficient": "🟢", "partial": "🟡", "missing": "🔴"}.get(coverage, "⚪")
        detail = slot.get("summary") or ""
        lines.append(f"{icon} **{spec.title}** · {COVERAGE_LABELS.get(coverage, coverage)} ({len(slot.get('items', []))}건){' — ' + detail if detail else ''}")
    remaining = len(open_gaps(gaps))
    if remaining:
        closing = (
            f"부족한 부분을 채우기 위해 **한 번에 하나씩** 질문드릴게요(최대 {min(remaining, ctx.config.max_questions)}개). "
            "모르는 내용은 '건너뛰기', 관련 파일이 있으면 언제든 첨부해 주세요."
        )
    else:
        closing = "자료만으로 필요한 정보가 충족되었어요. 바로 인수인계서를 작성할게요."
    message = f"{narrative}\n\n" + "\n".join(lines) + f"\n\n{closing}"
    coverage = {k: (slots.get(k) or {}).get("coverage", "missing") for k in SLOT_KEYS}
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
    content = (ack + "\n\n" if ack else "") + f"**[{spec.title}]** {gap['question']}"
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


def interpret(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    text = str((state.get("event") or {}).get("text", "")).strip()
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
    try:
        result: Interpretation = ctx.engine.interpret(data, ctx.kb)
    except LLMOutputError:
        result = _offline(ctx).interpret(data, ctx.kb)

    out: dict[str, Any] = {"last_intent": result.intent, "transcript": [user_turn]}
    intent = result.intent

    if intent == "finish":
        out["transcript"].append(_say("info", result.reply or "지금까지 정리한 내용으로 인수인계서를 작성할게요."))
        out.update(finish_requested=True, pending={})
        return out

    if intent == "skip" and stage != "review":
        if current:
            current["status"] = "skipped"
        out.update(gaps=gaps, pending={}, ack=result.reply or "건너뛰었어요. '확인 필요 사항'으로 남겨 둘게요.")
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
        out.update(gaps=gaps, slots=slots, pending={}, ack=result.reply if result.reply and len(result.reply) < 60 else "확인했습니다.")
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
            out["transcript"].append(
                _say("confirm", result.reply or "정리한 내용이 맞는지 확인해 주세요.", quick_replies=QUICK_REPLIES_CONFIRM, refs=refs)
            )
            keys = {item_id: list((up.get("fields") or {}).keys()) for (_, item_id), up in zip(refs, result.updates)}
            out["pending"] = {"gap_id": current["id"] if current else "", "refs": refs, "keys": keys}
        return out

    # 되묻기·잡담·해석 불가: 답한 뒤 같은 질문에 대한 답을 기다린다.
    reply = result.reply or "말씀하신 내용을 정확히 이해하지 못했어요. 조금 더 구체적으로 알려 주시겠어요?"
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
    targets = list(SLOT_KEYS)  # 새 자료는 어느 장에든 정보를 더할 수 있으므로 전체를 다시 본다

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
            user_fields = {k: v for k, v in (item.get("fields") or {}).items() if str(v).strip()}
            match.setdefault("fields", {}).update(user_fields)
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

    for g in gaps:  # 답을 기다리던 질문은 새 분석 결과와 함께 다시 고른다
        if g.get("status") == "asked":
            g["status"] = "open"
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
    try:
        extras = ctx.engine.compose_extras(state.get("profile", {}), slots)
    except LLMOutputError:
        extras = _offline(ctx).compose_extras(state.get("profile", {}), slots)
    version = int(state.get("document_version") or 0) + 1
    markdown = render_document(
        profile=state.get("profile", {}),
        slots=slots,
        gaps=state.get("gaps") or [],
        extras=extras,
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

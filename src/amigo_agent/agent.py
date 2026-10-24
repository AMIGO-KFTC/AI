"""HandoverAgent: 백엔드가 쓰는 에이전트 진입점(LangGraph 를 몰라도 쓸 수 있게 감싼다).

    agent = HandoverAgent.with_sqlite("data/agent.sqlite")          # 대화 상태를 SQLite 체크포인트에 영속화
    agent.start(session_id, profile, kb, on_event=save_to_db)       # STAGE 1 분석 → STAGE 2 요약 → 첫 질문
    agent.send_message(session_id, "업체 담당자는 …", kb)              # STAGE 3 답변 처리 → 확인/다음 질문
    agent.notify_files(session_id, kb, ["계약서.pdf"])               # 대화 중 추가 자료 → 재분석
    agent.finish(session_id, kb)                                    # 지금 바로 STAGE 4 문서 생성
    agent.reopen_question(session_id, gap_id, kb)                   # 이미 답한 질문으로 돌아가 다시 묻기(STAGE 3)
    agent.snapshot(session_id)                                      # 단계·슬롯·공백·문서 상태 조회

on_event 콜백은 진행 중 발생하는 이벤트를 즉시 받는다.
    {"type": "stage", "stage": "analyzing", "number": 1, "label": "자료 분석"}
    {"type": "progress", "message": "'협업 관계' 분석 중", ...}
    {"type": "slot", "key": "contacts", "slot": {...}}
    {"type": "message", "role": "assistant", "kind": "question", "content": "...", "meta": {...}}
    {"type": "document", "markdown": "...", "version": 1}
    {"type": "usage", "requests": 1, "input_tokens": 120, "output_tokens": 800, ..., "cost_usd": 0.0172}  # Claude 호출마다
"""

from __future__ import annotations

import copy
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Callable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .config import AgentConfig
from .edits import apply_gantt_edit
from .engine import Engine, make_engine
from .graph import build_graph
from .kb import KBAdapter
from .nodes import AgentContext
from .precedence import RANK_USER
from .qa import QAScope, answer_from_materials
from .render import render_document
from .state import open_gaps

EventHandler = Callable[[dict[str, Any]], None]


class AgentStateError(RuntimeError):
    """현재 상태에서 할 수 없는 요청(예: 분석 전에 메시지 전송)."""


@dataclass
class TurnResult:
    events: list[dict[str, Any]] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def messages(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == "message"]


class HandoverAgent:
    def __init__(
        self,
        config: AgentConfig | None = None,
        *,
        checkpointer: Any | None = None,
        client: Any | None = None,
        engine: Engine | None = None,
    ) -> None:
        self.config = config or AgentConfig()
        self.engine = engine or make_engine(self.config, client=client)
        self.checkpointer = checkpointer or InMemorySaver()
        self.graph = build_graph().compile(checkpointer=self.checkpointer)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @classmethod
    def with_sqlite(cls, path: str | Path, **kwargs: Any) -> HandoverAgent:
        from langgraph.checkpoint.sqlite import SqliteSaver

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        return cls(checkpointer=SqliteSaver(conn), **kwargs)

    # ------------------------------------------------------------------ 정보
    def describe(self) -> dict[str, Any]:
        return {"engine": self.engine.name, "model": self.config.model if self.engine.name == "claude" else None}

    def usage(self) -> dict[str, Any] | None:
        """Claude 엔진이면 지금까지의 API 호출 수·토큰 사용량·추정 비용(USD), 오프라인 엔진이면 None."""
        llm = getattr(self.engine, "llm", None)
        return llm.usage.summary(self.config.model) if llm is not None else None

    def answer_question(self, question: str, history: list[dict[str, str]], scopes: list[QAScope]) -> dict[str, Any]:
        """올린 자료·인계자 답변(지식베이스)과 인수인계서를 근거로 질문에 답한다(상태를 바꾸지 않는다). Claude 엔진이 아니면 관련 근거를 그대로 보여 준다."""
        return answer_from_materials(getattr(self.engine, "llm", None), self.config, question, history, scopes)

    def mermaid(self) -> str:
        return self.graph.get_graph().draw_mermaid()

    # ------------------------------------------------------------------ 실행
    def start(self, thread_id: str, profile: dict[str, Any], kb: Any, on_event: EventHandler | None = None) -> TurnResult:
        """STAGE 1 부터 새로 시작한다(같은 thread 의 이전 상태는 지운다)."""
        with self._lock(thread_id):
            self.checkpointer.delete_thread(thread_id)
            initial = {"profile": dict(profile or {}), "stage": "analyzing", "transcript": []}
            return self._run(thread_id, initial, kb, on_event)

    def send_message(self, thread_id: str, text: str, kb: Any, on_event: EventHandler | None = None) -> TurnResult:
        if not (text or "").strip():
            raise AgentStateError("빈 메시지는 보낼 수 없습니다.")
        return self._resume(thread_id, {"type": "message", "text": text.strip()}, kb, on_event)

    def skip(self, thread_id: str, kb: Any, on_event: EventHandler | None = None) -> TurnResult:
        return self._resume(thread_id, {"type": "skip"}, kb, on_event)

    def reopen_question(self, thread_id: str, gap_id: str, kb: Any, on_event: EventHandler | None = None) -> TurnResult:
        """고른 질문으로 이동해 그 질문을 묻는다(이미 답했거나 건너뛴 질문, 아직 묻지 않은 질문 모두).

        질의응답(STAGE 3)과 문서 검토(STAGE 4) 모두에서 된다. 검토 중에 돌아왔으면 그 질문에 답한(또는 건너뛴) 뒤
        바로 인수인계서를 다시 만든다(resume_review).

        지금 묻고 있던 질문은 순서를 기다리도록 되돌리고, 다시 새로 답하면 앞의 답을 덮어쓴다(인계자 최신 답변이 우선: precedence.py).
        체크포인트를 '다음 질문 고르기' 직후로 옮겨 ask 노드를 다시 돌리는 방식이라 다른 노드는 건드리지 않는다.
        """
        with self._lock(thread_id):
            if not self._has_state(thread_id):
                raise AgentStateError("아직 분석을 시작하지 않았습니다. 자료를 올리고 분석을 먼저 시작해 주세요.")
            if not self.is_waiting(thread_id):
                raise AgentStateError("이전 작업이 끝나지 않았습니다. 잠시 후 다시 시도하거나 '다시 시도'를 눌러 주세요.")
            config = self._config(thread_id)
            values = self.graph.get_state(config).values or {}
            stage = values.get("stage")
            if stage not in ("qna", "review"):
                raise AgentStateError("질의응답이나 문서 검토 중에만 질문으로 돌아갈 수 있습니다.")
            gaps = copy.deepcopy(values.get("gaps") or [])
            target = next((g for g in gaps if g.get("id") == gap_id), None)
            if target is None:
                raise AgentStateError("돌아갈 질문을 찾지 못했습니다.")
            movable = ("answered", "resolved", "skipped", "open") + (("asked",) if stage == "review" else ())  # 검토 중에는 문서 생성 때 묻던 질문도 고를 수 있다
            if target.get("status") not in movable:
                raise AgentStateError("지금 묻고 있는 질문입니다.")
            answered_before = target.get("status") not in ("open", "asked")
            for g in gaps:
                if g.get("status") == "asked":
                    g["status"] = "open"  # 지금 묻던 질문은 이 질문 다음에 다시 묻는다
            slot = (values.get("slots") or {}).get(target.get("slot", "")) or {}
            item = next((it for it in slot.get("items", []) if it.get("id") == target.get("item_id")), None)
            overwrite = [k for k, meta in ((item or {}).get("field_meta") or {}).items() if meta.get("rank") == RANK_USER and str(((item or {}).get("fields") or {}).get(k, "")).strip()]
            target.update(status="open", reopened=answered_before, reopen_fields=overwrite if answered_before else [])  # 새 답은 인계자가 앞서 적은 칸을 고치는 답으로 처리한다
            target["rank"] = min((g.get("rank", 10_000) for g in gaps), default=10_000) - 1  # open_gaps 의 맨 앞으로
            self.graph.update_state(
                config,
                {
                    "gaps": gaps,
                    "pending": {},
                    "ack": (
                        "고르신 질문으로 이동했어요. "
                        + ("새로 답해 주시면 앞의 답을 그 내용으로 고칠게요." if answered_before else "답해 주시면 문서에 반영할게요.")
                        + (" 답하신 뒤 인수인계서를 다시 만들게요." if stage == "review" else "")
                    ),
                    "resume_review": gap_id if stage == "review" else "",
                    "question_count": sum(1 for g in gaps if g.get("status") in ("answered", "resolved", "skipped")),  # ask 가 +1 하므로 '지난 질문 수 + 1' 번이 된다
                    "next_action": "ask",
                    "last_intent": "reopen",
                },
                as_node="choose_next",
            )
            return self._run(thread_id, None, kb, on_event)

    def finish(self, thread_id: str, kb: Any, on_event: EventHandler | None = None) -> TurnResult:
        return self._resume(thread_id, {"type": "finish"}, kb, on_event)

    def notify_files(self, thread_id: str, kb: Any, names: list[str], on_event: EventHandler | None = None) -> TurnResult:
        return self._resume(thread_id, {"type": "files", "names": list(names)}, kb, on_event)

    def retry(self, thread_id: str, kb: Any, on_event: EventHandler | None = None) -> TurnResult:
        """오류로 멈춘 실행을 마지막 체크포인트부터 다시 시도한다."""
        with self._lock(thread_id):
            if not self._has_state(thread_id):
                raise AgentStateError("다시 시도할 실행 기록이 없습니다.")
            if self.is_waiting(thread_id):
                return TurnResult(snapshot=self.snapshot(thread_id))
            return self._run(thread_id, None, kb, on_event)

    def edit_gantt(self, thread_id: str, name: str, nth: int, months: list[int]) -> dict[str, Any]:
        """화면에서 고친 월별 막대를 계획기간으로 상태에 확정하고, 같은 버전으로 문서를 다시 만든다(Claude 호출 없음)."""
        with self._lock(thread_id):
            if not self._has_state(thread_id):
                raise AgentStateError("아직 분석을 시작하지 않았습니다.")
            config = self._config(thread_id)
            values = self.graph.get_state(config).values or {}
            if not values.get("document"):
                raise AgentStateError("아직 생성된 인수인계서가 없습니다.")
            slots = copy.deepcopy(values.get("slots") or {})
            key = apply_gantt_edit(slots, name, nth, months)
            if key is None:
                raise AgentStateError("고칠 일정 행을 찾지 못했습니다.")
            version = int(values.get("document_version") or 1)
            markdown = render_document(profile=values.get("profile", {}), slots=slots, gaps=values.get("gaps") or [], sources=values.get("sources") or [], version=version)
            self.graph.update_state(config, {"slots": {key: slots[key]}, "document": markdown})
            return {"markdown": markdown, "version": version}

    def update_profile(self, thread_id: str, patch: dict[str, Any]) -> bool:
        """진행 중인 대화의 인계자 정보를 갱신한다(값이 None 이면 그 키를 지운다). 바뀐 것이 있으면 True.

        팀 관리자가 분석을 시작한 뒤 팀 지침·프롬프트를 고쳐도 다음 답변 처리부터 새 내용이 반영되게 백엔드가 매 턴 앞에서 부른다.
        """
        with self._lock(thread_id):
            if not self._has_state(thread_id):
                return False
            config = self._config(thread_id)
            profile = dict((self.graph.get_state(config).values or {}).get("profile") or {})
            changed = False
            for key, value in patch.items():
                if value is None:
                    changed |= profile.pop(key, None) is not None
                elif profile.get(key) != value:
                    profile[key], changed = value, True
            if changed:
                self.graph.update_state(config, {"profile": profile})
            return changed

    def delete(self, thread_id: str) -> None:
        self.checkpointer.delete_thread(thread_id)

    # ------------------------------------------------------------------ 조회
    def is_waiting(self, thread_id: str) -> bool:
        state = self.graph.get_state(self._config(thread_id))
        return bool(state.interrupts) or "wait_user" in (state.next or ())

    def snapshot(self, thread_id: str) -> dict[str, Any]:
        state = self.graph.get_state(self._config(thread_id))
        values = state.values or {}
        slots = {k: {kk: vv for kk, vv in v.items() if kk != "proposed_gaps"} for k, v in (values.get("slots") or {}).items()}
        gaps = values.get("gaps") or []
        return {
            "exists": bool(values),
            "stage": values.get("stage", ""),
            "waiting": bool(state.interrupts) or "wait_user" in (state.next or ()),
            "next": list(state.next or ()),
            "slots": slots,
            "gaps": gaps,
            "open_gap_count": len(open_gaps(gaps)),
            "current_gap_id": values.get("current_gap_id", ""),
            "pending": values.get("pending") or {},
            "question_count": values.get("question_count", 0),
            "document": values.get("document", ""),
            "document_version": values.get("document_version", 0),
            "sources": values.get("sources", []),
            "engine": self.engine.name,
        }

    # ------------------------------------------------------------------ 내부
    def _resume(self, thread_id: str, payload: dict[str, Any], kb: Any, on_event: EventHandler | None) -> TurnResult:
        with self._lock(thread_id):
            if not self._has_state(thread_id):
                raise AgentStateError("아직 분석을 시작하지 않았습니다. 자료를 올리고 분석을 먼저 시작해 주세요.")
            if not self.is_waiting(thread_id):
                raise AgentStateError("이전 작업이 끝나지 않았습니다. 잠시 후 다시 시도하거나 '다시 시도'를 눌러 주세요.")
            return self._run(thread_id, Command(resume=payload), kb, on_event)

    def _run(self, thread_id: str, graph_input: Any, kb: Any, on_event: EventHandler | None) -> TurnResult:
        context = AgentContext(kb=KBAdapter(kb), engine=self.engine, config=self.config)
        events: list[dict[str, Any]] = []
        for event in self.graph.stream(graph_input, self._config(thread_id), context=context, stream_mode="custom"):
            events.append(event)
            if on_event is not None:
                on_event(event)
        return TurnResult(events=events, snapshot=self.snapshot(thread_id))

    def _has_state(self, thread_id: str) -> bool:
        return bool(self.graph.get_state(self._config(thread_id)).values)

    def _config(self, thread_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            # LangGraph 1.2 는 Send 병렬 실행에서 max_concurrency=1 이면 멈출 수 있어 최소 2 로 둔다.
            "max_concurrency": max(2, self.config.max_concurrency),
            "recursion_limit": 100,
        }

    def _lock(self, thread_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(thread_id, threading.Lock())

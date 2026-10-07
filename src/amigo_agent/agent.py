"""HandoverAgent: 백엔드가 쓰는 에이전트 진입점(LangGraph 를 몰라도 쓸 수 있게 감싼다).

    agent = HandoverAgent.with_sqlite("data/agent.sqlite")          # 대화 상태를 SQLite 체크포인트에 영속화
    agent.start(session_id, profile, kb, on_event=save_to_db)       # STAGE 1 분석 → STAGE 2 요약 → 첫 질문
    agent.send_message(session_id, "업체 담당자는 …", kb)              # STAGE 3 답변 처리 → 확인/다음 질문
    agent.notify_files(session_id, kb, ["계약서.pdf"])               # 대화 중 추가 자료 → 재분석
    agent.finish(session_id, kb)                                    # 지금 바로 STAGE 4 문서 생성
    agent.snapshot(session_id)                                      # 단계·슬롯·공백·문서 상태 조회

on_event 콜백은 진행 중 발생하는 이벤트를 즉시 받는다.
    {"type": "stage", "stage": "analyzing", "number": 1, "label": "자료 분석"}
    {"type": "progress", "message": "'협업 관계' 분석 중", ...}
    {"type": "slot", "key": "contacts", "slot": {...}}
    {"type": "message", "role": "assistant", "kind": "question", "content": "...", "meta": {...}}
    {"type": "document", "markdown": "...", "version": 1}
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Callable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .config import AgentConfig
from .engine import Engine, make_engine
from .graph import build_graph
from .kb import KBAdapter
from .nodes import AgentContext
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

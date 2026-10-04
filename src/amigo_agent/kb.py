"""지식베이스 연결부.

에이전트는 RAG 모듈(amigo_rag.KnowledgeBase)을 직접 import 하지 않고 아래 프로토콜만 기대한다.
그래서 RAG 없이도(테스트용 가짜 지식베이스로) 에이전트를 개발·테스트할 수 있다.

    kb.search(query, top_k=5) -> [결과]   # 결과는 객체/딕셔너리 모두 허용
        결과에는 text, metadata, citation(또는 metadata 로 계산), chunk_id, score 가 있으면 된다
    kb.add_text(text, source_name=..., source_type=..., metadata=...)   # 선택
    kb.list_sources() -> [자료]                                          # 선택
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .state import Citation


class KnowledgeBaseLike(Protocol):
    def search(self, query: str, top_k: int = 5, **kwargs) -> list[Any]: ...


@dataclass
class Evidence:
    chunk_id: str
    text: str
    citation: str
    source_id: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_citation(self, hint: str = "") -> Citation:
        return {
            "source_id": self.source_id,
            "label": self.citation,
            "chunk_id": self.chunk_id,
            "quote": best_quote(self.text, hint),
        }


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _label_from_meta(meta: dict[str, Any]) -> str:
    try:
        from amigo_rag import format_citation  # RAG 가 설치되어 있으면 같은 규칙 사용

        return format_citation(meta)
    except Exception:
        name = meta.get("file_name") or meta.get("source_name") or "자료"
        page = meta.get("page")
        return f"{name} p.{page}" if page else str(name)


class KBAdapter:
    """여러 형태의 지식베이스 결과를 Evidence 로 통일하고, 실패해도 대화가 끊기지 않게 감싼다."""

    def __init__(self, kb: Any | None) -> None:
        self.kb = kb

    def search(self, query: str, top_k: int = 5) -> list[Evidence]:
        if self.kb is None or not query.strip():
            return []
        raw = self.kb.search(query, top_k=top_k)
        results: list[Evidence] = []
        for r in raw or []:
            meta = dict(_get(r, "metadata", {}) or {})
            citation = _get(r, "citation") or _label_from_meta(meta)
            results.append(
                Evidence(
                    chunk_id=str(_get(r, "chunk_id", "") or _get(r, "id", "")),
                    text=str(_get(r, "text", "") or ""),
                    citation=str(citation),
                    source_id=str(meta.get("source_id", "")),
                    score=float(_get(r, "score", 0.0) or 0.0),
                    metadata=meta,
                )
            )
        return results

    def add_text(self, text: str, *, source_name: str, source_type: str = "chat", metadata: dict | None = None) -> str | None:
        adder = getattr(self.kb, "add_text", None)
        if adder is None:
            return None
        result = adder(text, source_name=source_name, source_type=source_type, metadata=metadata or {})
        return str(_get(result, "source_id", "") or "") or None

    def list_sources(self) -> list[dict[str, Any]]:
        lister = getattr(self.kb, "list_sources", None)
        if lister is None:
            return []
        out = []
        for s in lister() or []:
            out.append(
                {
                    "source_id": str(_get(s, "source_id", "")),
                    "name": str(_get(s, "source_name", "") or _get(s, "name", "")),
                    "type": str(_get(s, "source_type", "") or _get(s, "type", "")),
                    "chunk_count": int(_get(s, "chunk_count", 0) or 0),
                }
            )
        return out


class EvidenceRegistry:
    """LLM 에게 보여준 근거에 [E1], [E2] … 번호를 붙이고, 번호로 다시 찾을 수 있게 보관한다."""

    def __init__(self) -> None:
        self._by_id: dict[str, Evidence] = {}
        self._by_chunk: dict[str, str] = {}

    def register(self, evidence: Evidence) -> str:
        key = evidence.chunk_id or f"{evidence.citation}:{hash(evidence.text)}"
        if key in self._by_chunk:
            return self._by_chunk[key]
        eid = f"E{len(self._by_id) + 1}"
        self._by_id[eid] = evidence
        self._by_chunk[key] = eid
        return eid

    def get(self, eid: str) -> Evidence | None:
        return self._by_id.get(eid.strip().strip("[]"))

    def citations(self, ids: list[str], hint: str = "") -> list[Citation]:
        out: list[Citation] = []
        seen = set()
        for eid in ids:
            ev = self.get(eid)
            if ev and ev.chunk_id not in seen:
                seen.add(ev.chunk_id)
                out.append(ev.to_citation(hint))
        return out

    def format(self, evidences: list[Evidence], max_chars: int = 1200) -> str:
        """근거 목록을 프롬프트용 텍스트로 만든다."""
        lines = []
        for ev in evidences:
            eid = self.register(ev)
            section = ev.metadata.get("section")
            header = f"[{eid}] {ev.citation}" + (f" › {section}" if section else "")
            body = ev.text if len(ev.text) <= max_chars else ev.text[:max_chars] + " …"
            lines.append(f"{header}\n{body}")
        return "\n\n".join(lines)

    def __len__(self) -> int:
        return len(self._by_id)


_SENT_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")


def best_quote(text: str, hint: str = "", limit: int = 120) -> str:
    """근거 청크에서 항목과 가장 관련 있는 문장(또는 표 행)을 골라 짧게 인용한다."""
    pieces = [p.strip(" |-#") for p in _SENT_SPLIT.split(text or "") if p.strip(" |-#")]
    pieces = [p for p in pieces if not set(p) <= set("-| ")]
    if not pieces:
        return ""
    hint_tokens = set(_tokens(hint))
    best = max(pieces, key=lambda p: (len(hint_tokens & set(_tokens(p))), -abs(len(p) - 60))) if hint_tokens else pieces[0]
    best = re.sub(r"\s*\|\s*", " · ", best).strip(" ·")
    return best if len(best) <= limit else best[: limit - 1] + "…"


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[가-힣]+|[A-Za-z0-9]+", (text or "").lower())
    grams = []
    for w in words:
        grams.append(w)
        if re.match(r"^[가-힣]{2,}$", w):
            grams.extend(w[i : i + 2] for i in range(len(w) - 1))
    return grams

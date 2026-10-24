"""인수인계 챗봇: 인계자가 올린 자료(파일·메일·링크·가져온 대화/문서)와 인계자 답변, 그리고 완성된 인수인계서를 근거로 질문에 답한다.

- 근거는 두 곳에서 모은다: ① 업무(세션)별 지식베이스 검색 ② 인수인계서(Markdown) 중 질문과 관련 있는 장. 인수인계서는 근거 중 하나일 뿐이다.
- 근거마다 [E번호]를 붙여 Claude 에 주고, Claude 는 실제로 쓴 번호를 evidence_ids 로 돌려준다. 화면은 이것으로 출처를 보여 준다.
- Claude 가 연결되지 않은 엔진(오프라인·가상 응답)은 관련 근거를 그대로 보여 주는 방식으로 답한다.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from html import unescape
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .config import AgentConfig
from .kb import Evidence, KBAdapter

logger = logging.getLogger(__name__)

FULL_DOCUMENT_CHARS = 16_000  # 업무가 하나이고 인수인계서가 이보다 짧으면 전문을 근거로 준다(길면 관련 장만)
DOC_SECTIONS = 5  # 인수인계서에서 고르는 관련 장 수
DOC_SECTION_CHARS = 3_000
KB_HITS = 10  # 지식베이스에서 가져오는 조각 수(모든 업무 합계)
KB_HITS_PER_SCOPE = 8
KB_HITS_PER_SCOPE_MULTI = 4
KB_CHUNK_CHARS = 1_500
MIN_KB_SCORE = 0.2  # 지식베이스가 '관련 없음'으로 판정한 조각도 이 점수 이상이면 후보로 둔다
MAX_HISTORY_TURNS = 8
OFFLINE_BLOCKS = 4
NOT_FOUND = "올려 주신 자료와 인수인계서에서 관련 내용을 찾지 못했어요. 자료에 없는 내용이라 인계자에게 직접 확인해 주세요."
NO_MATERIALS = "질문할 수 있는 자료가 아직 없어요. 자료를 올려 분석하거나 인수인계서를 완성한 뒤, 또는 인수인계서를 전달받은 뒤에 물어봐 주세요."

KIND_LABELS = {"document": "인수인계서", "answer": "인계자 답변", "mail": "메일", "link": "링크", "file": "파일", "note": "메모"}

QA_SYSTEM = """\
당신은 업무 인수인계를 돕는 도우미입니다. 인수자가 묻는 질문에, 아래 [E번호] 근거에 적힌 내용만으로 답합니다.
근거는 인계자가 올린 자료(파일·메일·링크)의 발췌, 인계자가 인터뷰에서 직접 한 답변, 그리고 완성된 인수인계서의 해당 부분입니다.

원칙
- 근거에 적힌 사실만 답합니다. 근거에 없는 내용을 짐작하거나 일반 상식으로 채우지 않습니다.
- 근거에 답이 없으면 없다고 솔직히 말하고, 근거에 나온 담당자·연락처가 있으면 확인할 곳으로 알려 줍니다. 이때 grounded 는 false 입니다.
- 이름·연락처·날짜·금액·주기·절차 순서는 근거의 표현 그대로 옮깁니다. 표에 있는 값도 빠뜨리지 않습니다.
- 근거끼리 내용이 다르면 하나로 뭉개지 말고 어느 자료에 무엇이 적혀 있는지 함께 알려 줍니다. 어느 쪽이 맞는지 정해야 한다면 인계자 답변 > 인수인계서 > 날짜가 더 최근인 공식 문서 > 회의록·메일 > 날짜 없는 참고자료 순으로 보고, 그 이유를 한 줄로 밝힙니다.
- 인수인계서는 올린 자료를 정리한 결과입니다. 같은 내용이 원본 자료(메일·파일·링크)나 인계자 답변에도 있으면 그것도 근거로 쓰고 evidence_ids 에 함께 적습니다. 인수인계서와 원본이 다르면 다르다고 알려 줍니다.
- 업무가 여러 개인 근거에는 머리글에 업무 이름이 붙어 있습니다. 어느 업무의 내용인지 구분해서 답합니다.
- 답변 글에는 [E3] 같은 근거 번호를 쓰지 않습니다. 대신 '유지보수 계약서에 따르면', '인계자 답변에 따르면'처럼 자료 이름을 자연스럽게 언급합니다. 인수인계서 안의 [1], [7] 같은 번호 표기도 옮기지 않습니다.
- 실제로 답에 쓴 근거 번호만 evidence_ids 에 적습니다(근거 없이 답했으면 빈 목록).
- 비밀번호·토큰 같은 민감 정보는 근거에 있더라도 전달하지 않고 담당자에게 확인하라고 안내합니다.
- 한국어 존댓말로, 필요한 만큼만 간결하게 씁니다. 목록이나 표가 읽기 쉬우면 Markdown 으로 씁니다.
- 질문이 업무 인수인계와 관계없는 일반 대화면 인수인계 자료에 대해 물어봐 달라고 짧게 안내합니다. grounded 는 false 입니다.
"""


class QAOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(description="질문에 대한 답(Markdown, 한국어 존댓말). 근거 번호는 쓰지 않는다")
    grounded: bool = Field(description="근거에 적힌 내용으로 답했으면 true, 근거에 없어서 답하지 못했거나 관계없는 질문이면 false")
    evidence_ids: list[str] = Field(description="답에 실제로 쓴 근거 번호 목록(예: ['E1', 'E4']). 근거가 없으면 빈 목록")


@dataclass
class QAScope:
    """질문할 수 있는 업무 하나: 지식베이스(올린 자료·인계자 답변)와 인수인계서. 둘 중 하나만 있어도 된다."""

    label: str  # 사람이 읽는 이름(예: '기관 홈페이지 운영 · 인계자 김민수')
    markdown: str = ""  # 인수인계서(없으면 빈 문자열)
    version: int = 0
    kb: Any | None = None  # search() 가 있는 지식베이스(없으면 인수인계서만)


@dataclass
class QAEvidence:
    scope: int  # scopes 안의 위치
    kind: str  # document | answer | mail | link | file | note
    label: str  # 출처 표기(파일명 p.3, 메일 제목, 인수인계서 v4 › 장 제목 …)
    text: str
    date: str = ""  # YYYY-MM-DD(알 수 있을 때)
    score: float = 0.0
    eid: str = ""
    source_id: str = ""  # 올린 자료의 ID(지식베이스 조각일 때). 화면이 원본 파일을 내려받는 데 쓴다


@dataclass
class Section:
    doc: int  # scopes 안의 위치
    heading: str
    text: str
    order: int = 0


_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.M)
_WORD = re.compile(r"[0-9A-Za-z]+|[가-힣]+")
_TABLE = re.compile(r"<table[^>]*>.*?</table>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<(t[hd])([^>]*)>(.*?)</\1>", re.S | re.I)
_MONTH = re.compile(r"^(\d{1,2})월$")
_COLSPAN = re.compile(r'colspan="(\d+)"', re.I)
_ROWSPAN = re.compile(r'rowspan="(\d+)"', re.I)


def _cell_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", " / ", html, flags=re.I)
    return " ".join(unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ").split())


def _span(attrs: str) -> int:
    m = _COLSPAN.search(attrs)
    return int(m.group(1)) if m else 1


def _rowspan(attrs: str) -> int:
    m = _ROWSPAN.search(attrs)
    return int(m.group(1)) if m else 1


def _month_ranges(months: list[int]) -> str:
    """[1, 4, 10, 11] → '1월, 4월, 10~11월'"""
    runs: list[list[int]] = []
    for m in sorted(set(months)):
        if runs and m == runs[-1][-1] + 1:
            runs[-1].append(m)
        else:
            runs.append([m])
    return ", ".join(f"{r[0]}월" if len(r) == 1 else f"{r[0]}~{r[-1]}월" for r in runs)


def _place(rows: list[list[tuple[str, str]]]) -> list[list[tuple[int, str, str]]]:
    """표의 칸마다 실제 열 위치를 구한다. 위 줄에서 내려온 rowspan 칸과 colspan 을 모두 반영한다. (열 위치, 속성, 글)"""
    pending: dict[int, int] = {}  # 열 → 아래 줄에서도 차지할 줄 수
    placed: list[list[tuple[int, str, str]]] = []
    for row in rows:
        carried, added, col, cells = dict(pending), {}, 0, []
        for attrs, text in row:
            while col in carried:
                col += 1
            span = _span(attrs)
            cells.append((col, attrs, text))
            if _rowspan(attrs) > 1:
                added.update({c: _rowspan(attrs) - 1 for c in range(col, col + span)})
            col += span
        pending = {c: n - 1 for c, n in carried.items() if n > 1} | added
        placed.append(cells)
    return placed


def _table_to_text(html: str) -> str:
    rows = [[(attrs, _cell_text(body)) for _, attrs, body in _CELL.findall(r)] for r in _ROW.findall(html)]
    rows = [r for r in rows if r]
    if not rows:
        return ""
    placed = _place(rows)
    # 월별 일정표: '1월 … 12월' 머리글이 있는 줄을 찾는다(머리글이 한 줄이든 두 줄이든 된다)
    header = next((i for i, cells in enumerate(placed) if sum(1 for _, _, text in cells if _MONTH.match(text)) >= 6), None)
    if header is not None:
        months = {col: int(m.group(1)) for col, _, text in placed[header] if (m := _MONTH.match(text))}
        lines = ["월별 일정표(진행하는 달):"]
        for cells in placed[header + 1 :]:
            on, note = [], ""
            for col, attrs, text in cells:
                if 'class="bar"' in attrs.replace("'", '"'):
                    on += [months[c] for c in range(col, col + _span(attrs)) if c in months]
                elif col > 0 and text:
                    note = text
            lines.append(f"- {cells[0][2]}: {_month_ranges(on) or '진행하는 달 없음'}" + (f" ({note})" if note else ""))
        return "\n".join(lines)
    width = max(len(r) for r in rows)
    grid = [[text for _, text in r] + [""] * (width - len(r)) for r in rows]  # colspan 은 한 칸으로 본다
    out = ["| " + " | ".join(grid[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in grid[1:]]
    return "\n".join(out)


def plain_tables(markdown: str) -> str:
    """문서 안의 HTML 표(월별 일정표 등)를 Markdown 표·글로 바꾼다. 막대 색칠은 글로 읽을 수 없기 때문이다."""
    return _TABLE.sub(lambda m: "\n" + _table_to_text(m.group(0)) + "\n", markdown)


def split_sections(doc: int, markdown: str) -> list[Section]:
    """Markdown 을 제목(#~###) 단위로 나눈다. 첫 제목 앞의 글은 '머리말' 장이 된다."""
    matches = list(_HEADING.finditer(markdown))
    if not matches:
        text = markdown.strip()
        return [Section(doc, "", text)] if text else []
    sections: list[Section] = []
    head = markdown[: matches[0].start()].strip()
    if head:
        sections.append(Section(doc, "", head))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        text = markdown[m.start():end].strip()
        if text:
            sections.append(Section(doc, m.group(2), text))
    for n, s in enumerate(sections):
        s.order = n
    return sections


# ---------------------------------------------------------------- 인수인계서 장 고르기
def _terms(text: str) -> set[str]:
    """검색용 말마디. 한글은 조사가 붙어도 맞도록 두 글자씩 끊고, 영문·숫자는 낱말 그대로 쓴다."""
    out: set[str] = set()
    for word in _WORD.findall(text.lower()):
        if word.isascii():
            if len(word) >= 2:
                out.add(word)
        elif len(word) == 1:
            continue
        else:
            out.update(word[i : i + 2] for i in range(len(word) - 1))
    return out


def rank_sections(sections: list[Section], query: str) -> list[tuple[float, Section]]:
    """질문 말마디가 많이 들어 있고, 다른 장에는 드문 말마디일수록 높은 점수(제목에 있으면 가산)."""
    q = _terms(query)
    if not q or not sections:
        return [(0.0, s) for s in sections]
    bodies = [_terms(s.text) for s in sections]
    df = {t: sum(1 for b in bodies if t in b) for t in q}
    ranked = []
    for s, body in zip(sections, bodies, strict=True):
        heading = _terms(s.heading)
        score = sum(math.log(1 + len(sections) / df[t]) * (2.0 if t in heading else 1.0) for t in q if t in body)
        ranked.append((score / math.sqrt(1 + len(s.text) / 800), s))
    return sorted(ranked, key=lambda pair: pair[0], reverse=True)


def retrieval_query(question: str, history: list[dict[str, str]]) -> str:
    """꼬리 질문('그 담당자 연락처는?')도 찾을 수 있게 앞선 사용자 질문 2개를 함께 쓴다."""
    previous = [h["content"] for h in history if h.get("role") == "user"][-2:]
    return " ".join([*previous, question])


# ---------------------------------------------------------------- 근거 모으기
def _kind_of(meta: dict[str, Any]) -> str:
    source_type = str(meta.get("source_type") or "")
    file_type = str(meta.get("file_type") or source_type)
    if source_type == "chat":
        return "answer"
    if source_type == "note":
        return "note"
    if file_type in ("eml", "msg"):
        return "mail"
    if meta.get("url") and source_type not in ("pdf", "docx", "pptx", "xlsx", "hwp", "hwpx", "txt", "md", "csv"):
        return "link"
    return "file"


def _kb_evidence(index: int, ev: Evidence) -> QAEvidence:
    ts = str(ev.priority().get("ts") or "")
    return QAEvidence(index, _kind_of(ev.metadata), ev.citation, ev.text[:KB_CHUNK_CHARS], ts[:10], ev.score, source_id=ev.source_id)


def gather_kb_evidence(scopes: list[QAScope], query: str) -> list[QAEvidence]:
    """업무별 지식베이스에서 질문과 관련 있는 조각을 찾아 점수순으로 돌려준다(관련 없다고 판정된 조각은 뺀다)."""
    per_scope = KB_HITS_PER_SCOPE if len(scopes) == 1 else KB_HITS_PER_SCOPE_MULTI
    hits: list[QAEvidence] = []
    for i, scope in enumerate(scopes):
        if scope.kb is None:
            continue
        try:
            results = KBAdapter(scope.kb).search(query, top_k=per_scope)
        except Exception as exc:  # 한 업무의 지식베이스가 깨져도 나머지로 답한다
            logger.warning("qa: knowledge base search failed for %s: %s", scope.label, exc)
            continue
        # 지식베이스의 relevant 판정은 낱말이 그대로 겹쳐야 참이라 구어체 질문('~관련해서 뭘 요청했어요?')에서는 정답 조각도 False 일 수 있다.
        # 그래서 판정이 거짓이어도 점수가 어느 정도면 넘기고(정말 관련 있는지는 Claude 가 가린다), 점수가 낮은 조각만 뺀다.
        hits += [_kb_evidence(i, ev) for ev in results if ev.relevant is not False or ev.score >= MIN_KB_SCORE]
    return sorted(hits, key=lambda e: e.score, reverse=True)[:KB_HITS]


def gather_document_evidence(scopes: list[QAScope], query: str, *, whole: bool, min_overlap: int = 0) -> list[QAEvidence]:
    """인수인계서에서 근거를 만든다. whole 이고 업무가 하나이며 짧으면 전문, 아니면 질문과 관련 있는 장만 고른다."""
    docs = [(i, plain_tables(s.markdown)) for i, s in enumerate(scopes) if s.markdown.strip()]
    if not docs:
        return []
    if whole and len(docs) == 1 and len(docs[0][1]) <= FULL_DOCUMENT_CHARS:
        i, text = docs[0]
        return [QAEvidence(i, "document", f"인수인계서 v{scopes[i].version}", text)]
    sections = [s for i, text in docs for s in split_sections(i, text) if "근거 자료" not in s.heading]  # 예전 형식 문서의 '근거 자료' 장은 출처 이름 목록일 뿐이라 근거로 쓰지 않는다(지금 양식에는 이 장이 없다)
    q = _terms(query)
    need = min(min_overlap, len(q))
    picked = [(score, s) for score, s in rank_sections(sections, query) if score > 0 and len(q & _terms(s.text)) >= need][:DOC_SECTIONS]
    return [
        QAEvidence(s.doc, "document", f"인수인계서 v{scopes[s.doc].version}" + (f" › {s.heading}" if s.heading else ""), s.text[:DOC_SECTION_CHARS], score=score)
        for score, s in picked
    ]


def _number(evidences: list[QAEvidence]) -> list[QAEvidence]:
    for n, ev in enumerate(evidences, 1):
        ev.eid = f"E{n}"
    return evidences


def build_user_message(scopes: list[QAScope], evidences: list[QAEvidence], history: list[dict[str, str]], question: str) -> str:
    multi = len(scopes) > 1
    blocks = []
    for ev in evidences:
        head = " · ".join(x for x in (KIND_LABELS[ev.kind], f"업무: {scopes[ev.scope].label}" if multi else "", ev.label, ev.date) if x)
        blocks.append(f"[{ev.eid}] {head}\n{ev.text}")
    lines = [f"{'사용자' if h.get('role') == 'user' else '도우미'}: {h['content']}" for h in history[-MAX_HISTORY_TURNS:]]
    return "----- 근거 -----\n" + "\n\n=====\n\n".join(blocks) + f"\n\n----- 대화 기록 -----\n{chr(10).join(lines) or '(없음)'}\n\n----- 지금 질문 -----\n{question}"


def offline_answer(scopes: list[QAScope], evidences: list[QAEvidence]) -> tuple[QAOutput, list[QAEvidence]]:
    """Claude 없이 답한다: 질문과 가장 관련 있는 근거를 출처와 함께 그대로 보여 준다."""
    shown = evidences[:OFFLINE_BLOCKS]
    if not shown:
        return QAOutput(answer=NOT_FOUND, grounded=False, evidence_ids=[]), []
    multi = len(scopes) > 1
    blocks = []
    for ev in shown:
        head = " · ".join(x for x in (KIND_LABELS[ev.kind], scopes[ev.scope].label if multi else "", ev.label) if x)
        blocks.append(f"**{head}**\n\n{ev.text[:1200]}")
    intro = "AI 엔진이 연결되어 있지 않아, 질문과 가장 관련 있어 보이는 부분을 자료에서 그대로 가져왔어요.\n\n"
    return QAOutput(answer=intro + "\n\n---\n\n".join(blocks), grounded=True, evidence_ids=[ev.eid for ev in shown]), shown


def answer_from_materials(
    llm: Any | None,
    config: AgentConfig,
    question: str,
    history: list[dict[str, str]],
    scopes: list[QAScope],
) -> dict[str, Any]:
    """질문에 답한다. llm 이 ClaudeLLM 이면 Claude 로, None 이면 관련 근거를 그대로 보여 준다.

    돌려주는 값: answer(Markdown) · grounded(자료에 근거가 있는지) · sources(근거로 쓴 자료 [{scope, kind, label, source_id}], 같은 자료는 한 번만) · searched(찾은 근거 수)
    """
    if not scopes:
        return {"answer": NO_MATERIALS, "grounded": False, "sources": [], "searched": 0}
    query = retrieval_query(question, history)
    kb_evidence = gather_kb_evidence(scopes, query)
    if llm is None:
        # 전문을 주지 않고 장 단위로 고른다. 질문 말마디가 2개 이상 겹치는 장만 쓴다(낱말 하나만 겹쳐서 엉뚱한 답이 나오지 않게)
        evidences = _number(gather_document_evidence(scopes, query, whole=False, min_overlap=2)[:2] + kb_evidence)
        out, used = offline_answer(scopes, evidences)
    else:
        evidences = _number(gather_document_evidence(scopes, query, whole=True) + kb_evidence)
        if not evidences:
            return {"answer": NOT_FOUND, "grounded": False, "sources": [], "searched": 0}
        out = llm.structured(
            system=QA_SYSTEM,
            user=build_user_message(scopes, evidences, history, question),
            output_model=QAOutput,
            effort=config.effort_chat,
            max_tokens=2000,
        )
        by_id = {ev.eid: ev for ev in evidences}
        used = [by_id[i.strip().strip("[]")] for i in out.evidence_ids if i.strip().strip("[]") in by_id]
        if out.grounded and not used:
            used = evidences[:1]
    sources, seen = [], set()
    for ev in used:
        key = (ev.scope, ev.kind, ev.label)
        if key not in seen:
            seen.add(key)
            sources.append({"scope": ev.scope, "kind": ev.kind, "label": ev.label, "source_id": ev.source_id})
    return {"answer": out.answer.strip() or NOT_FOUND, "grounded": out.grounded, "sources": sources, "searched": len(evidences)}

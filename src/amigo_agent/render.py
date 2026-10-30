"""최종 인수인계서(Markdown) 렌더러.

문서 전체를 코드로 결정적으로 만든다(LLM 은 쓰지 않는다). 표·항목 값이 바뀌거나 사라질 위험이 없다.
장 구성은 노션 '인수인계서 형식' 템플릿(template.py)을 그대로 따르고, 양식에 없는 장은 싣지 않는다:
확인 필요 사항(남은 질문)은 화면 오른쪽 패널에 따로 보여 준다. 근거 자료 장도 없고, 근거는 그 내용이 적힌 칸의
글 바로 뒤에 [번호] 로만 단다(화면은 번호에 마우스를 올리면 출처를 띄운다. 번호 = GET /citations 의 number,
내려받는 PDF·Word 에서는 번호를 뺀다).
"""

from __future__ import annotations

import html
import re
from datetime import date
from typing import Any

from .periods import plan_span
from .relevance import bigrams
from .state import Gap, SlotState, item_label, normalize_title
from .dept_info import clean_dept_info
from .template import (
    MODE_LABELS,
    OFFICE_DEPT_TITLE,
    OFFICE_FIRST_TITLE,
    OFFICE_ISSUES,
    OFFICE_PENDING,
    OFFICE_PLANS,
    OFFICE_SLOT,
    PARTNER_SCOPES,
    SLOT_BY_KEY,
    FieldSpec,
    SlotSpec,
    is_office,
    leave_period,
    mode_of,
    profile_plans,
    profile_units,
    slots_for,
)

class _Citations:
    """근거 번호. 장 순서 → 항목 순서 → 항목의 근거 순서로 매기고, 같은 (출처 표기, 조각) 은 같은 번호(BackEnd routers/evidence.py 와 같은 규칙)."""

    def __init__(self) -> None:
        self.numbers: dict[str, int] = {}

    def marks(self, spec: SlotSpec, item: dict, fields: dict) -> dict[str, str]:
        """{필드 키: ' [1] [3]'}. 근거 문장과 가장 많이 겹치는 필드 뒤에 번호를 달고, 어느 필드와도 겹치지 않으면 항목 이름 뒤에 단다."""
        if not spec.evidence:
            return {}
        placed: dict[str, list[int]] = {}
        grams = {f.key: bigrams(str(fields.get(f.key) or "")) for f in spec.shown}
        for c in item.get("citations") or []:
            key = f"{c.get('label')}|{c.get('chunk_id') or c.get('quote', '')[:40]}"
            number = self.numbers.setdefault(key, len(self.numbers) + 1)
            quote = bigrams(str(c.get("quote") or ""))
            overlap = {k: len(g & quote) for k, g in grams.items()}
            best = max(overlap, key=lambda k: overlap[k]) if overlap else spec.title_field
            target = best if overlap.get(best, 0) >= 2 else spec.title_field
            if number not in placed.setdefault(target, []):
                placed[target].append(number)
        return {k: "".join(f" [{n}]" for n in sorted(nums)) for k, nums in placed.items()}


# 모델이 값 안에 남긴 내부 근거 ID('(E10)', '[E1, E3]')는 문서에 싣지 않는다(근거는 [번호] 로 따로 단다)
EVIDENCE_ID_RE = re.compile(r"\s*[\(\[]E\d+(?:\s*[,·/]\s*E\d+)*[\)\]]")


# 지라에서 가져온 자료의 이슈 키('[LGL-3]', '(AMG-12)')가 문장에 딸려 오면 문서에서는 뺀다(출처는 [번호] 로 따로 단다).
# 국제 표준·규격 번호('[ISO-27001]', '[KS-1234]')는 내용이므로 남긴다.
JIRA_KEY_RE = re.compile(r"[\[(](?!(?:ISO|IEC|KS|RFC|NIST|ITU|PCI|SP|GS|TTA)-)[A-Z][A-Z0-9]{1,9}-\d{1,6}[\])][ \t]?")


def strip_jira_keys(text: str) -> str:
    if not JIRA_KEY_RE.search(text):
        return text
    def drop(match: re.Match) -> str:
        # 앞 글자에 붙어 있던 키('개발(AMG-12) 완료')는 뒤 공백을 남겨 단어가 붙지 않게 한다
        before = match.string[match.start() - 1] if match.start() else ""
        return match.group(0)[-1] if match.group(0)[-1] in " \t" and before and not before.isspace() else ""

    text = JIRA_KEY_RE.sub(drop, text)
    text = re.sub(r"[ \t]+(?=[.,;:)]|$)", "", text, flags=re.M)  # 키가 문장 끝에 있었다면 남는 앞 공백을 뗀다
    return re.sub(r"(?<=\S)[ \t]{2,}", " ", text)


def _clean(value: Any) -> str:
    return strip_jira_keys(EVIDENCE_ID_RE.sub("", str(value or ""))).strip()


def _tilde(text: str) -> str:
    """마크다운 글의 물결표를 이스케이프한다. '12월~1월, 11월~2월'처럼 ~ 가 두 번 나오면 화면(GFM)이 그 사이를 취소선으로 그린다."""
    return re.sub(r"(?<!\\)~", r"\~", text)


LINE_SEP_RE = re.compile(r"\s*\n+\s*|\s+/\s+")  # 칸 안에서 문장을 나누는 줄바꿈과 ' / '(모델이 여러 내용을 잇는 표시)


NUMBERED_RE = re.compile(r"^(\d{1,2})[.)]\s+")


def _lines_br(text: str) -> str:
    """칸 안의 여러 문장을 줄바꿈(<br>)으로 나눈다(노션 '추가 요구사항 정의': 새 문장은 '/' 대신 줄바꿈).
    번호 목록의 항목이 하나뿐이면('1. …'만 있으면) 번호를 뗀다(같은 문서 17)."""
    parts = [part for part in LINE_SEP_RE.split(text.replace("\r", "")) if part.strip()]
    numbered = [i for i, part in enumerate(parts) if NUMBERED_RE.match(part)]
    if len(numbered) == 1 and NUMBERED_RE.match(parts[numbered[0]]).group(1) == "1":
        parts[numbered[0]] = NUMBERED_RE.sub("", parts[numbered[0]], count=1)
    return "<br>".join(parts)


def _cell(value: Any) -> str:
    text = _tilde(_clean(value))
    return _lines_br(text.replace("|", "\\|")) or "-"


HANDWRITTEN_BLANK = "ID: ________ / PW: ________"
BAR_COLOR = "#7055C8"  # 월별 일정표의 막대 색(화면은 CSS, Word/PDF 는 이 색으로 칠한다)


def _h(value: Any, br: bool = True) -> str:
    """HTML 표 칸에 넣을 값. 사용자가 쓴 글이 태그로 해석되지 않도록 모두 이스케이프한다. 여러 문장은 줄바꿈(<br>)으로 나눈다.
    월별 일정표의 칸(br=False)은 막대 수정(BackEnd merge_gantt_rows)이 칸 모양을 읽으므로 ' / '로 잇는다."""
    text = html.escape(_clean(value), quote=True)
    if br:
        return _lines_br(text) or "-"
    return text.replace("\r", "").replace("\n", " / ") or "-"


def _md_table(out: list[str], headers: list[str], rows: list[list[str]]) -> None:
    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    out += ["| " + " | ".join(cells) + " |" for cells in rows]
    out.append("")


def _scoped(spec: SlotSpec, items: list[dict]) -> list[tuple[str, list[FieldSpec], list[dict]]]:
    """표를 나눠 싣는 장(협업 대상)이면 [(외부, 열, 행), (내부, 열, 행)], 아니면 [("", 열, 행)]. 행이 없는 쪽은 뺀다(항상 표를 싣는 장은 빈 행으로 둔다)."""
    columns = list(spec.shown)
    if not spec.split_field:
        return [("", columns, items)]
    drop = dict(spec.split_drop)
    out = []
    for scope in PARTNER_SCOPES:
        rows = [it for it in items if scope_of(it.get("fields") or {}) == scope]
        if rows or spec.always_table:
            out.append((scope, [f for f in columns if f.key not in drop.get(scope, ())], rows))
    return out


def _render_table(out: list[str], spec, items: list[dict], cites: _Citations) -> None:
    if not items and not spec.split_field:  # 있을 때만 적는 장(시스템 접근 권한): 직접 적을 수 있게 빈 행을 둔다
        _md_table(out, [f.label for f in spec.shown] + list(spec.handwritten), [["-"] * (len(spec.shown) + len(spec.handwritten))])
        return
    for scope, columns, rows in _scoped(spec, items):
        if scope:
            out += [f"**{scope}**", ""]
        cells = []
        for item in rows:
            fields = dict(item.get("fields") or {})
            fields.setdefault(spec.title_field, item_label(spec, item))
            marks = cites.marks(spec, item, fields)
            cells.append([_cell(fields.get(f.key)) + marks.get(f.key, "") for f in columns] + [HANDWRITTEN_BLANK] * len(spec.handwritten))
        if not cells:  # 항목이 없는 쪽(외부·내부)도 표는 두고 빈 행('-')을 둔다: 직접 수정에서 행을 더하거나 칸을 채울 수 있다
            cells = [["-"] * (len(columns) + len(spec.handwritten))]
        _md_table(out, [f.label for f in columns] + list(spec.handwritten), cells)


def _gantt_cells(filled: list[bool]) -> str:
    """일정표의 달 칸. 이어지는 달은 한 칸으로 합쳐(colspan) 막대가 끊기지 않게 그린다."""
    cells: list[str] = []
    i = 0
    while i < len(filled):
        end = i
        while end + 1 < len(filled) and filled[end + 1] == filled[i]:
            end += 1
        run = end - i + 1
        if filled[i]:
            cells.append(f'<td class="bar" bgcolor="{BAR_COLOR}"' + (f' colspan="{run}"' if run > 1 else "") + ">&nbsp;</td>")
        else:
            cells.extend("<td></td>" for _ in range(run))
        i = end + 1
    return "".join(cells)


def _base_year(profile: dict[str, Any], today: date) -> int:
    """연도 없이 적힌 계획기간('3월~12월')을 어느 해로 볼지: 인계 일자의 해, 없으면 올해."""
    found = re.search(r"(?<!\d)(20\d{2})(?!\d)", str(profile.get("handover_date") or ""))
    return int(found.group(1)) if found else today.year


def _clamp_span(span: set[tuple[int, int]], base_year: int) -> set[tuple[int, int]]:
    """일정표에 그리는 해는 올해(base_year)의 전년·올해·다음 해 3년뿐이다. 그 밖(2024년 이하, 2028년 이상)은 버린다."""
    return {(y, m) for y, m in span if base_year - 1 <= y <= base_year + 1}


def _gantt_window(rows: list[tuple[set[tuple[int, int]], bool]], base_year: int) -> list[tuple[int, int]] | None:
    """올해(base_year) 안에서만 끝나는 표는 None(1~12월 월별 표). 전년·다음 해에 걸치면(2~3년) 분기별로 그릴 (연도, 분기) 칸 목록."""
    spans = set().union(*(_clamp_span(span, base_year) for span, _ in rows)) if rows else set()
    years = {y for y, _ in spans}
    if not years or years == {base_year}:
        return None
    return [(y, q) for y in range(min(years), max(years) + 1) for q in range(1, 5)]


def _render_gantt(out: list[str], spec, items: list[dict], base_year: int) -> None:
    """업무 단위 요약 + 일정 명세표: 계획기간을 막대로 그린다(근거 열 없음).

    올해 안이면 머리글 2행('단위업무명 | 계획기간(12칸 병합) | 비고' + '1월 … 12월')의 월별 표다.
    전년·다음 해에 걸치면(2~3년) 너무 빽빽하지 않게 분기별 표(연도 행 + '1분기 … 4분기')로 그리고, 올해 -1년 ~ +1년 밖의 해는 버린다.
    """
    rows = []
    for item in items:
        fields = dict(item.get("fields") or {})
        fields.setdefault(spec.title_field, item_label(spec, item))
        rows.append((fields, plan_span(fields.get("period"), base_year)))
    window = _gantt_window([span for _, span in rows], base_year)
    name_head, period_head, note_head = _h(spec.label(spec.title_field)), _h(spec.label("period")), _h(spec.label("note"))
    if window is None:
        out += [
            "<table>",
            f'<tr><th rowspan="2">{name_head}</th><th colspan="12">{period_head}</th><th rowspan="2">{note_head}</th></tr>',
            "<tr>" + "".join(f"<th>{month}월</th>" for month in range(1, 13)) + "</tr>",
        ]
    else:
        years: list[list[int]] = []
        for y, _ in window:
            if years and years[-1][0] == y:
                years[-1][1] += 1
            else:
                years.append([y, 1])
        out += [
            "<table>",
            f'<tr><th rowspan="3">{name_head}</th><th colspan="{len(window)}">{period_head}</th><th rowspan="3">{note_head}</th></tr>',
            "<tr>" + "".join(f'<th colspan="{n}">{y}년</th>' for y, n in years) + "</tr>",
            "<tr>" + "".join(f"<th>{q}분기</th>" for _, q in window) + "</tr>",
        ]
    for fields, (span, always) in rows:
        if window is None:
            months = {m for y, m in _clamp_span(span, base_year)} if span else set()
            filled = [always or m in months for m in range(1, 13)]
        else:
            quarters = {(y, (m - 1) // 3 + 1) for y, m in _clamp_span(span, base_year)}
            filled = [always or cell in quarters for cell in window]
        note = str(fields.get("note") or "").strip()
        period = str(fields.get("period") or "").strip()
        if period and not any(filled):  # 읽을 수 없거나 그리는 해 밖의 계획기간은 원문을 마지막 열(비고)에 남긴다
            note = f"계획기간: {period}" + (f" · {note}" if note else "")
        out.append(f"<tr><td>{_h(fields.get(spec.title_field), br=False)}</td>{_gantt_cells(filled)}<td>{_h(note, br=False)}</td></tr>")
    out += ["</table>", ""]


def same_name(a: str, b: str) -> bool:
    """두 이름이 같은 업무·프로젝트를 가리키는지(공백·기호를 빼고 같거나, 4글자 이상이 서로 포함)."""
    x, y = normalize_title(a), normalize_title(b)
    return bool(x and y) and (x == y or (min(len(x), len(y)) >= 4 and (x in y or y in x)))


EXTERNAL_RE = re.compile(r"\(주\)|㈜|주식회사|은행|공사|공단|협회|재단|진흥원|연구원|결제원|기관|업체|회사|솔루션|corp|inc|ltd", re.I)


def scope_of(fields: dict[str, Any], profile: dict[str, Any] | None = None) -> str:
    """협업 대상이 우리 기관 소속(내부)인지 다른 기관·업체(외부)인지. 분석 결과의 구분 값을 먼저 따르고, 없으면 소속 이름으로 정한다."""
    raw = str(fields.get("scope") or "")
    if "외부" in raw:
        return "외부"
    if "내부" in raw:
        return "내부"
    return "외부" if EXTERNAL_RE.search(f"{fields.get('org') or ''} {fields.get('name') or ''}") else "내부"


def _embedded_rows(child: SlotSpec, rows: list[dict], span: int) -> list[str]:
    """카드 안 표(협업관계·추진 경과)의 HTML 행. 나눠 싣는 표는 '외부' '내부' 소제목 행 아래에 각각 머리글과 행을 둔다."""
    groups = _scoped(child, rows) if rows else []
    body: list[str] = []
    for scope, columns, items in groups:
        last = f' colspan="{span - len(columns) + 1}"' if span > len(columns) else ""  # 열 수가 적은 표는 마지막 열을 넓힌다
        cells = lambda values, tag: "".join(f"<{tag}{last if i == len(values) - 1 else ''}>{v}</{tag}>" for i, v in enumerate(values))  # noqa: E731,B023
        if scope:
            body.append(f'<tr><th colspan="{span}">{_h(scope)}</th></tr>')
        body.append("<tr>" + cells([_h(f.label) for f in columns], "th") + "</tr>")
        for c in items:
            cf = dict(c.get("fields") or {})
            cf.setdefault(child.title_field, c.get("title"))
            body.append("<tr>" + cells([_h(cf.get(f.key)) for f in columns], "td") + "</tr>")
    if not body:
        return [f'<tr><th>{_h(child.embed_label)}</th><td colspan="{span}">-</td></tr>']
    body[0] = f'<tr><th rowspan="{len(body)}">{_h(child.embed_label)}</th>' + body[0][len("<tr>"):]
    return body


def _value_or_hint(f: FieldSpec, fields: dict[str, Any], md: bool = False) -> str:
    """칸 값. 비어 있고 안내 문구(placeholder)가 있는 칸은 흐린 안내를 보여 준다(인계자가 직접 적거나 행을 지운다)."""
    if not _clean(fields.get(f.key)) and f.placeholder:
        return f'<span class="doc-hint">{html.escape(f.placeholder)}</span>'
    return _cell(fields.get(f.key)) if md else _h(fields.get(f.key))


def _children_for(child: SlotSpec, child_items: list[dict], name: str) -> list[dict]:
    return [c for c in child_items if same_name(str((c.get("fields") or {}).get(child.embed_link) or ""), name)]


GANADA = "가나다라마바사아자차카타파하"


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _card_number(index: int, ganada: bool) -> str:
    """카드 번호: 1) 2) … 또는 ① ② …(인사발령 '주요 내용'의 세부업무, 20개를 넘으면 (21) 처럼 쓴다)."""
    if not ganada:
        return f"{index})"
    return CIRCLED[index - 1] if index <= len(CIRCLED) else f"({index})"


def _render_cards(out: list[str], spec, items: list[dict], cites: _Citations, children: list[tuple[SlotSpec, list[dict]]], heading: str = "###", ganada: bool = False) -> None:
    """항목마다 '항목 | 내용' 표로 보여 준다.

    다른 장에서 옮겨 온 표(협업관계: 외부 소속 | 이름 | 주요역할 | 연락처 | 비고, 내부 소속 | 이름 | 주요역할 | 비고 / 추진 경과: 시기 | 주요 추진 내용 | 비고)가
    들어가는 장은 그 행의 칸을 나눠 넣으므로 카드 전체를 HTML 표(rowspan/colspan)로 만든다. 옮겨 올 표가 없는 장은 Markdown 표로 만든다.
    내부 값(단위업무명·구분·상태)은 싣지 않는다.
    """
    shown = [f for f in spec.shown if f.key != spec.title_field]
    for index, item in enumerate(items, start=1):
        fields = dict(item.get("fields") or {})
        fields.setdefault(spec.title_field, item_label(spec, item))
        name = fields.get(spec.title_field)
        marks = cites.marks(spec, item, fields)
        out += [f"{heading} {_card_number(index, ganada)} {_cell(name)}{marks.get(spec.title_field, '')}", ""]
        if not children:
            out += ["| 항목 | 내용 |", "|---|---|"]
            out += [f"| {f.label} | {_value_or_hint(f, fields, md=True)}{marks.get(f.key, '')} |" for f in shown]
            out.append("")
            continue
        span = max(len(child_spec.shown) for child_spec, _ in children)
        wide = f' colspan="{span}"'
        rows = ["<table>", f"<tr><th>항목</th><th{wide}>내용</th></tr>"]
        for f in shown:
            rows.append(f"<tr><th>{_h(f.label)}</th><td{wide}>{_value_or_hint(f, fields)}{marks.get(f.key, '')}</td></tr>")
            for child_spec, child_items in children:
                if child_spec.embed_after == f.key:
                    rows += _embedded_rows(child_spec, _children_for(child_spec, child_items, str(name or "")), span)
        rows.append("</table>")
        out += rows + [""]
    # 어느 카드에도 맞지 않는 표의 행은 양식에 없는 표로 따로 싣지 않는다.


def unlinked_children(slots: dict[str, SlotState], active: list[SlotSpec]) -> list[tuple[SlotSpec, dict]]:
    """붙는 장(정기업무·프로젝트)의 어느 항목과도 맞지 않는 카드 안 표의 행. 문서는 이런 행을 양식에 없는 표로 싣지 않는다."""
    out = []
    for child in active:
        if not child.embed_into:
            continue
        parent = SLOT_BY_KEY[child.embed_into]
        names = [str((p.get("fields") or {}).get(parent.title_field) or item_label(parent, p)) for p in (slots.get(child.embed_into) or {}).get("items", [])]
        for item in (slots.get(child.key) or {}).get("items", []):
            value = str((item.get("fields") or {}).get(child.embed_link) or "")
            if not any(same_name(value, name) for name in names):
                out.append((child, item))
    return out


UNIT_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
WORK_KINDS = (("regular", "정기업무"), ("irregular", "비정기 업무"))
OTHER_UNIT = "기타 업무"


def _unit_groups(profile: dict[str, Any], slots: dict[str, SlotState]) -> list[tuple[str, dict[str, list[dict]]]]:
    """세부업무(정기·비정기)를 속한 단위업무별로 묶는다: [(단위업무, {"regular": [...], "irregular": [...]})].

    단위업무 순서는 인계자가 고른 순서다. 세부업무의 단위업무(unit)가 고른 것과 맞지 않으면, 단위업무가 하나뿐이면 그 아래에,
    아니면 '기타 업무' 아래에 둔다. 단위업무를 고르지 않은 예전 세션은 이름 없는 묶음 하나다.
    """
    names = [u["name"] for u in profile_units(profile) if u["name"]]
    groups: dict[str, dict[str, list[dict]]] = {name: {"regular": [], "irregular": []} for name in names}
    for kind, _ in WORK_KINDS:
        for item in (slots.get(kind) or {}).get("items") or []:
            unit = str((item.get("fields") or {}).get("unit") or "").strip()
            target = next((n for n in names if unit and same_name(unit, n)), None)
            if target is None:
                target = names[0] if len(names) == 1 else (OTHER_UNIT if names else "")
            groups.setdefault(target, {"regular": [], "irregular": []})[kind].append(item)
    out = [(name, works) for name, works in groups.items() if name in names or works["regular"] or works["irregular"]]
    return out or [("", {"regular": [], "irregular": []})]  # 세부업무가 하나도 없어도 정기·비정기 칸은 보인다


def _render_main_contents(out: list[str], profile: dict[str, Any], slots: dict[str, SlotState], active: tuple[SlotSpec, ...], cites: _Citations) -> None:
    """인사발령 '2. 주요 내용': A. 단위업무 → 가. 정기업무 / 나. 비정기 업무 → ① 세부업무(카드). (노션 '인수인계서 형식')"""
    groups = _unit_groups(profile, slots)
    lettered = [g for g in groups if g[0]]
    for gi, (unit, works) in enumerate(groups):
        if unit:
            out += [f"### {UNIT_LETTERS[lettered.index((unit, works)) % len(UNIT_LETTERS)]}. {_cell(unit)}", ""]
        for ki, (kind, label) in enumerate(WORK_KINDS, start=1):
            spec = SLOT_BY_KEY[kind]
            if spec not in active:
                continue
            out += [f"#### {GANADA[ki - 1]}. {label}", ""]
            items = works[kind]
            if not items:
                out += ["확인된 내용이 없습니다.", ""]
                continue
            children = [(c, (slots.get(c.key) or {}).get("items") or []) for c in active if c.embed_into == spec.key]
            _render_cards(out, spec, items, cites, children, heading="#####", ganada=True)


def evidence_items(profile: dict[str, Any], slots: dict[str, SlotState]) -> list[tuple[SlotSpec, dict]]:
    """근거 번호를 다는 항목을 문서에 나오는 순서대로 돌려준다(BackEnd 근거 목록 API 가 화면과 같은 번호를 매기는 데 쓴다).

    팀장 양식은 2 현안 → 3 제계약서 → 4 미결 → 5~15 목록, 인사발령은 '주요 내용'의 단위업무별(정기 → 비정기) 순서, 그 밖은 장 순서다.
    근거 번호를 달지 않는 장(일정표)과 다른 장의 카드 안에 들어가는 표는 뺀다.
    """
    def items(key: str) -> list[dict]:
        return list((slots.get(key) or {}).get("items") or [])

    if is_office(profile):
        def office(numbers: tuple[int, ...]) -> list[tuple[SlotSpec, dict]]:
            out = []
            for item in items(OFFICE_SLOT.key):
                found = re.match(r"^(\d+)\.", _clean((item.get("fields") or {}).get("name") or item_label(OFFICE_SLOT, item)))
                if found and int(found.group(1)) in numbers:
                    out.append((OFFICE_SLOT, item))
            return out
        return ([(OFFICE_ISSUES, it) for it in items(OFFICE_ISSUES.key)] + office((3,))
                + [(OFFICE_PENDING, it) for it in items(OFFICE_PENDING.key)] + office(tuple(range(5, 16))))
    out: list[tuple[SlotSpec, dict]] = []
    grouped = False
    for spec in slots_for(profile):
        if not spec.evidence or spec.embed_into:
            continue
        if spec.key in ("regular", "irregular") and mode_of(profile) != "leave":
            if not grouped:
                for _, works in _unit_groups(profile, slots):
                    out += [(SLOT_BY_KEY[kind], it) for kind, _ in WORK_KINDS for it in works[kind]]
                grouped = True
            continue
        out += [(spec, it) for it in items(spec.key)]
    return out


def _render_slot(out: list[str], spec: SlotSpec, slots: dict[str, SlotState], active: tuple[SlotSpec, ...], cites: _Citations, base_year: int) -> None:
    items = (slots.get(spec.key) or {}).get("items") or []
    children = [(c, (slots.get(c.key) or {}).get("items") or []) for c in active if c.embed_into == spec.key]
    if spec.muted_note:
        out += [f'<span class="doc-guide">{html.escape(spec.muted_note)}</span>', ""]
    if spec.layout == "table" and (items or spec.handwritten or spec.always_table):  # 시스템 접근 권한·주요 연락망은 비어 있어도 직접 적을 수 있게 빈 표를 둔다
        _render_table(out, spec, items, cites)
        return
    if not items:
        out += ["확인된 내용이 없습니다.", ""]
        return
    if spec.layout == "gantt":
        _render_gantt(out, spec, items, base_year)
    elif spec.layout == "card":
        _render_cards(out, spec, items, cites, children)
    else:
        _render_table(out, spec, items, cites)


def _bullet_lines(text: Any) -> list[str]:
    """'A / B' 나 여러 줄로 이어 쓴 내용을 개괄식 문장 목록으로 나눈다(앞의 번호·기호는 뗀다)."""
    lines = [line.strip() for line in re.split(r"\s+/\s+|\n+", _clean(text)) if line.strip()]
    return [re.sub(r"^(\d{1,2}\.(?!\d)|[-•□○])\s*", "", line) for line in lines]  # '1. ' 같은 번호만 뗀다('2026.03 착수'의 연도는 남긴다)


def _boxes(text: Any, mark: str = "", symbol: str = "□") -> list[str]:
    """개괄식 문장(□ …). 마지막 문장 끝에 근거 번호를 붙인다. 내용이 없으면 '해당 없음'."""
    lines = _bullet_lines(text)
    if lines:
        lines[-1] += mark
    return [f"{symbol} {_tilde(line)}  " for line in lines] or [f"{symbol} 해당 없음  "]


def _render_office_items(out: list[str], slots: dict[str, SlotState], cites: _Citations, numbers: tuple[int, ...]) -> None:
    """원내 양식의 자료에서만 산출하는 목록(3번, 5~15번): '## 번호. 목록' 과 □ 개괄식 문장. 비고는 AI 참고사항이라 싣지 않는다."""
    for item in (slots.get(OFFICE_SLOT.key) or {}).get("items") or []:
        fields = dict(item.get("fields") or {})
        fields.setdefault("name", item_label(OFFICE_SLOT, item))
        found = re.match(r"^(\d+)\.", _clean(fields["name"]))
        if not found or int(found.group(1)) not in numbers:
            continue
        marks = cites.marks(OFFICE_SLOT, item, fields)
        out += [f"## {_clean(fields['name'])}", ""]
        out += _boxes(fields.get("content"), marks.get("content", "") + marks.get("name", ""))
        out.append("")


def _render_office_issues(out: list[str], slots: dict[str, SlotState], cites: _Citations) -> None:
    """2. 주요 현안사항: 현안마다 가. 개요 / 나. 진행상황 / 다. 고려사항(□ 개괄식). 현안이 하나면 번호를 붙이지 않는다."""
    items = (slots.get(OFFICE_ISSUES.key) or {}).get("items") or []
    out += ["## 2. 주요 현안사항", ""]
    if not items:
        out += ["□ 해당 없음  ", ""]
        return
    for index, item in enumerate(items, start=1):
        fields = dict(item.get("fields") or {})
        name = _clean(fields.get("name") or item_label(OFFICE_ISSUES, item))
        marks = cites.marks(OFFICE_ISSUES, item, fields)
        out += [f"### {name}" if len(items) == 1 else f"### {index}) {name}", ""]
        for label, key in (("가. 개요", "summary"), ("나. 진행상황", "progress"), ("다. 고려사항", "considerations")):
            out += [f"**{label}**", ""] + _boxes(fields.get(key), marks.get(key, "")) + [""]


def _render_office_pending(out: list[str], slots: dict[str, SlotState], cites: _Citations) -> None:
    """4. 주요 미결사항의 경과 및 처리대책: 미결사항마다 □ 이름, 그 아래 ○ 경과 / ○ 처리대책."""
    items = (slots.get(OFFICE_PENDING.key) or {}).get("items") or []
    out += ["## 4. 주요 미결사항의 경과 및 처리대책", ""]
    if not items:
        out += ["□ 해당 없음  ", ""]
        return
    for item in items:
        fields = dict(item.get("fields") or {})
        marks = cites.marks(OFFICE_PENDING, item, fields)
        out.append(f"□ {_tilde(_clean(fields.get('name') or item_label(OFFICE_PENDING, item)))}{marks.get('name', '')}  ")
        for label, key in (("경과", "progress"), ("처리대책", "plan")):
            value = "; ".join(_bullet_lines(fields.get(key))) or "해당 없음"
            out.append(f"  ○ {label}: {_tilde(value)}{marks.get(key, '')}  ")
        out.append("")


def dept_items(profile: dict[str, Any]) -> list[str]:
    """소속 부·팀 인수인계 목록의 서류·물품(부서별 추가자료). BackEnd 가 dept_items 로 주고, 팀장 세션은 dept_notes 에도 들어 있다."""
    items = [" ".join(str(x).split()) for x in (profile or {}).get("dept_items") or [] if str(x).strip()]
    return items or clean_dept_info((profile or {}).get("dept_notes"))["items"]


def _render_dept_uploads(out: list[str], profile: dict[str, Any], heading: str) -> None:
    """부서별 추가자료: 소속 부·팀 인수인계 목록이 있는 팀만 싣는다. AI 가 채우지 않고 인계자가 항목마다 파일을 올린다.
    문서에는 항목별 등록 여부 표로 싣고, 화면은 이 표를 올리는 카드로 그린다(FrontEnd DeptFilesTable)."""
    names = dept_items(profile)
    if not names:
        return
    uploads = (profile or {}).get("dept_uploads") or {}
    out += [
        heading,
        "",
        f"해당 부서의 인수인계에 필요한 추가자료 {len(names)}개 항목이 확인되었습니다. 아래 항목별로 자료를 등록해 주세요.",
        "",
    ]
    rows = []
    for name in names:
        files = [str(f) for f in (uploads.get(name) or []) if str(f).strip()] if isinstance(uploads, dict) else []
        rows.append([_cell(name), _cell("등록됨: " + ", ".join(files)) if files else "자료 등록 필요"])
    _md_table(out, ["추가자료", "등록 여부"], rows)


def _render_office(out: list[str], profile: dict[str, Any], slots: dict[str, SlotState], active: tuple[SlotSpec, ...], cites: _Citations, base_year: int) -> None:
    """관리자(팀장) 인사발령 원내 양식: 1 사업계획 일정표 → 2 현안 → 3 제계약서 → 4 미결 → 5~15 목록 → (해당 팀만) 16 부서·팀별 추가자료."""
    out += [f"## {OFFICE_FIRST_TITLE}", ""]
    _render_slot(out, OFFICE_PLANS, slots, active, cites, base_year)
    _render_office_issues(out, slots, cites)
    _render_office_items(out, slots, cites, (3,))
    _render_office_pending(out, slots, cites)
    _render_office_items(out, slots, cites, tuple(range(5, 16)))
    _render_dept_uploads(out, profile, f"## {OFFICE_DEPT_TITLE}")


def render_document(
    *,
    profile: dict[str, Any],
    slots: dict[str, SlotState],
    gaps: list[Gap],
    sources: list[dict[str, Any]],
    version: int,
    today: date | None = None,
) -> str:
    today = today or date.today()
    base_year = _base_year(profile, today)
    cites = _Citations()
    out: list[str] = ["# 업무 인수인계서", ""]
    active = slots_for(profile)
    visible = [spec for spec in active if not spec.embed_into]  # 다른 장 안에 들어가는 장(협업관계·추진 경과)은 따로 싣지 않는다

    giver = " ".join(x for x in (profile.get("name"), profile.get("position")) if x) or "-"
    units = ", ".join(u["name"] for u in profile_units(profile) if u["name"])
    rows = [
        ("인계자", giver),
        ("소속", profile.get("organization")),
        ("인수자", profile.get("successor")),
        ("휴가 기간", leave_period(profile)),
        ("인계 일자", profile.get("handover_date")),
        ("인계 유형", MODE_LABELS[mode_of(profile)] + (" · 원내 양식(팀장)" if is_office(profile) else "")),
        ("주요 사업계획", ", ".join(profile_plans(profile))) if is_office(profile) else ("단위 업무명", units or profile.get("task_name")),
        ("담당 업무", None if is_office(profile) else profile.get("duties")),
        ("작성", f"{today.isoformat()} · AMIGO 자동 작성 초안 v{version}"),
    ]
    out += ["| 구분 | 내용 |", "|---|---|"]
    out += [f"| {k} | {_cell(v)} |" for k, v in rows if v]
    out.append("")

    if is_office(profile):  # 팀장용 원내 양식: 공문서처럼 목록마다 제목 + 개괄식 문장(□). 1번은 사업계획 일정표
        _render_office(out, profile, slots, active, cites, base_year)
        return "\n".join(out)

    number = 0
    main_done = False
    for index, spec in enumerate(visible):
        if spec.key in ("regular", "irregular") and mode_of(profile) != "leave":
            if not main_done:  # 정기·비정기 업무는 '주요 내용' 한 장에서 단위업무별로 묶는다
                number += 1
                out += [f"## {number}. 주요 내용", ""]
                _render_main_contents(out, profile, slots, active, cites)
                main_done = True
            continue
        if spec.group and index > 0 and visible[index - 1].group == spec.group:
            out += [f"### {spec.title}", ""]  # 같은 장 제목 아래 소제목(기타 자료: 시스템 접근 권한 · 부서별 추가 자료)
            _render_slot(out, spec, slots, active, cites, base_year)
            continue
        number += 1
        if spec.group:
            out += [f"## {number}. {spec.group}", "", f"### {spec.title}", ""]
        else:
            out += [f"## {number}. {spec.title}", ""]
        _render_slot(out, spec, slots, active, cites, base_year)

    return "\n".join(out)

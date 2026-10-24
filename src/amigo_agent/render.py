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

from .periods import plan_months
from .relevance import bigrams
from .state import Gap, SlotState, item_label, normalize_title
from .template import MODE_LABELS, SLOT_BY_KEY, SlotSpec, leave_period, mode_of, slots_for

class _Citations:
    """근거 번호. 장 순서 → 항목 순서 → 항목의 근거 순서로 매기고, 같은 (출처 표기, 조각) 은 같은 번호(BackEnd routers/evidence.py 와 같은 규칙)."""

    def __init__(self) -> None:
        self.numbers: dict[str, int] = {}

    def marks(self, spec: SlotSpec, item: dict, fields: dict) -> dict[str, str]:
        """{필드 키: ' [1] [3]'}. 근거 문장과 가장 많이 겹치는 필드 뒤에 번호를 달고, 어느 필드와도 겹치지 않으면 항목 이름 뒤에 단다."""
        if not spec.evidence:
            return {}
        placed: dict[str, list[int]] = {}
        grams = {f.key: bigrams(str(fields.get(f.key) or "")) for f in spec.fields}
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


def _clean(value: Any) -> str:
    return EVIDENCE_ID_RE.sub("", str(value or "")).strip()


def _cell(value: Any) -> str:
    text = _clean(value)
    return text.replace("|", "\\|").replace("\r", "").replace("\n", " / ") or "-"


HANDWRITTEN_BLANK = "ID: ________ / PW: ________"
BAR_COLOR = "#7055C8"  # 월별 일정표의 막대 색(화면은 CSS, Word/PDF 는 이 색으로 칠한다)


def _h(value: Any) -> str:
    """HTML 표 칸에 넣을 값. 사용자가 쓴 글이 태그로 해석되지 않도록 모두 이스케이프한다."""
    text = _clean(value)
    return html.escape(text, quote=True).replace("\r", "").replace("\n", " / ") or "-"


def _render_table(out: list[str], spec, items: list[dict], cites: _Citations) -> None:
    headers = [f.label for f in spec.fields] + list(spec.handwritten)
    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for item in items:
        fields = dict(item.get("fields") or {})
        fields.setdefault(spec.title_field, item_label(spec, item))
        marks = cites.marks(spec, item, fields)
        cells = [_cell(fields.get(f.key)) + marks.get(f.key, "") for f in spec.fields] + [HANDWRITTEN_BLANK] * len(spec.handwritten)
        out.append("| " + " | ".join(cells) + " |")
    out.append("")


def _gantt_cells(months: set[int]) -> str:
    """1~12월 칸. 이어지는 달은 한 칸으로 합쳐(colspan) 막대가 끊기지 않게 그린다."""
    cells: list[str] = []
    month = 1
    while month <= 12:
        filled = month in months
        end = month
        while end + 1 <= 12 and ((end + 1) in months) == filled:
            end += 1
        run = end - month + 1
        if filled:
            cells.append(f'<td class="bar" bgcolor="{BAR_COLOR}"' + (f' colspan="{run}"' if run > 1 else "") + ">&nbsp;</td>")
        else:
            cells.extend("<td></td>" for _ in range(run))
        month = end + 1
    return "".join(cells)


def _render_gantt(out: list[str], spec, items: list[dict]) -> None:
    """업무 단위 요약 + 일정 명세표: 계획기간을 1~12월 열로 펼치고, 걸치는 달에 이어진 막대를 그린다(근거 열 없음).

    머리글은 2행이다: 1행 '단위업무명 | 계획기간(12칸 병합) | 진행상황 요약', 2행 '1월 … 12월'.
    """
    out += [
        "<table>",
        f'<tr><th rowspan="2">{_h(spec.label(spec.title_field))}</th><th colspan="12">{_h(spec.label("period"))}</th><th rowspan="2">{_h(spec.label("note"))}</th></tr>',
        "<tr>" + "".join(f"<th>{month}월</th>" for month in range(1, 13)) + "</tr>",
    ]
    for item in items:
        fields = dict(item.get("fields") or {})
        fields.setdefault(spec.title_field, item_label(spec, item))
        months = set(plan_months(fields.get("period")))
        note = str(fields.get("note") or "").strip()
        period = str(fields.get("period") or "").strip()
        if period and not months:  # 월로 읽을 수 없는 계획기간은 원문을 마지막 열(진행상황 요약)에 남긴다
            note = f"계획기간: {period}" + (f" · {note}" if note else "")
        out.append(f"<tr><td>{_h(fields.get(spec.title_field))}</td>{_gantt_cells(months)}<td>{_h(note)}</td></tr>")
    out += ["</table>", ""]


def _same_project(a: str, b: str) -> bool:
    x, y = normalize_title(a), normalize_title(b)
    return bool(x and y) and (x == y or (min(len(x), len(y)) >= 4 and (x in y or y in x)))


def _columns(child: SlotSpec) -> list:
    """카드 안 표의 열: 연결 필드(어느 항목에 붙는지)를 뺀 필드."""
    return [f for f in child.fields if f.key != child.embed_link]


def _render_cards(out: list[str], spec, items: list[dict], cites: _Citations, children: list[tuple[SlotSpec, list[dict]]]) -> None:
    """항목마다 '항목 | 내용' 표로 보여 준다.

    다른 장에서 옮겨 온 항목(협업관계)이 있으면 그 행의 칸을 나눠 표로 넣으므로(정기업무: 소속 | 이름 | 주요역할 | 연락처 | 비고,
    비정기 업무: 담당자 | 협의내용 | 추후 고려사항)
    카드 전체를 HTML 표(rowspan/colspan)로 만든다. 옮겨 올 항목이 없는 장은 Markdown 표로 만든다.
    """
    for index, item in enumerate(items, start=1):
        fields = dict(item.get("fields") or {})
        fields.setdefault(spec.title_field, item_label(spec, item))
        name = fields.get(spec.title_field)
        marks = cites.marks(spec, item, fields)
        out += [f"### {index}) {_cell(name)}{marks.get(spec.title_field, '')}", ""]
        if not children:
            out += ["| 항목 | 내용 |", "|---|---|"]
            out += [f"| {f.label} | {_cell(fields.get(f.key))}{marks.get(f.key, '')} |" for f in spec.fields[1:]]
            out.append("")
            continue
        span = max(len(_columns(child_spec)) for child_spec, _ in children)
        wide = f' colspan="{span}"'
        rows = ["<table>", f"<tr><th>항목</th><th{wide}>내용</th></tr>"]
        for f in spec.fields[1:]:
            rows.append(f"<tr><th>{_h(f.label)}</th><td{wide}>{_h(fields.get(f.key))}{marks.get(f.key, '')}</td></tr>")
            for child_spec, child_items in children:
                if child_spec.embed_after != f.key:
                    continue
                mine = [c for c in child_items if _same_project(str((c.get("fields") or {}).get(child_spec.embed_link) or ""), str(name or ""))]
                if not mine:
                    rows.append(f"<tr><th>{_h(child_spec.embed_label)}</th><td{wide}>-</td></tr>")
                    continue
                columns = _columns(child_spec)
                last = f' colspan="{span - len(columns) + 1}"' if span > len(columns) else ""  # 열 수가 적은 표는 마지막 열을 넓힌다
                cells = lambda values, tag: "".join(f"<{tag}{last if i == len(values) - 1 else ''}>{v}</{tag}>" for i, v in enumerate(values))  # noqa: E731
                rows.append(f'<tr><th rowspan="{len(mine) + 1}">{_h(child_spec.embed_label)}</th>' + cells([_h(f.label) for f in columns], "th") + "</tr>")
                for c in mine:
                    cf = dict(c.get("fields") or {})
                    cf.setdefault(child_spec.title_field, c.get("title"))
                    rows.append("<tr>" + cells([_h(cf.get(f.key)) for f in columns], "td") + "</tr>")
        rows.append("</table>")
        out += rows + [""]
    # 어느 카드에도 맞지 않는 항목은 양식에 없는 표로 따로 싣지 않는다. 어느 항목인지 묻는 질문(nodes._link_gaps)이 화면의 확인 필요 사항에 남는다.


def body_field(child: SlotSpec) -> str:
    """연결 확인 문구에 함께 적을 내용 필드(협의내용·주요역할): 이름·연결 필드가 아닌 첫 '내용' 필드."""
    skip = {child.title_field, child.embed_link, "org"}
    return next(f.key for f in child.fields if f.key not in skip)


def unlinked_children(slots: dict[str, SlotState], active: list[SlotSpec]) -> list[tuple[SlotSpec, dict]]:
    """붙는 장(정기업무·프로젝트)의 어느 항목과도 맞지 않는 협업관계 항목. 문서는 이런 항목을 양식에 없는 표로 싣지 않는다."""
    out = []
    for child in active:
        if not child.embed_into:
            continue
        parent = SLOT_BY_KEY[child.embed_into]
        names = [str((p.get("fields") or {}).get(parent.title_field) or item_label(parent, p)) for p in (slots.get(child.embed_into) or {}).get("items", [])]
        for item in (slots.get(child.key) or {}).get("items", []):
            value = str((item.get("fields") or {}).get(child.embed_link) or "")
            if not any(_same_project(value, name) for name in names):
                out.append((child, item))
    return out


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
    cites = _Citations()
    out: list[str] = ["# 업무 인수인계서", ""]
    active = slots_for(profile)
    visible = [spec for spec in active if not spec.embed_into]  # 다른 장 안에 들어가는 장(협업관계)은 따로 싣지 않는다

    giver = " ".join(x for x in (profile.get("name"), profile.get("position")) if x) or "-"
    rows = [
        ("인계자", giver),
        ("소속", profile.get("organization")),
        ("인수자", profile.get("successor")),
        ("휴가 기간", leave_period(profile)),
        ("인계 일자", profile.get("handover_date")),
        ("인계 유형", MODE_LABELS[mode_of(profile)]),
        ("단위 업무명", profile.get("task_name")),
        ("담당 업무", profile.get("duties")),
        ("작성", f"{today.isoformat()} · AMIGO 자동 작성 초안 v{version}"),
    ]
    out += ["| 구분 | 내용 |", "|---|---|"]
    out += [f"| {k} | {_cell(v)} |" for k, v in rows if v]
    out.append("")

    for number, spec in enumerate(visible, start=1):
        slot = slots.get(spec.key) or {}
        items = slot.get("items") or []
        out += [f"## {number}. {spec.title}", ""]
        children = [(c, (slots.get(c.key) or {}).get("items") or []) for c in active if c.embed_into == spec.key]
        if not items and not any(rows for _, rows in children):
            out += ["확인된 내용이 없습니다.", ""]
            continue
        if not items:
            out += ["확인된 프로젝트가 없습니다.", ""]
        if spec.layout == "gantt":
            _render_gantt(out, spec, items)
        elif spec.layout == "card":
            _render_cards(out, spec, items, cites, children)
        else:
            _render_table(out, spec, items, cites)

    return "\n".join(out)

"""최종 인수인계서(Markdown) 렌더러.

문서 전체를 코드로 결정적으로 만든다(LLM 은 쓰지 않는다). 표·항목 값과 근거 번호가 바뀌거나 사라질 위험이 없어
작성 내용별로 근거 자료를 추적할 수 있다. 장 구성은 노션 '인수인계서 형식' 템플릿(template.py)을 따른다.
"""

from __future__ import annotations

import html
from datetime import date
from typing import Any

from .periods import plan_months
from .state import Gap, SlotState, item_label, normalize_title
from .template import MODE_LABELS, SLOT_BY_KEY, SlotSpec, mode_of, slots_for

STATUS_LABEL = {"open": "미질문", "asked": "답변 대기", "answered": "확인 대기", "skipped": "인계자 미확인"}


def _cell(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("|", "\\|").replace("\r", "").replace("\n", " / ") or "-"


class _Citations:
    def __init__(self) -> None:
        self.numbers: dict[str, int] = {}
        self.entries: list[tuple[int, str, str]] = []

    def refs(self, item: dict) -> str:
        nums = []
        for c in item.get("citations") or []:
            key = f"{c.get('label')}|{c.get('chunk_id') or c.get('quote', '')[:40]}"
            if key not in self.numbers:
                self.numbers[key] = len(self.numbers) + 1
                self.entries.append((self.numbers[key], str(c.get("label") or "출처"), str(c.get("quote") or "")))
            if self.numbers[key] not in nums:
                nums.append(self.numbers[key])
        return " ".join(f"[{n}]" for n in nums) or "-"


HANDWRITTEN_BLANK = "ID: ________ / PW: ________"
BAR_COLOR = "#7055C8"  # 월별 일정표의 막대 색(화면은 CSS, Word/PDF 는 이 색으로 칠한다)


def _h(value: Any) -> str:
    """HTML 표 칸에 넣을 값. 사용자가 쓴 글이 태그로 해석되지 않도록 모두 이스케이프한다."""
    text = str(value or "").strip()
    return html.escape(text, quote=True).replace("\r", "").replace("\n", " / ") or "-"


def _render_table(out: list[str], spec, items: list[dict], cites: _Citations) -> None:
    show_evidence = spec.evidence
    headers = [f.label for f in spec.fields] + list(spec.handwritten) + (["근거"] if show_evidence else [])
    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for item in items:
        fields = dict(item.get("fields") or {})
        fields.setdefault(spec.title_field, item_label(spec, item))
        cells = [_cell(fields.get(f.key)) for f in spec.fields] + [HANDWRITTEN_BLANK] * len(spec.handwritten)
        if show_evidence:
            cells.append(cites.refs(item))
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
    """업무 단위 요약 + 일정 명세표: 계획기간을 1~12월 열로 펼치고, 걸치는 달에 이어진 막대를 그린다(근거 열 없음)."""
    out += ["<table>", "<tr>" + f"<th>{_h(spec.fields[0].label)}</th>" + "".join(f"<th>{month}월</th>" for month in range(1, 13)) + "<th>비고</th></tr>"]
    for item in items:
        fields = dict(item.get("fields") or {})
        fields.setdefault(spec.title_field, item_label(spec, item))
        months = set(plan_months(fields.get("period")))
        note = str(fields.get("note") or "").strip()
        period = str(fields.get("period") or "").strip()
        if period and not months:  # 월로 읽을 수 없는 계획기간은 원문을 비고에 남긴다
            note = f"계획기간: {period}" + (f" · {note}" if note else "")
        out.append(f"<tr><td>{_h(fields.get(spec.title_field))}</td>{_gantt_cells(months)}<td>{_h(note)}</td></tr>")
    out += ["</table>", ""]


def _same_project(a: str, b: str) -> bool:
    x, y = normalize_title(a), normalize_title(b)
    return bool(x and y) and (x == y or (min(len(x), len(y)) >= 4 and (x in y or y in x)))


def _render_cards(out: list[str], spec, items: list[dict], cites: _Citations, children: list[tuple[SlotSpec, list[dict]]]) -> None:
    """항목마다 '항목 | 내용' 표로 보여 준다.

    다른 장에서 옮겨 온 항목(협업관계·비상 대응)이 있으면 그 행의 칸을 나눠 담당자 | 협의내용 | 추후 고려사항 표로 넣으므로
    카드 전체를 HTML 표(rowspan/colspan)로 만든다. 옮겨 올 항목이 없는 장은 Markdown 표로 만든다.
    """
    claimed: set[int] = set()
    for index, item in enumerate(items, start=1):
        fields = dict(item.get("fields") or {})
        fields.setdefault(spec.title_field, item_label(spec, item))
        name = fields.get(spec.title_field)
        out += [f"### {index}) {_cell(name)}", ""]
        if not children:
            out += ["| 항목 | 내용 |", "|---|---|"]
            out += [f"| {f.label} | {_cell(fields.get(f.key))} |" for f in spec.fields[1:]]
            if spec.evidence:
                out.append(f"| 근거 | {cites.refs(item)} |")
            out.append("")
            continue
        rows = ["<table>", '<tr><th>항목</th><th colspan="3">내용</th></tr>']
        for f in spec.fields[1:]:
            rows.append(f'<tr><th>{_h(f.label)}</th><td colspan="3">{_h(fields.get(f.key))}</td></tr>')
            for child_spec, child_items in children:
                if child_spec.embed_after != f.key:
                    continue
                mine = [c for c in child_items if _same_project(str((c.get("fields") or {}).get(child_spec.embed_link) or ""), str(name or ""))]
                claimed.update(id(c) for c in mine)
                if not mine:
                    rows.append(f'<tr><th>{_h(child_spec.embed_label)}</th><td colspan="3">-</td></tr>')
                    continue
                label = _h(child_spec.embed_label)
                rows.append(
                    f'<tr><th rowspan="{len(mine) + 1}">{label}</th>'
                    f'<th>{_h(child_spec.label("party"))}</th><th>{_h(child_spec.label("discussed"))}</th><th>{_h(child_spec.label("upcoming"))}</th></tr>'
                )
                for c in mine:
                    cf = c.get("fields") or {}
                    rows.append(f'<tr><td>{_h(cf.get("party") or c.get("title"))}</td><td>{_h(cf.get("discussed"))}</td><td>{_h(cf.get("upcoming"))}</td></tr>')
        if spec.evidence:
            rows.append(f'<tr><th>근거</th><td colspan="3">{_h(cites.refs(item))}</td></tr>')
        rows.append("</table>")
        out += rows + [""]
    for child_spec, child_items in children:  # 어느 프로젝트인지 맞출 수 없는 항목은 따로 모아 둔다
        rest = [c for c in child_items if id(c) not in claimed]
        if rest:
            link = child_spec.label(child_spec.embed_link)
            out += [f"### {child_spec.embed_label} · 연결되는 {link} 없음", ""]
            headers = [child_spec.label("party"), link, child_spec.label("discussed"), child_spec.label("upcoming")]
            out += ["| " + " | ".join(headers) + " |", "|---|---|---|---|"]
            for c in rest:
                cf = c.get("fields") or {}
                out.append("| " + " | ".join(_cell(cf.get(k)) for k in ("party", child_spec.embed_link, "discussed", "upcoming")) + " |")
            out.append("")


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
            out += ["확인된 내용이 없습니다. (아래 '확인 필요 사항' 참고)", ""]
            continue
        if not items:
            out += ["확인된 프로젝트가 없습니다. (아래 '확인 필요 사항' 참고)", ""]
        if spec.layout == "gantt":
            _render_gantt(out, spec, items)
        elif spec.layout == "card":
            _render_cards(out, spec, items, cites, children)
        else:
            _render_table(out, spec, items, cites)

    pending = [g for g in gaps if g.get("status") in STATUS_LABEL]
    out += [f"## {len(visible) + 1}. 확인 필요 사항", ""]
    if pending:
        for gap in pending:
            title = SLOT_BY_KEY[gap["slot"]].title if gap.get("slot") in SLOT_BY_KEY else "기타"
            out.append(f"- [{title}] {gap.get('description') or gap.get('question')} ({STATUS_LABEL[gap['status']]})")
    else:
        out.append("- 없음")
    out.append("")

    out += [f"## {len(visible) + 2}. 근거 자료", ""]
    if cites.entries:
        for num, label, quote in cites.entries:
            out.append(f"{num}. {label}" + (f' — "{quote}"' if quote else ""))
    else:
        out.append("- 없음")
    if sources:
        out += ["", "**등록된 원천 자료**", ""]
        out += [f"- {s.get('name')} ({s.get('type')})" for s in sources if s.get("type") != "chat"]
    out.append("")
    return "\n".join(out)

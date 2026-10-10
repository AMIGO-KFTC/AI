"""최종 인수인계서(Markdown) 렌더러.

표와 근거 번호는 코드로 결정적으로 만든다. LLM 은 '업무 개요'와 '핵심 당부사항' 문장만 쓰므로,
근거 표시가 바뀌거나 사라질 위험이 없다(작성 내용별 근거 자료 추적 가능).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .state import Gap, SlotState, item_label
from .template import MODE_LABELS, SLOT_BY_KEY, mode_of, slots_for

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


def _render_table(out: list[str], spec, items: list[dict], cites: _Citations) -> None:
    headers = [f.label for f in spec.fields] + list(spec.handwritten) + ["근거"]
    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for item in items:
        fields = dict(item.get("fields") or {})
        fields.setdefault(spec.title_field, item_label(spec, item))
        cells = [_cell(fields.get(f.key)) for f in spec.fields] + [HANDWRITTEN_BLANK] * len(spec.handwritten)
        out.append("| " + " | ".join(cells + [cites.refs(item)]) + " |")
    out.append("")


def _render_cards(out: list[str], spec, items: list[dict], cites: _Citations) -> None:
    """필드가 많은 장은 항목마다 '항목 | 내용' 표로 보여 준다."""
    for index, item in enumerate(items, start=1):
        fields = dict(item.get("fields") or {})
        fields.setdefault(spec.title_field, item_label(spec, item))
        out += [f"### {index}) {_cell(fields.get(spec.title_field))}", "", "| 항목 | 내용 |", "|---|---|"]
        out += [f"| {f.label} | {_cell(fields.get(f.key))} |" for f in spec.fields[1:]]
        out += [f"| 근거 | {cites.refs(item)} |", ""]


def render_document(
    *,
    profile: dict[str, Any],
    slots: dict[str, SlotState],
    gaps: list[Gap],
    extras: dict[str, Any],
    sources: list[dict[str, Any]],
    version: int,
    today: date | None = None,
) -> str:
    today = today or date.today()
    cites = _Citations()
    out: list[str] = ["# 업무 인수인계서", ""]
    active = slots_for(profile)

    giver = " ".join(x for x in (profile.get("name"), profile.get("position")) if x) or "-"
    rows = [
        ("인계자", giver),
        ("소속", profile.get("organization")),
        ("인수자", profile.get("successor")),
        ("인계 일자", profile.get("handover_date")),
        ("인계 유형", MODE_LABELS[mode_of(profile)]),
        ("담당 업무", profile.get("duties")),
        ("작성", f"{today.isoformat()} · AMIGO 자동 작성 초안 v{version}"),
    ]
    out += ["| 구분 | 내용 |", "|---|---|"]
    out += [f"| {k} | {_cell(v)} |" for k, v in rows if v]
    out.append("")

    out += ["## 1. 업무 개요", "", extras.get("overview") or "-", ""]
    key_points = [p for p in extras.get("key_points") or [] if p]
    if key_points:
        out += ["**인수자 핵심 당부사항**", ""]
        out += [f"{i}. {p}" for i, p in enumerate(key_points, start=1)]
        out.append("")

    for number, spec in enumerate(active, start=2):
        slot = slots.get(spec.key) or {}
        items = slot.get("items") or []
        out += [f"## {number}. {spec.title}", ""]
        if not items:
            out += ["확인된 내용이 없습니다. (아래 '확인 필요 사항' 참고)", ""]
            continue
        if spec.layout == "card":
            _render_cards(out, spec, items, cites)
        else:
            _render_table(out, spec, items, cites)

    pending = [g for g in gaps if g.get("status") in STATUS_LABEL]
    out += [f"## {len(active) + 2}. 확인 필요 사항", ""]
    if pending:
        for gap in pending:
            title = SLOT_BY_KEY[gap["slot"]].title if gap.get("slot") in SLOT_BY_KEY else "기타"
            out.append(f"- [{title}] {gap.get('description') or gap.get('question')} ({STATUS_LABEL[gap['status']]})")
    else:
        out.append("- 없음")
    out.append("")

    out += [f"## {len(active) + 3}. 근거 자료", ""]
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

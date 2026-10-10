"""최종 인수인계서(Markdown) 렌더러.

표와 근거 번호는 코드로 결정적으로 만든다. LLM 은 '업무 소개'(3줄)와 '핵심 당부사항' 문장만 쓰므로,
근거 표시가 바뀌거나 사라질 위험이 없다(작성 내용별 근거 자료 추적 가능).

양식(10/4 회의 기준): 업무 단위별 1건 → 1 업무 소개 / 이해관계자(관계 도식) / 정기 업무 / 비정기 업무 /
시스템 접근 계정(ID·PW 수기 작성란) / 부서별 특이사항. 장기휴가는 Light 버전이라 일부 장을 뺀다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .state import Gap, SlotState, item_label
from .template import MODES, SLOT_BY_KEY, SlotSpec, normalize_mode, slots_for_mode
from .textkit import infer_relation, infer_side

STATUS_LABEL = {"open": "미질문", "asked": "답변 대기", "answered": "확인 대기", "skipped": "인계자 미확인"}

# 관계 유형별 화살표 색(도식). 요청·보고는 담당자에서 상대 방향, 협의는 양방향.
RELATION_COLOR = {"요청": "#2563eb", "협의": "#16a34a", "보고": "#ea580c"}
DEFAULT_RELATION = "협의"
BLANK = "\u3000"  # 수기 작성란(표 빈칸이 '-' 로 바뀌지 않도록 전각 공백)


def _cell(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("|", "\\|").replace("\r", "").replace("\n", " / ") or "-"


class _Citations:
    def __init__(self) -> None:
        self.numbers: dict[str, int] = {}
        self.entries: list[tuple[int, str, str]] = []

    def numbers_for(self, item: dict) -> list[int]:
        nums: list[int] = []
        for c in item.get("citations") or []:
            key = f"{c.get('label')}|{c.get('chunk_id') or c.get('quote', '')[:40]}"
            if key not in self.numbers:
                self.numbers[key] = len(self.numbers) + 1
                self.entries.append((self.numbers[key], str(c.get("label") or "출처"), str(c.get("quote") or "")))
            if self.numbers[key] not in nums:
                nums.append(self.numbers[key])
        return nums

    def refs(self, item: dict) -> str:
        return " ".join(f"[{n}]" for n in self.numbers_for(item)) or "-"


def _fields(spec: SlotSpec, item: dict) -> dict[str, Any]:
    fields = dict(item.get("fields") or {})
    fields.setdefault(spec.title_field, item_label(spec, item))
    return fields


def _table(spec: SlotSpec, items: list[dict], cites: _Citations, *, skip: tuple[str, ...] = (), extra: tuple[str, ...] = ()) -> list[str]:
    """항목 1건 = 표 1행. 필드가 적은 장(업무 소개·이해관계자·계정·부서별 특이사항)에 쓴다."""
    columns = [f for f in spec.fields if f.key not in skip]
    headers = [f.label for f in columns] + list(extra) + ["근거"]
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for item in items:
        fields = _fields(spec, item)
        cells = [_cell(fields.get(f.key)) for f in columns] + [BLANK] * len(extra)
        out.append("| " + " | ".join(cells + [cites.refs(item)]) + " |")
    out.append("")
    return out


def _item_blocks(number: int, spec: SlotSpec, items: list[dict], cites: _Citations) -> list[str]:
    """항목 1건 = 소제목 + (항목 | 내용) 표. 필드가 많은 세부 업무(정기·비정기)에 쓴다."""
    out: list[str] = []
    for index, item in enumerate(items, start=1):
        fields = _fields(spec, item)
        out += [f"### {number}-{index}. {_cell(item_label(spec, item))}", "", "| 항목 | 내용 |", "|---|---|"]
        for f in spec.fields[1:]:
            out.append(f"| {f.label} | {_cell(fields.get(f.key))} |")
        out += ["", f"근거: {cites.refs(item)}", ""]
    return out


def _relation(item: dict) -> str:
    fields = item.get("fields") or {}
    relation = str(fields.get("relation") or "").strip()
    for name in RELATION_COLOR:
        if name in relation:
            return name
    return infer_relation(str(fields.get("role") or "")) or DEFAULT_RELATION


def _side(item: dict, organization: str) -> str:
    fields = item.get("fields") or {}
    side = str(fields.get("side") or "").strip()
    if "외부" in side:
        return "외부"
    if "내부" in side:
        return "내부"
    return infer_side(str(fields.get("party") or item.get("title") or ""), organization) or "외부"


def _label(text: str) -> str:
    return str(text or "").replace('"', "'").replace("\n", " ").strip() or "-"


def stakeholder_diagram(items: list[dict], *, owner: str, task: str, organization: str = "") -> str:
    """이해관계자 관계도(Mermaid). 요청·협의·보고를 화살표 색으로 구분한다."""
    lines = ["```mermaid", "flowchart LR", f'  ME(["{_label(owner)}<br/>{_label(task)}"])']
    groups: dict[str, list[str]] = {"내부": [], "외부": []}
    links: list[tuple[str, str]] = []
    for index, item in enumerate(items, start=1):
        fields = item.get("fields") or {}
        node = f"S{index}"
        party = _label(fields.get("party") or item.get("title"))
        person = _label(fields.get("person")) if fields.get("person") else ""
        groups[_side(item, organization)].append(f'    {node}["{party}{"<br/>" + person if person else ""}"]')
        links.append((node, _relation(item)))
    for side, nodes in groups.items():
        if nodes:
            lines += [f'  subgraph {"IN" if side == "내부" else "OUT"}["{side}"]', *nodes, "  end"]
    for node, relation in links:
        lines.append(f"  ME <-->|{relation}| {node}" if relation == "협의" else f"  ME -->|{relation}| {node}")
    for index, (_, relation) in enumerate(links):
        lines.append(f"  linkStyle {index} stroke:{RELATION_COLOR[relation]},stroke-width:2px")
    lines.append("```")
    return "\n".join(lines)


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
    mode = normalize_mode(profile.get("mode"))
    task = str(profile.get("task_name") or profile.get("duties") or "").strip()
    out: list[str] = ["# 업무 인수인계서", ""]

    giver = " ".join(x for x in (profile.get("name"), profile.get("position")) if x) or "-"
    rows = [
        ("인계 유형", MODES[mode] + (" (Light)" if mode == "leave" else "")),
        ("단위 업무명", task),
        ("인계자", giver),
        ("소속", profile.get("organization")),
        ("인수자", profile.get("successor")),
        ("인계 일자", profile.get("handover_date")),
        ("작성", f"{today.isoformat()} · AMIGO 자동 작성 초안 v{version}"),
    ]
    out += ["| 구분 | 내용 |", "|---|---|"]
    out += [f"| {k} | {_cell(v)} |" for k, v in rows if v]
    out.append("")

    # 1. 업무 소개: 3줄 소개 + 업무지침·관계법령·업무매뉴얼 + 핵심 당부사항
    intro = SLOT_BY_KEY["duties"]
    out += [f"## 1. 업무 소개{(' — ' + task) if task else ''}", "", extras.get("overview") or "-", ""]
    duty_items = (slots.get("duties") or {}).get("items") or []
    if duty_items:
        out += _table(intro, duty_items, cites)
    key_points = [p for p in extras.get("key_points") or [] if p]
    if key_points:
        out += ["**인수자 핵심 당부사항**", ""]
        out += [f"{i}. {p}" for i, p in enumerate(key_points, start=1)]
        out.append("")

    number = 1
    for spec in slots_for_mode(mode):
        if spec.key == "duties":
            continue
        number += 1
        items = (slots.get(spec.key) or {}).get("items") or []
        out += [f"## {number}. {spec.title}", ""]
        if not items:
            out += ["확인된 내용이 없습니다. (아래 '확인 필요 사항' 참고)", ""]
            continue
        if spec.key == "stakeholders":
            out += [stakeholder_diagram(items, owner=giver, task=task or "담당 업무", organization=str(profile.get("organization") or "")), ""]
            out += ["화살표 색: 요청(파랑) · 협의(초록, 양방향) · 보고(주황)", ""]
            out += _table(spec, items, cites)
        elif spec.key in ("recurring", "projects"):
            out += _item_blocks(number, spec, items, cites)
        elif spec.key == "accounts":
            out += ["> 아이디·비밀번호는 보안 서약에 따라 AI에 입력하지 않았습니다. 출력본에 직접 기재해 주세요.", ""]
            out += _table(spec, items, cites, extra=("ID", "PW"))
        else:
            out += _table(spec, items, cites)

    pending = [g for g in gaps if g.get("status") in STATUS_LABEL]
    out += [f"## {number + 1}. 확인 필요 사항", ""]
    if pending:
        for gap in pending:
            title = SLOT_BY_KEY[gap["slot"]].title if gap.get("slot") in SLOT_BY_KEY else "기타"
            out.append(f"- [{title}] {gap.get('description') or gap.get('question')} ({STATUS_LABEL[gap['status']]})")
    else:
        out.append("- 없음")
    out.append("")

    out += [f"## {number + 2}. 근거 자료", ""]
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

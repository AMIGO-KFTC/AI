"""사용자가 완성된 문서 화면에서 직접 고친 값을 에이전트 상태에 '인계자 확정값'으로 반영한다.

월별 일정표의 막대를 고치면 그 단위 업무의 계획기간(period)을 새 월 목록으로 바꾼다. 값은 인계자 답변과 같은 순위(1순위)로 들어가므로,
이후 검토 의견·자료 추가로 문서를 다시 만들어도 자료에서 읽은 옛 값에 덮어써지지 않는다(precedence.py).
"""

from __future__ import annotations

import html
import re

from .periods import span_to_period
from .precedence import user_meta
from .render import _gantt_cells, display_name
from .state import SlotState, apply_field, item_label
from .template import SLOT_BY_KEY

EDIT_LABEL = "문서에서 직접 수정"


def apply_gantt_edit(slots: dict[str, SlotState], name: str, nth: int, months: list[int]) -> str | None:
    """월별 일정표에서 이름이 `name` 인 nth(0부터) 번째 행의 계획기간을 `months` 로 바꾼다. 바꾼 슬롯 키를 반환하고, 행을 못 찾으면 None."""
    seen = 0
    for key, slot in slots.items():
        spec = SLOT_BY_KEY.get(key)
        if spec is None or spec.layout != "gantt":
            continue
        for item in slot.get("items", []):
            shown = str((item.get("fields") or {}).get(spec.title_field) or item_label(spec, item))  # 문서(render.py)의 첫 열과 같은 이름
            # 문서에는 근거 번호('(E3)')·지라 키('[LGL-3]')를 뗀 이름이 보이므로, 저장된 이름도 같은 방식으로 다듬어 견준다(안 그러면 행을 못 찾는다)
            if display_name(shown) != display_name(name):
                continue
            if seen < nth:
                seen += 1
                continue
            period = span_to_period(months)
            item.get("fields", {}).pop("period", None)  # 직접 고친 값이 기존 값(같은 값이어도)을 확실히 대체한다
            (item.get("field_meta") or {}).pop("period", None)
            apply_field(item, "period", period or "해당 월 없음", user_meta(EDIT_LABEL))
            item["origin"] = "user" if item.get("origin") == "user" else "mixed"
            item["confirmed"] = True
            return key
    return None


# ---------------------------------------------------------------- AI 작업 기록이 없는 문서(데모·가져온 문서 등)의 막대 수정
_TABLE = re.compile(r"<table\b[^>]*>.*?</table>", re.S)
_ROW = re.compile(r'^(<tr><td>(.*?)</td>)((?:<td></td>|<td class="bar"[^>]*>&nbsp;</td>)+)(<td>[^<]*</td></tr>)[ \t]*$', re.M)
_YEAR_TH = re.compile(r'<th colspan="(\d+)">(\d{4})년</th>')
_QUARTER_TH = re.compile(r"<th>([1-4])분기</th>")
_MONTH_TH = re.compile(r"<th>(\d{1,2})월</th>")
_CELL = re.compile(r"<td([^>]*)>")
_COLSPAN = re.compile(r'colspan="(\d+)"')


def _gantt_columns(table: str) -> list[tuple[int, ...]] | None:
    """일정표 머리글에서 칸마다 가리키는 달 값: 월 표는 [(1,), (2,), …], 해를 넘기는 분기 표는 [(202601, 202602, 202603), …]. 일정표가 아니면 None."""
    head = table.split("<tr><td>")[0]
    quarters = _QUARTER_TH.findall(head)
    if quarters:
        years = [int(y) for n, y in _YEAR_TH.findall(head) for _ in range(int(n))]
        if len(years) != len(quarters):
            return None
        return [tuple(year * 100 + (int(q) - 1) * 3 + d for d in (1, 2, 3)) for year, q in zip(years, quarters)]
    months = [int(m) for m in _MONTH_TH.findall(head)]
    return [(m,) for m in months] if months == list(range(1, 13)) else None


def _cells_span(cells: str) -> int:
    """행의 달 칸 묶음이 차지하는 칸 수(colspan 합)."""
    return sum(int(span.group(1)) if (span := _COLSPAN.search(cell.group(1))) else 1 for cell in _CELL.finditer(cells))


def edit_gantt_markdown(markdown: str, name: str, nth: int, months: list[int]) -> str | None:
    """AI 작업 상태 없이 문서(Markdown+HTML)의 일정표에서 이름이 `name` 인 nth(0부터, 표마다 센다) 번째 행의 막대만 `months` 로 바꾼다.

    월 표는 1~12, 해를 넘기는 분기 표는 YYYYMM 값을 받는다(분기 칸은 그 분기의 달이 하나라도 있으면 칠한다).
    이름·비고 같은 직접 쓴 글은 그대로 두고 막대 칸만 다시 그린다. 행을 못 찾으면 None.
    """
    want = set(months)
    target = display_name(name)
    for table_match in _TABLE.finditer(markdown):
        table = table_match.group(0)
        columns = _gantt_columns(table)
        if columns is None:
            continue
        seen = 0
        for row in _ROW.finditer(table):
            if display_name(html.unescape(row.group(2))) != target:
                continue
            if seen < nth:
                seen += 1
                continue
            if _cells_span(row.group(3)) != len(columns):  # 머리글과 칸 수가 다른 행은 손대지 않는다
                break
            filled = [any(value in want for value in column) for column in columns]
            fixed = row.group(1) + _gantt_cells(filled) + row.group(4)
            table = table[: row.start()] + fixed + table[row.end() :]
            return markdown[: table_match.start()] + table + markdown[table_match.end() :]
    return None

"""'계획기간' 문장에서 1~12월 중 걸치는 달을 읽어 낸다(업무 단위 요약 표의 월별 막대에 쓴다).

읽는 표기 예시
- 구간: `2026.03~2026.12`, `2026-03-01 ~ 2026-12-31`, `3월~12월`, `3월부터 12월까지`, `2026.10 ~ 2027.01`(해를 넘기면 10~12월과 1월)
- 나열: `1·4·7·10월`, `3, 6, 9, 12월`, `11월`
- 상시: `상시`, `연중`, `매월`, `매일`, `매주`, `수시`

- 반기·분기: `상반기`, `하반기`, `2026년 3분기`, `1~2분기`, `3분기~4분기`, `Q1~Q2`, `3Q`, 월이 따로 없는 `분기별`·`매 분기`(분기 말월 3·6·9·12월)
- 줄임·시점: `3~5월`, `연초`(1월), `연말`(12월), `연말연시`(12월과 1월)

읽을 수 없으면 빈 목록을 돌려주며, 문서에서는 원문을 일정표의 마지막 열(진행상황 요약)에 그대로 보여 준다.
"""

from __future__ import annotations

import re

ALWAYS_RE = re.compile(r"상시|연중|연간|매월|매일|매주|수시|항시|365")
_DATE_TOKEN = re.compile(r"(?<!\d)(\d{4})\s*[.\-/년]\s*(\d{1,2})(?:\s*[.\-/월]\s*\d{1,2}\s*일?)?(?!\d)")
_MONTH_TOKEN = re.compile(r"(?<![\d.])(\d{1,2})\s*월")
_MONTH_LIST = re.compile(r"((?:\d{1,2}\s*[·,、/]\s*)+\d{1,2})\s*월")
_RANGE_SEP = re.compile(r"[~∼～–—]|부터|에서|-|→")
_SEP = r"\s*(?:[~∼～–—-]|부터)\s*"
_BARE_RANGE = re.compile(rf"(?<![\d.\-/])(\d{{1,2}}){_SEP}(\d{{1,2}})\s*월(?!\d)")  # 3~5월
_QUARTER_RANGE = re.compile(rf"(?<![\d.])([1-4]){_SEP}([1-4])\s*분기")  # 1~2분기
_QUARTER_LIST = re.compile(r"(?<![\d.])([1-4](?:\s*[·,、/]\s*[1-4])+)\s*분기")  # 1·2분기
_QUARTER = re.compile(r"(?<![\d.])([1-4])\s*분기|(?<![A-Za-z])Q\s*([1-4])(?![\d])|(?<![\d.A-Za-z])([1-4])\s*Q(?![A-Za-z])", re.I)
_HALF = re.compile(r"(상|하)\s*반기")
_EACH_QUARTER = re.compile(r"분기\s*별|매\s*분기|분기\s*마다")
_FROM_YEAR_START = re.compile(rf"연초{_SEP}(\d{{1,2}})\s*월")  # 연초~3월
_TO_YEAR_END = re.compile(rf"(\d{{1,2}})\s*월{_SEP}연말")  # 9월~연말
_YEAR_START = re.compile(r"연초")
_YEAR_END = re.compile(r"연말(?!\s*정산)")
_YEAR_END_START = re.compile(r"연말\s*연시")


def _valid(month: int) -> bool:
    return 1 <= month <= 12


def _span(start: int, end: int, years: tuple[int, int] | None = None) -> set[int]:
    """start월~end월에 걸친 달. 해를 넘기면(end<start 또는 연도가 다르면) 12월을 지나 다음 해로 이어진다."""
    if years and years[1] - years[0] >= 1 and (years[1] - years[0]) * 12 + end - start >= 11:
        return set(range(1, 13))
    if start <= end and not (years and years[1] > years[0]):
        return set(range(start, end + 1))
    return set(range(start, 13)) | set(range(1, end + 1))


def _quarter(n: int) -> set[int]:
    return set(range(3 * (n - 1) + 1, 3 * n + 1))


def _calendar_words(raw: str) -> tuple[set[int], str]:
    """반기·분기·연초·연말·'3~5월' 같은 표현에서 달을 읽고, 읽은 부분은 지운 문장을 함께 돌려준다."""
    months: set[int] = set()

    def take(pattern: re.Pattern[str], found) -> None:
        nonlocal raw
        for m in pattern.finditer(raw):
            months.update(found(m))
        raw = pattern.sub(" ", raw)

    take(_FROM_YEAR_START, lambda m: set(range(1, int(m.group(1)) + 1)) if _valid(int(m.group(1))) else set())
    take(_TO_YEAR_END, lambda m: set(range(int(m.group(1)), 13)) if _valid(int(m.group(1))) else set())
    take(_YEAR_END_START, lambda m: {12, 1})
    take(_YEAR_START, lambda m: {1})
    take(_YEAR_END, lambda m: {12})
    take(_HALF, lambda m: set(range(1, 7)) if m.group(1) == "상" else set(range(7, 13)))
    take(_QUARTER_LIST, lambda m: set().union(*(_quarter(int(q)) for q in re.findall(r"[1-4]", m.group(1)))))
    take(_QUARTER_RANGE, lambda m: set().union(*(_quarter(q) for q in range(int(m.group(1)), int(m.group(2)) + 1))))
    quarters = [(m.start(), int(next(g for g in m.groups() if g))) for m in _QUARTER.finditer(raw)]
    if quarters:
        between = raw[quarters[0][0] : quarters[-1][0]]
        numbers = [q for _, q in quarters]
        if len(quarters) >= 2 and _RANGE_SEP.search(between):
            numbers = list(range(min(numbers), max(numbers) + 1))
        months.update(set().union(*(_quarter(q) for q in numbers)))
        raw = _QUARTER.sub(" ", raw)
    for m in _BARE_RANGE.finditer(raw):
        a, b = int(m.group(1)), int(m.group(2))
        if _valid(a) and _valid(b):
            months |= _span(a, b)
    raw = _BARE_RANGE.sub(lambda m: " ", raw) if months else raw
    return months, raw


def plan_months(text: str | None) -> list[int]:
    """계획기간 문장에서 걸치는 달(1~12)을 오름차순으로 돌려준다. 읽을 수 없으면 빈 목록."""
    raw = str(text or "").strip()
    if not raw:
        return []
    if ";" in raw:  # 'A; B' 는 따로 읽어 합친다(months_to_period 가 떨어진 구간을 이렇게 잇는다)
        return sorted({m for part in raw.split(";") for m in plan_months(part)})
    if ALWAYS_RE.search(raw):
        return list(range(1, 13))
    extra, rest = _calendar_words(raw)
    months = extra | set(_explicit_months(rest))
    if not months and _EACH_QUARTER.search(raw):
        months = {3, 6, 9, 12}  # 월이 따로 없는 '분기별'은 각 분기 말월에 표시한다
    return sorted(months)


def _explicit_months(raw: str) -> list[int]:
    tokens: list[tuple[int, int, int | None]] = []  # (위치, 월, 연도)
    for m in _DATE_TOKEN.finditer(raw):
        if _valid(int(m.group(2))):
            tokens.append((m.start(), int(m.group(2)), int(m.group(1))))
    covered = [(m.start(), m.end()) for m in _DATE_TOKEN.finditer(raw)]
    listed: set[int] = set()
    for m in _MONTH_LIST.finditer(raw):
        listed |= {int(n) for n in re.findall(r"\d{1,2}", m.group(1)) if _valid(int(n))}
        covered.append((m.start(), m.end()))
    for m in _MONTH_TOKEN.finditer(raw):
        if any(a <= m.start() < b for a, b in covered):
            continue
        if _valid(int(m.group(1))):
            tokens.append((m.start(), int(m.group(1)), None))
    tokens.sort()

    months: set[int] = set(listed)
    if len(tokens) >= 2:
        first, last = tokens[0], tokens[-1]
        between = raw[first[0] : last[0]]
        if _RANGE_SEP.search(between):
            years = (first[2], last[2]) if first[2] and last[2] else None
            months |= _span(first[1], last[1], years)
            for _, month, _year in tokens[1:-1]:
                months.add(month)
        else:
            months |= {month for _, month, _ in tokens}
    elif tokens:
        months.add(tokens[0][1])
    return sorted(months)


def months_to_period(months: list[int] | set[int]) -> str:
    """월 목록을 계획기간 문장으로 되돌린다(plan_months 로 다시 읽으면 같은 달이 나온다). 예: [3,4,5,9] → '3월~5월; 9월'."""
    ordered = sorted({m for m in months if _valid(m)})
    if not ordered:
        return ""
    runs: list[tuple[int, int]] = []
    for month in ordered:
        if runs and month == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], month)
        else:
            runs.append((month, month))
    return "; ".join(f"{a}월" if a == b else f"{a}월~{b}월" for a, b in runs)

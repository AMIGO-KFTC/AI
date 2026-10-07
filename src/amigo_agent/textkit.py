"""한국어 문장 처리 도우미(조사 선택, 연락처·이름·부서 추출, 주기 추론)."""

from __future__ import annotations

import re

PHONE_RE = re.compile(r"(?:0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}|내선\s*\d{3,5}|[\w.+-]+@[\w-]+(?:\.[\w-]+)+)")
PERSON_RE = re.compile(
    r"([가-힣]{2,4})\s*(차장|과장|대리|주임|부장|팀장|실장|사원|선임|책임|수석|본부장|센터장|매니저)(?![가-힣])"
)
PARTY_RE = re.compile(
    r"(\(주\)\s*[가-힣A-Za-z0-9]{2,15}"
    r"|[가-힣A-Za-z0-9]{1,12}(?:팀|부서|본부|센터|기관|업체|위원회|사업단|담당관)"
    r"|[가-힣]{3,10}부)"
    r"(?=$|[^가-힣]|[과와은는이가을를에의도로])"
)
LIST_MARK_RE = re.compile(r"^\s*(?:[-*•·▪◦]|\d+[.)]|[가-하][.)]|[①-⑳])\s*")

_CYCLE_RULES = (
    (re.compile(r"매일|일일|날마다"), "매일"),
    (re.compile(r"매주|주간|주\s*1회|월요일|화요일|수요일|목요일|금요일"), "매주"),
    (re.compile(r"분기|1[·,/]\s*4[·,/]\s*7[·,/]\s*10\s*월"), "분기"),
    (re.compile(r"매월|월간|월\s*1회|매달|영업일"), "매월"),
    (re.compile(r"매년|연간|연\s*1회|해마다|\d{1,2}\s*월(?!\s*\d)"), "매년"),
)


def has_batchim(word: str) -> bool:
    for ch in reversed(word.strip()):
        if "가" <= ch <= "힣":
            return (ord(ch) - 0xAC00) % 28 != 0
        if ch.isalnum():
            return ch.lower() in "lmnr0136789"  # 영문/숫자 대략치(엘, 엠, 엔, 알, 영, 일, 삼 …)
    return False


def josa(word: str, pair: str) -> str:
    """josa("시스템", "을/를") -> "시스템을"."""
    first, second = pair.split("/")
    return f"{word}{first if has_batchim(word) else second}"


def strip_list_mark(text: str) -> str:
    return LIST_MARK_RE.sub("", text or "").strip()


def infer_cycle(*texts: str) -> str:
    joined = " ".join(t for t in texts if t)
    for pattern, label in _CYCLE_RULES:
        if pattern.search(joined):
            return label
    return ""


def short_title(text: str, limit: int = 24) -> str:
    text = strip_list_mark(text)
    head = re.split(r"[:：。,，\n]|[.](?=\s|$)|(?<=다)\s", text, maxsplit=1)[0].strip()
    head = head or text
    return head if len(head) <= limit else head[: limit - 1] + "…"


def extract_contacts(text: str) -> str:
    found = []
    for match in PHONE_RE.findall(text or ""):
        value = match.strip()
        if value not in found:
            found.append(value)
    return ", ".join(found)


_NOT_NAMES = {"기관", "업체", "담당", "부서", "업무", "유지", "보수", "관리", "운영", "외부", "내부", "협력"}


def extract_person(text: str) -> str:
    for match in PERSON_RE.finditer(text or ""):
        if match.group(1) not in _NOT_NAMES:
            return f"{match.group(1)} {match.group(2)}"
    return ""


def extract_parties(text: str) -> list[str]:
    parties = []
    for match in PARTY_RE.findall(text or ""):
        party = match.strip()
        if len(party) >= 2 and party not in parties and not party.endswith(("업무", "사업부")):
            parties.append(party)
    return parties

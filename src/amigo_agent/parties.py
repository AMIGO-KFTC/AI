"""협의 상대(팀 또는 개인)의 여러 표기를 같은 상대로 묶는다.

자료마다 같은 상대를 '재무팀 박지훈 차장', '박지훈 차장님', '재무팀(박지훈)', '(주)한빛솔루션', '㈜한빛솔루션', '한빛솔루션(주)' 처럼 다르게 적는다.
이름을 '조직 · 사람 · 직함'으로 나눠 읽고, 다음 규칙으로 같은 상대인지 정한다(정밀도를 우선해, 애매하면 합치지 않는다).

- 사람 이름이 같고 조직·직함이 서로 모순되지 않으면 같은 상대다(한쪽에 조직이나 직함이 없어도 된다).
- 조직만 적은 항목('재무팀')은 같은 조직의 사람이 딱 한 명일 때만 그 사람과 합친다. 두 명 이상이면 누구인지 알 수 없으므로 따로 둔다.
- 사람 없이 조직만 적은 항목끼리는 조직 이름이 같으면 합친다('보안팀'과 '보안 팀', '(주)유니온'과 '㈜유니온').
- 같은 조직이라도 사람이 다르면 합치지 않는다('재무팀 박지훈'과 '재무팀 김하나').
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .state import SlotItem, merge_item_fields

TITLES = (
    "대표이사", "본부장", "센터장", "팀장", "실장", "부장", "차장", "과장", "대리", "주임", "사원", "책임", "선임", "수석", "연구원",
    "매니저", "이사", "상무", "전무", "부사장", "사장", "팀원", "담당", "PM", "PL", "리드",
)
_TITLE_RE = "|".join(sorted(TITLES, key=len, reverse=True))
_SURNAMES = set(
    "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민나진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부"
)
_ORG_ENDINGS = ("팀", "부", "실", "과", "처", "센터", "본부", "국", "청", "솔루션", "시스템", "테크", "랩")  # 이름 끝 글자로도 흔한 원·사·회·단·소는 뺀다
_COMPANY_MARK = re.compile(r"\(주\)|주식회사|\(유\)|유한회사")


@dataclass(frozen=True)
class Party:
    org: str  # 비교용 조직 이름(공백·(주) 제거)
    person: str  # 사람 이름(없으면 "")
    title: str  # 직함(소문자, 없으면 "")


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")  # ㈜ → (주)
    text = re.sub(r"[\[\]{}]", " ", text)
    text = re.sub(r"\(([^()]*)\)", lambda m: f" ({m.group(1)}) ", text)
    return re.sub(r"\s+", " ", text).strip()


_COMMON_WORDS = {"서비스", "홈페이지", "시스템", "프로젝트", "콘텐츠", "플랫폼", "개발", "운영", "관리", "지원", "기획", "마케팅", "영업", "고객", "외부", "내부", "협력", "담당"}


def _looks_like_name(token: str, titled: bool = False) -> bool:
    """성씨 목록에 있는 2~4글자 한글이면 이름이다. 직함이 바로 뒤에 붙은 3글자 한글은 성씨 목록에 없어도(드문 성) 이름으로 본다."""
    if re.fullmatch(rf"(?:{_TITLE_RE})님?", token, re.I):  # '주임님' 의 '주임'은 성(주)+이름이 아니라 직함이다
        return False
    if re.fullmatch(r"[가-힣]+", token) is None or token.endswith(_ORG_ENDINGS):
        return False
    if 2 <= len(token) <= 4 and token[0] in _SURNAMES:
        return True
    return titled and len(token) == 3 and token not in _COMMON_WORDS


_REL = r"(?:협의|협업|논의|소통|협력)(?:\s*(?:내용|건|사항))?"
# Claude 가 항목 이름에 붙이는 '…과의 협의'. '총무과' 처럼 과가 부서명 끝인 경우를 깎지 않도록, 직함·님·팀/실/부 뒤의 '과/와'만 조사로 본다.
_RELATION_SUFFIX = re.compile(rf"(?P<pre>님|[팀실부처]|{_TITLE_RE})(?:과|와|하고)?의?\s*{_REL}\s*$|\s+(?:(?:과|와)의\s*)?{_REL}\s*$", re.I)


def parse_party(text: str) -> Party:
    cleaned = _clean(_RELATION_SUFFIX.sub(lambda m: m.group("pre") or "", text or "").strip() or text)
    is_company = bool(_COMPANY_MARK.search(cleaned))
    cleaned = _COMPANY_MARK.sub(" ", cleaned)
    cleaned = re.sub(r"\(\s*\)", " ", cleaned)
    person = title = ""
    # 이름 + 직함: '박지훈 차장', '정다은pm', '박지훈 차장님'
    pattern = re.compile(rf"(?P<name>[가-힣]{{2,4}})\s*\(?(?P<title>{_TITLE_RE})\)?(?:님)?(?![가-힣])", re.I)
    m = next((x for x in pattern.finditer(cleaned) if _looks_like_name(x.group("name"), titled=True)), None)
    if m:
        person, title = m.group("name"), m.group("title").lower()
        cleaned = (cleaned[: m.start()] + " " + cleaned[m.end() :]).strip()
    elif not is_company or len(re.findall(r"[^\s()]+", cleaned)) > 1:  # 직함 없이 이름만: '재무팀 박지훈', '재무팀(박지훈)' ((주)가 붙은 한 단어는 회사 이름이다)
        tokens = re.findall(r"[^\s()]+", cleaned)
        names = [t for t in tokens if _looks_like_name(t)]
        if names and len(tokens) >= 1 and (len(tokens) > 1 or names[0] == tokens[0] and not tokens[0].endswith(_ORG_ENDINGS)):
            person = names[-1]
            cleaned = " ".join(t for t in tokens if t != person)
    org = re.sub(r"[\s()·,/\-]+", "", cleaned).lower()
    return Party(org=org, person=person, title=title)


def same_person(a: Party, b: Party) -> bool:
    if not a.person or a.person != b.person:
        return False
    if a.org and b.org and a.org != b.org:
        return False
    if a.title and b.title and a.title != b.title:
        return False
    return True


def _info(item: SlotItem, party_key: str = "party") -> int:
    p = parse_party(str((item.get("fields") or {}).get(party_key) or item.get("title", "")))
    return len(p.org) + 3 * len(p.person) + len(p.title)


def _merge_into(target: SlotItem, source: SlotItem, party_key: str) -> None:
    """source 항목을 target 에 합친다(값 후보·근거·확인 상태 포함). 이름은 더 자세한 쪽을 쓴다."""
    for key, entries in (source.get("candidates") or {}).items():
        merged = target.setdefault("candidates", {}).setdefault(key, [])
        if not merged and str((target.get("fields") or {}).get(key, "")).strip():
            merged.append({"value": target["fields"][key], **((target.get("field_meta") or {}).get(key) or {})})
        merged.extend(entries)
    src = {**source, "fields": {k: v for k, v in (source.get("fields") or {}).items() if k != party_key and k not in (source.get("candidates") or {})}}
    merge_item_fields(target, src, defer=True)
    known = {(c.get("source_id"), c.get("chunk_id"), c.get("label")) for c in target.get("citations", [])}
    for citation in source.get("citations", []):
        sig = (citation.get("source_id"), citation.get("chunk_id"), citation.get("label"))
        if sig not in known:
            target.setdefault("citations", []).append(citation)
            known.add(sig)
    if source.get("origin") == "user" or target.get("origin") == "user":
        target["origin"] = "user" if target.get("origin") == source.get("origin") == "user" else "mixed"
    target["confirmed"] = bool(target.get("confirmed") or source.get("confirmed"))
    if _info(source) > _info(target):
        target["title"] = source["title"]
        (target.setdefault("fields", {}))[party_key] = source.get("fields", {}).get(party_key) or source["title"]


def consolidate_parties(items: list[SlotItem], party_key: str = "party") -> dict[str, str]:
    """같은 협의 상대로 읽히는 항목을 합친다(items 를 제자리에서 고침). 사라진 항목 id → 합쳐진 항목 id 를 돌려준다."""
    parsed = [parse_party(str((it.get("fields") or {}).get(party_key) or it.get("title", ""))) for it in items]
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    persons = [i for i, p in enumerate(parsed) if p.person]
    for x, i in enumerate(persons):  # 1) 같은 사람끼리
        for j in persons[x + 1 :]:
            if same_person(parsed[i], parsed[j]):
                union(i, j)
    orgs_only = [i for i, p in enumerate(parsed) if not p.person and p.org]
    for x, i in enumerate(orgs_only):  # 2) 조직만 적은 항목끼리
        for j in orgs_only[x + 1 :]:
            if parsed[i].org == parsed[j].org:
                union(i, j)
    for i in orgs_only:  # 3) 조직만 적은 항목은 그 조직의 사람이 딱 한 명일 때만 그 사람에게 붙인다
        roots = {find(j) for j in persons if parsed[j].org == parsed[i].org}
        if len(roots) == 1:
            union(i, roots.pop())
    for i in [i for i in persons if not parsed[i].org]:  # 4) 조직 없이 사람만 적은 항목도 같은 이름을 쓰는 상대가 한 명일 때 붙인다
        roots = {find(j) for j in persons if j != i and parsed[j].person == parsed[i].person and same_person(parsed[i], parsed[j])}
        if len(roots) == 1:
            union(i, roots.pop())

    groups: dict[int, list[int]] = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    remap: dict[str, str] = {}
    keep: list[SlotItem] = []
    for _, members in sorted(groups.items()):
        best = max(members, key=lambda i: (_info(items[i]), -i))
        target = items[best]
        for i in members:
            if i != best:
                remap[items[i]["id"]] = target["id"]
                _merge_into(target, items[i], party_key)
        keep.append(target)
    items[:] = keep
    return remap


def remap_ids(state_part: Any, remap: dict[str, str]) -> None:
    """합쳐져 사라진 항목 id 를 가리키던 질문·확인 대기 참조를 새 id 로 바꾼다."""
    if not remap:
        return
    for gap in state_part or []:
        if gap.get("item_id") in remap:
            gap["item_id"] = remap[gap["item_id"]]

"""부서・팀(반, 사무소)별 인수인계 목록(사내 문서의 요약): 부팀명 / 목록 / 비고.

정보만 담은 목록이다('~해야 한다' 같은 지침이 아니다). 백엔드가 소속 부·팀의 목록을 인계자 정보의 `dept_notes` 로 넘긴다.

    {"department": "디지털전략부 웹서비스팀",
     "items": ["홈페이지 유지보수 계약 명세표", "웹 접근성 인증 현황표"],          # 인계할 서류·물품(보여 줄 때 가. 나. 다. 를 붙인다)
     "note": "웹 접근성 인증서 사본 첨부\\n- 인증 유효기간, 갱신 예정일",          # 비고: 첫 줄은 첨부·인계물, '- ' 줄은 함께 적을 사항
     "others": {"경영지원본부 재무팀": {"items": [...], "note": "..."}}}            # 협업 부·팀의 목록(참고용)

이 정보는 인수인계서 항목의 근거가 아니다. 이 부·팀이 인계해야 하는 서류·물품의 목록이라서, 자료에서 각 서류·물품이 어떻게 관리되는지 찾고
자료에 없으면 인계자에게 보관 위치와 관리 방식을 묻는 데 쓴다(목록에 있는 이름은 이미 알기 때문에 다시 묻지 않는다).
"""

from __future__ import annotations

from typing import Any

MAX_ENTRIES = 12  # 목록 항목 상한(프롬프트가 부풀지 않게)
MAX_OTHERS = 12
ORDINALS = "가나다라마바사아자차카타파하"


def _text(value: Any, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def _note(value: Any, limit: int = 300) -> str:
    """비고는 줄 구분을 살린다(빈 줄 제외)."""
    lines = [_text(line, 200) for line in str(value or "").splitlines()]
    return "\n".join(line for line in lines if line)[:limit]


def _entries(raw: Any) -> dict[str, Any]:
    """한 부·팀의 목록과 비고를 정리한다."""
    raw = raw if isinstance(raw, dict) else {}
    items: list[str] = []
    for value in raw.get("items") or []:
        name = _text(value) if isinstance(value, str) else ""
        if name and name not in items:
            items.append(name)
    return {"items": items[:MAX_ENTRIES], "note": _note(raw.get("note"))}


def clean_dept_info(raw: dict[str, Any] | None) -> dict[str, Any]:
    """profile["dept_notes"] 를 정리한다. 이 부·팀에 정보가 하나도 없으면 items 가 빈 목록, note 가 빈 문자열이다."""
    if not isinstance(raw, dict):
        return {"department": "", "items": [], "note": "", "others": {}}
    others = {}
    for name, value in list((raw.get("others") or {}).items())[:MAX_OTHERS] if isinstance(raw.get("others"), dict) else []:
        entries = _entries(value)
        if _text(name) and (entries["items"] or entries["note"]):
            others[_text(name, 50)] = entries
    return {"department": _text(raw.get("department"), 100), **_entries(raw), "others": others}


def has_info(info: dict[str, Any]) -> bool:
    return bool(info["items"] or info["note"])


def numbered(items: list[str]) -> list[str]:
    """원본 표처럼 '가. 나. 다.' 번호를 붙인다."""
    return [f"{ORDINALS[i]}. {name}" if i < len(ORDINALS) else f"{i + 1}. {name}" for i, name in enumerate(items)]


def note_parts(note: str) -> tuple[str, list[str]]:
    """비고를 첫 줄(첨부·인계물)과 '- ' 로 시작하는 기재 사항으로 나눈다."""
    head, details = "", []
    for line in note.splitlines():
        if line.startswith("-"):
            details.append(line.lstrip("- ").strip())
        elif not head:
            head = line
    return head, [d for d in details if d]


def targets(info: dict[str, Any]) -> list[dict[str, str]]:
    """인계 목록의 서류·물품마다, 그리고 비고마다 인계자에게 확인할 질문을 만든다. 목록에 있는 이름은 이미 알기 때문에 묻지 않는다."""
    out = [
        {"kind": "item", "name": name, "label": "인수인계 목록",
         "question": f"인수인계 목록의 '{name}'은(는) 어디에 보관되어 있고, 최신본은 누가 어떤 주기로 관리하나요?"}
        for name in info["items"]
    ]
    head, details = note_parts(info["note"])
    if head or details:
        what = ", ".join(details)
        question = (f"인수인계 목록 비고에 따라 '{head}'와(과) 관련해 {what}을(를) 알려 주시겠어요?" if head and what
                    else f"인수인계 목록 비고의 {what}을(를) 알려 주시겠어요?" if what
                    else f"인수인계 목록 비고의 '{head}'는 어떻게 준비되어 있나요?")
        out.append({"kind": "note", "name": head or what, "label": "인수인계 목록 비고", "question": question})
    return out


def pick_questions(info: dict[str, Any], limit: int = 3) -> list[dict[str, str]]:
    """질문할 대상을 최대 limit 개 고른다. 비고가 있으면 목록 앞쪽 항목과 함께 꼭 넣는다."""
    found = targets(info)
    note = [t for t in found if t["kind"] == "note"]
    items = [t for t in found if t["kind"] == "item"]
    if not note or limit <= 0:
        return items[:limit]
    return items[: limit - 1] + note

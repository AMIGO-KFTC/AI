"""부서별 특이사항(사내 문서의 요약): 부서 / 중요문서 위치 / 관리 가맹점 / 라이센스 업체.

정보만 담은 목록이다('~해야 한다' 같은 지침이 아니다). 백엔드가 소속 부서의 정보를 인계자 정보의 `dept_notes` 로 넘긴다.

    {"department": "디지털전략부 웹서비스팀",
     "documents": [{"name": "웹 접근성 인증 신청서류", "location": "공유드라이브 …"}],
     "franchises": ["한울 제휴몰"],
     "licenses": [{"vendor": "한빛웹솔루션", "product": "홈페이지 CMS"}],
     "others": {"재무팀": {"documents": [...], "franchises": [...], "licenses": [...]}}}   # 협업 부서 정보(참고용)

이 정보는 인수인계서 항목의 근거가 아니다. 이 부서가 실제로 관리하는 대상의 목록이라서, 자료에서 각 대상이 어떻게 관리되는지 찾고
자료에 없는 대상은 인계자에게 '관리 방식'을 묻는 데 쓴다(위치·업체명처럼 이미 아는 정보는 다시 묻지 않는다).
"""

from __future__ import annotations

from typing import Any

MAX_ENTRIES = 12  # 종류별 항목 상한(프롬프트가 부풀지 않게)
MAX_OTHERS = 12


def _text(value: Any, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def _entries(raw: Any) -> dict[str, list]:
    """한 부서의 문서·가맹점·라이센스 목록을 정리한다."""
    raw = raw if isinstance(raw, dict) else {}
    documents = [{"name": _text(d.get("name")), "location": _text(d.get("location"), 200)} for d in raw.get("documents") or [] if isinstance(d, dict) and _text(d.get("name"))]
    franchises = [_text(f) for f in raw.get("franchises") or [] if isinstance(f, str) and _text(f)]
    licenses = [{"vendor": _text(l.get("vendor")), "product": _text(l.get("product"))} for l in raw.get("licenses") or [] if isinstance(l, dict) and _text(l.get("vendor"))]
    return {"documents": documents[:MAX_ENTRIES], "franchises": franchises[:MAX_ENTRIES], "licenses": licenses[:MAX_ENTRIES]}


def clean_dept_info(raw: dict[str, Any] | None) -> dict[str, Any]:
    """profile["dept_notes"] 를 정리한다. 이 부서에 정보가 하나도 없으면 documents·franchises·licenses 가 모두 빈 목록이다."""
    if not isinstance(raw, dict):
        return {"department": "", "documents": [], "franchises": [], "licenses": [], "others": {}}
    others = {}
    for name, value in list((raw.get("others") or {}).items())[:MAX_OTHERS] if isinstance(raw.get("others"), dict) else []:
        entries = _entries(value)
        if _text(name) and any(entries.values()):
            others[_text(name, 50)] = entries
    return {"department": _text(raw.get("department"), 100), **_entries(raw), "others": others}


def has_info(info: dict[str, Any]) -> bool:
    return bool(info["documents"] or info["franchises"] or info["licenses"])


def targets(info: dict[str, Any]) -> list[dict[str, str]]:
    """이 부서가 관리하는 대상(라이센스 업체·가맹점·중요 문서)마다 인계자에게 '관리 방식'을 확인하는 질문을 만든다. 위치·업체명은 이미 알기 때문에 묻지 않는다."""
    out = []
    for item in info["licenses"]:
        what = f"({item['product']})" if item["product"] else ""
        out.append({"kind": "license", "name": item["vendor"], "label": "라이센스 업체",
                    "question": f"라이센스 업체 '{item['vendor']}'{what}의 계약 기간과 갱신 시기, 담당자는 어떻게 되나요?"})
    for name in info["franchises"]:
        out.append({"kind": "franchise", "name": name, "label": "관리 가맹점",
                    "question": f"관리하시는 가맹점 '{name}'은 어떤 방식으로 관리하고, 담당자와 점검 주기는 어떻게 되나요?"})
    for item in info["documents"]:
        out.append({"kind": "document", "name": item["name"], "label": "중요 문서",
                    "question": f"중요 문서 '{item['name']}'의 최신본은 누가 관리하고, 어떤 주기로 갱신하나요?"})
    return out


def interleave(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """대상을 종류별로 번갈아(라이센스 → 가맹점 → 문서) 늘어놓는다."""
    groups = {"license": [], "franchise": [], "document": []}
    for item in items:
        groups[item["kind"]].append(item)
    out: list[dict[str, str]] = []
    while any(groups.values()):
        for kind in ("license", "franchise", "document"):
            if groups[kind]:
                out.append(groups[kind].pop(0))
    return out


def pick_questions(info: dict[str, Any], limit: int = 3) -> list[dict[str, str]]:
    """질문할 대상을 종류별로 번갈아 최대 limit 개 고른다."""
    return interleave(targets(info))[:limit]

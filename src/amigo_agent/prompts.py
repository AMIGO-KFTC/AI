"""프롬프트(한국어). 역할·원칙은 시스템 프롬프트에, 매번 바뀌는 자료는 사용자 메시지에 둔다(프롬프트 캐시 유지)."""

from __future__ import annotations

from typing import Any

from .links import link_names_block
from .dept_info import clean_dept_info, has_info, targets as dept_targets
from .state import Gap, SlotState, Turn
from .template import COVERAGE_LABELS, MODE_LABELS, SLOT_BY_KEY, SLOTS, SlotSpec, mode_of

ANALYSIS_SYSTEM = """\
당신은 공공·금융기관의 업무 인수인계서 작성을 돕는 분석가입니다. 인계자가 올린 업무 자료(문서·메일·링크)를 근거로 인수인계서의 한 장(章)을 채웁니다.

원칙
- 근거 자료에 적힌 사실만 기록합니다. 추측하거나 일반론으로 빈칸을 채우지 않습니다.
- 모든 항목에는 근거 ID(예: E3)를 1개 이상 붙입니다. 근거를 댈 수 없는 내용은 항목으로 만들지 말고 gaps 로 남깁니다.
- 같은 업무가 여러 자료에 흩어져 있으면 하나의 항목으로 합치고 근거 ID 를 모두 적습니다.
- 자료끼리 값이 다르면 근거 머리글의 유형·작성일을 보고 다음 순위로 하나만 고릅니다: 1) 인계자 답변 2) 작성일이 더 최근인 공식 문서 3) 회의록·메일 4) 날짜가 없는 참고자료. 같은 순위끼리는 시각이 더 최근인 자료를 따릅니다. 이긴 자료의 근거 ID 를 적고, 진 값은 쓰지 않습니다(충돌 처리는 인계자가 최종 검토 때 확인합니다).
- 자료에 이름만 나오고 세부 내용이 없는 대상(시스템, 과제, 부서, 업체, 담당자)은 search_documents 로 추가 검색해 연결 관계를 확인합니다. 검색은 필요할 때만 하고 최대 3번까지 합니다.
- 필드 값은 짧고 구체적으로 한 줄로 씁니다. 자료에 없으면 빈 문자열로 둡니다.
- 'references'(참고자료) 필드에는 그 업무를 처리할 때 봐야 하는 매뉴얼·규정·계약서·양식·관련 법령의 이름을 근거 자료에서 찾아 적습니다. 자료에 적힌 문서명 그대로(예: '운영매뉴얼 v3.2', '계약서 별첨 SLA 표')에 위치(파일명·쪽·시트)가 있으면 함께 쓰고, 여럿이면 '; '로 구분합니다. 자료에 그런 문서가 언급되지 않았으면 짐작해서 쓰지 말고 빈 문자열로 둡니다.
- '주요 결정사항', '후임자에게 바라는 것', '소개 및 배경', '목표', '수행 절차' 같은 서술형 필드는 자료에 그 내용이 직접 적혀 있을 때만 씁니다. 누가 무엇을 하기로 정했다는 말, 협의·합의했다는 말, 후임자에게 당부한다는 말, 배경·목표·절차는 자료에 있는 문장만 옮기고, 그럴듯하게 짐작해서 만들지 않습니다. 자료에 없으면 빈 문자열로 두고 gaps 로 남깁니다. 비워 두는 것이 지어내는 것보다 낫습니다.
- 진행 상태(요청했다, 협의했다, 완료했다, 확정됐다)는 자료의 표현 그대로 옮깁니다. 예정·초안·진행 중을 완료로 바꾸지 않습니다.
- 자료에 있는 숫자·날짜·기한·비중·금액·건수·이름·연락처는 해당 필드에 빠짐없이 그대로 옮깁니다. 표에 값이 있는데 비워 두지 않습니다.
- coverage: sufficient(인수자가 바로 업무를 이어받을 만큼 필수 정보가 갖춰짐), partial(일부 항목·필수 필드가 비어 있음), missing(관련 근거가 거의 없음).
- summary: 이 장에서 파악한 내용을 1~2문장으로 요약합니다.
- gaps: 인계자에게 물어볼 질문 후보입니다. 인수자가 업무를 이어받는 데 중요한 것부터 고릅니다.
  · 근거 자료에 이미 답이 있는 내용은 묻지 않습니다.
  · 인계자가 파일을 찾지 않고 기억·경험으로 바로 답할 수 있게 묻습니다(담당자 이름·연락처, 실제 처리 방법, 판단 기준, 주의할 점, 현재 진행 상황).
  · 질문 하나에는 정보 하나만 묻습니다. 한 대상의 '담당자와 연락처'는 하나로 보지만, 여러 부서·여러 항목·'완료 여부와 파일과 일정'처럼 여러 가지를 한 질문에 묶지 않습니다.
  · 자료에서 확인한 맥락을 짧게 언급해 무엇을 묻는지 바로 알 수 있게 쓰고, 한 문장으로 끝냅니다.
  · 계약서·매뉴얼처럼 파일로 받는 편이 나은 정보면 ask_for_document 를 true 로 하되, question 은 파일이 없어도 답할 수 있게 핵심 내용을 묻습니다(예: '계약 기간과 월 금액이 어떻게 되나요?').
- 업무 히스토리(주요 결정사항·협의사항)는 '무엇을 왜 그렇게 결정했는지'와 근거(메일·메신저·회의록)가 드러나게 씁니다. 자료에 없으면 gaps 로 남깁니다.
- 질문은 인계자가 당시 상황을 떠올릴 수 있게 구체적인 업무·시점·상대를 들어 묻습니다(예: '차액 정산 방식을 지금처럼 정하기까지 어떤 논의가 있었나요?').
- 시스템 ID·비밀번호·토큰 같은 민감 정보는 기록하지 않고 질문하지도 않습니다. 시스템 이름과 권한 신청 방법만 다룹니다.
- 모든 문장은 한국어 존댓말로 씁니다.

"""

INTERPRET_SYSTEM = """\
당신은 업무 인수인계 인터뷰를 진행하는 AI 도우미입니다. 인계자의 답변을 해석해 인수인계서 항목을 갱신합니다.

원칙
- 인계자가 말한 사실만 기록하고, 말하지 않은 내용을 지어내지 않습니다.
- intent: answer(새 정보 제공), confirm(직전에 정리한 내용이 맞다고 함), correct(직전에 정리한 내용을 고침), skip(모름·건너뛰기), finish(문서 생성·종료 요청), question(인계자가 되묻거나 의미를 확인함), other(그 밖의 말).
- 답변이 자료와 다르거나 자료를 확인해야 하면 search_documents 로 확인합니다. 내용이 다르면 reply 에서 정중하게 어느 쪽이 맞는지 확인을 요청합니다.
- updates: 기존 항목을 고치면 그 item_id 를 쓰고, 새 항목이면 item_id 를 빈 문자열로 둡니다. fields 에는 해당 장의 필드 키만 씁니다. 검색 결과를 근거로 썼으면 evidence_ids 에 E 번호를 적습니다.
- resolved_gap_ids: 이번 답변으로 해소된 질문 ID(현재 질문 외에 함께 해소된 것도 포함).
- reply: 인계자에게 보낼 메시지(존댓말, 4문장 이내).
  · answer/correct 이고 updates 가 있으면 정리한 내용을 항목별로 짧게 요약하고 "맞으면 '네', 고칠 부분이 있으면 말씀해 주세요."로 끝냅니다(교차 확인).
  · question 이면 질문에 답한 뒤 원래 질문을 짧게 다시 묻습니다.
  · 그 밖에는 짧게 응답합니다. 새 질문을 덧붙이지 않습니다(한 번에 하나의 질문 원칙).
"""

PLAN_SYSTEM = """\
당신은 업무 인수인계 인터뷰를 설계합니다. 장(章)별 분석에서 나온 질문 후보를 받아, 인계자에게 물을 순서를 정합니다.
- duplicates: 사실상 같은 정보를 묻는 질문 쌍입니다(장이 달라도 같은 답이 나오면 중복). 더 잘 쓰인 쪽을 keep_id, 다른 쪽을 drop_id 로 적습니다. 묻는 대상이나 정보가 다르면 중복이 아닙니다.
- order: 남길 질문 id 를 인수자에게 중요한 순서로 모두 나열합니다. 다음 순서로 판단합니다.
  1) 인계일 전후로 기한이 닥친 일과 당장 연락해야 할 사람(장애·마감 대응)
  2) 업무를 이어받는 데 꼭 필요한 권한·계정·처리 방법
  3) 진행 중인 과제의 현재 상태와 다음 할 일
  4) 그 밖의 배경 정보
- 팀 전용 프롬프트가 있으면 그 관점과 우선순위를 순서에 반영합니다.
- 질문 문장은 바꾸지 않습니다.
"""

SUMMARY_SYSTEM = """\
당신은 업무 인수인계 도우미입니다. 자료 분석 결과를 인계자에게 알려 주는 짧은 안내문을 씁니다.
- 2~3문장, 존댓말. 무엇을 파악했고 어떤 부분이 부족한지 구체적으로(업무·시스템 이름을 들어) 말합니다.
- 제공된 분석 결과에 없는 내용은 쓰지 않습니다. 질문은 하지 않습니다.
"""


def profile_block(profile: dict[str, Any]) -> str:
    rows = [
        ("단위 업무명", profile.get("task_name")),
        ("성명/직책", " ".join(x for x in (profile.get("name"), profile.get("position")) if x)),
        ("소속", profile.get("organization")),
        ("인계 유형", MODE_LABELS[mode_of(profile)]),
        ("담당 업무(인계자 입력)", profile.get("duties")),
        ("인수자", profile.get("successor")),
        ("인계 예정일", profile.get("handover_date")),
    ]
    return "\n".join(f"- {k}: {v}" for k, v in rows if v)


# 팀 관리자가 정한 지침의 상한. BackEnd 가 저장할 때도 같은 한도로 검증하지만, 프롬프트가 부풀지 않도록 여기서도 자른다.
MAX_TEAM_ITEMS = 30
MAX_TEAM_ITEM_NAME = 100
MAX_TEAM_ITEM_INSTRUCTION = 500
MAX_TEAM_NOTES = 2000
MAX_TEAM_PROMPT = 2000  # 팀 전용 프롬프트(AI 가 항상 따라야 하는 역할과 판단 기준)


def clean_team_guidelines(guidelines: dict[str, Any] | None) -> dict[str, Any]:
    """팀 지침(profile["team_guidelines"])을 정리한다: 이름이 없는 항목·빈 값을 버리고 길이를 자른다."""
    if not isinstance(guidelines, dict):
        return {"items": [], "notes": "", "prompt": ""}
    items: list[dict[str, Any]] = []
    for raw in guidelines.get("items") or []:
        if not isinstance(raw, dict):
            continue
        name = " ".join(str(raw.get("name") or "").split())[:MAX_TEAM_ITEM_NAME]
        if not name:
            continue
        instruction = " ".join(str(raw.get("instruction") or "").split())[:MAX_TEAM_ITEM_INSTRUCTION]
        items.append({"name": name, "instruction": instruction})
    notes = str(guidelines.get("notes") or "").strip()[:MAX_TEAM_NOTES]
    prompt = str(guidelines.get("prompt") or "").strip()[:MAX_TEAM_PROMPT]
    return {"items": items[:MAX_TEAM_ITEMS], "notes": notes, "prompt": prompt}


def team_prompt_block(guidelines: dict[str, Any] | None) -> str:
    """팀 전용 프롬프트만(질문 순서 정하기·안내문 쓰기용). 비어 있으면 빈 문자열."""
    prompt = clean_team_guidelines(guidelines)["prompt"]
    if not prompt:
        return ""
    return (
        "## 팀 전용 프롬프트 (팀 관리자가 정한 역할과 판단 기준)\n" + prompt
        + "\n이 프롬프트의 관점과 우선순위를 따릅니다. 시스템 원칙(근거·민감 정보)과 부딪치면 시스템 원칙을 따릅니다."
    )


def team_guidelines_block(guidelines: dict[str, Any] | None, *, slot_key: str = "") -> str:
    """팀 관리자가 정한 '우리 팀 인수인계서 지침'을 프롬프트 한 구간으로 만든다. 지침이 비어 있으면 빈 문자열.

    - slot_key 가 있으면 그 장을 분석하는 프롬프트용(해당하는 항목만 다루고, 부서별 추가 자료 장에서는 모든 항목을 살핀다).
    - slot_key 가 없으면 인계자와 대화하는 프롬프트용(답변을 정리할 때 참고).
    항목은 '자료에 있으면 이 이름으로 빠짐없이 정리하라'는 안내일 뿐이다. 자료에 없으면 비워 두고 그것 때문에 질문을 만들지 않는다.
    지침은 '무엇을 찾아 담을지'를 정할 뿐 사실의 근거가 아니다. 근거·민감 정보 원칙은 지침으로 바뀌지 않음을 블록 안에서 밝힌다.
    """
    data = clean_team_guidelines(guidelines)
    if not data["items"] and not data["notes"] and not data["prompt"]:
        return ""
    lines = [
        "## 우리 팀 인수인계서 지침 (팀 관리자가 정한 내용)",
        "이 지침은 무엇을 찾아 담고 어떻게 쓸지 정하는 것입니다. 사실의 근거가 아니므로, 항목의 내용은 여전히 근거 자료와 인계자 답변에서만 가져옵니다. "
        "근거 ID 를 붙이는 원칙과 시스템 ID·비밀번호 같은 민감 정보를 다루지 않는 원칙은 이 지침으로 바뀌지 않습니다.",
    ]
    if data["prompt"]:
        lines.append("")
        lines.append("### 팀 전용 프롬프트 (팀 관리자가 정한 역할과 판단 기준)")
        lines.append(data["prompt"])
        lines.append(
            "위 프롬프트는 자료를 분석하고 정리하는 데만 쓰지 않고, 인계자에게 던지는 질문(gaps)에도 적용합니다. "
            "무엇을 먼저·중요하게 물을지, 어떤 관점과 표현으로 물을지, 꼭 확인할 사항을 위 프롬프트에 맞춥니다. 위 프롬프트와 시스템 원칙(근거·민감 정보)이 부딪치면 시스템 원칙을 따릅니다."
        )
    if data["items"]:
        lines.append("")
        lines.append("### 우리 팀 인수인계서에 담는 항목")
        for item in data["items"]:
            hint = f" — {item['instruction']}" if item["instruction"] else ""
            lines.append(f"- {item['name']}{hint}")
        if slot_key == "dept_notes":
            lines.append(
                "이 장(부서별 추가 자료)에서는 위 항목을 살핍니다. 자료에서 찾은 항목만 항목 이름을 '추가 자료'로, 작성 안내에 맞춰 찾은 내용을 '관리 내용'으로 적고 근거 ID 를 붙입니다. "
                "자료에 없는 항목은 만들지 않고, 그 항목만을 위한 질문(gaps)도 만들지 않습니다."
            )
        elif slot_key:
            lines.append(
                "이 장의 성격에 맞는 항목만 다룹니다. 이 장과 관련 없는 항목은 무시하세요(다른 장 분석에서 처리합니다). "
                "해당하는 내용을 자료에서 찾으면 근거 ID 와 함께 이 장의 항목·필드에 넣습니다. 자료에 없으면 그대로 두고, 그 항목만을 위한 질문(gaps)은 만들지 않습니다."
            )
        else:
            lines.append(
                "인계자가 말한 내용이 위 항목에 해당하면 놓치지 않고 그 항목에 맞춰 정리합니다. "
                "위 항목이 비어 있다는 이유만으로 새 질문을 하지는 않습니다."
            )
    if data["notes"]:
        lines.append("")
        lines.append("### 우리 팀의 특징·작성 지침")
        lines.append(data["notes"])
    return "\n".join(lines)


# 부서별 특이사항(사내 문서의 요약): 부서 / 중요문서 위치 / 관리 가맹점 / 라이센스 업체. 정보일 뿐 사실의 근거가 아니다(dept_info.py).
COLLABORATION_SLOTS = ("stakeholders", "irregular", "regular", "leave_tasks")


def _info_lines(info: dict[str, Any], *, with_location: bool = True) -> list[str]:
    lines = []
    if info["documents"]:
        docs = "; ".join(f"{d['name']}({d['location']})" if with_location and d["location"] else d["name"] for d in info["documents"])
        lines.append(f"- 중요문서 위치: {docs}")
    lines.append("- 관리 가맹점: " + ("; ".join(info["franchises"]) if info["franchises"] else "없음"))
    lines.append("- 라이센스 업체: " + ("; ".join(f"{l['vendor']}({l['product']})" if l["product"] else l["vendor"] for l in info["licenses"]) if info["licenses"] else "없음"))
    return lines


def dept_notes_block(profile: dict[str, Any], *, slot_key: str = "", purpose: str = "") -> str:
    """소속 부서의 특이사항(중요문서 위치·관리 가맹점·라이센스 업체)을 프롬프트 한 구간으로 만든다. 정보가 없으면 빈 문자열.

    - slot_key == "dept_notes": 이 장에서 각 대상(라이센스 업체·가맹점·중요 문서)이 자료에서 어떻게 관리되는지 찾고, 자료에 없으면 '관리 방식'을 인계자에게 묻는다.
    - 그 밖의 장(slot_key): 관련 있는 정보만 분석·질문에 참고한다. 협업 장에서는 협업 부서 정보도 함께 준다.
    - purpose == "plan": 질문 순서 프롬프트용(이름만, 같은 대상을 묻는 질문은 중복으로 본다).
    - 그 밖(slot_key 없음): 인계자와 대화하는 프롬프트용(답변을 이 정보의 대상 이름으로 정리).
    이 정보는 사실의 근거가 아니다. 근거 ID·민감 정보 원칙은 바뀌지 않는다.
    """
    info = clean_dept_info((profile or {}).get("dept_notes"))
    if not has_info(info):
        return ""
    lines = [
        f"## 우리 부서 특이사항 ({info['department'] or profile.get('organization', '')})",
        "사내 부서별 특이사항 문서의 요약으로, 이 부서가 실제로 관리하는 대상의 목록입니다. 정보 자체는 인수인계서 항목의 근거가 아닙니다. "
        "항목의 내용은 여전히 근거 자료와 인계자 답변에서만 가져오고, 근거 ID 를 붙이는 원칙과 시스템 ID·비밀번호 같은 민감 정보를 다루지 않는 원칙은 바뀌지 않습니다.",
    ]
    if purpose == "plan":
        names = [t["name"] for t in dept_targets(info)]
        lines.append("관리 대상: " + ", ".join(names))
        lines.append("이 대상들의 관리 방식을 확인하는 질문은 인수자가 부서 업무를 이어받는 데 필요한 정보이므로 순서를 정할 때 가볍게 보지 않습니다. 같은 대상을 묻는 질문끼리는 중복으로 봅니다.")
        return "\n".join(lines)
    lines += _info_lines(info)
    if slot_key == "dept_notes":
        lines.append(
            "\n이 장(부서별 추가 자료)에서는 위 대상(라이센스 업체·관리 가맹점·중요 문서) 각각이 근거 자료에서 실제로 어떻게 관리되는지 찾습니다. "
            "자료에서 찾은 대상은 대상 이름을 '추가 자료'로, 자료에 적힌 구체적인 내용(담당자·계약 기간·갱신 시기·점검 주기·관리 방식)을 '관리 내용'으로 적고 근거 ID 를 붙입니다. 위 목록의 이름과 위치만 옮겨 적지 않습니다. "
            "자료에 관리 내용이 없는 대상은 인계자에게 확인할 질문(gaps)으로 만듭니다. 이 장의 gaps 는 이런 대상의 '관리 방식'을 묻는 질문으로만 채우고(대상과 무관한 일반 질문은 이 장에서 만들지 않습니다), "
            "업체·가맹점·문서 이름을 넣어 구체적으로 묻습니다. 업체 이름이나 문서 위치처럼 위 목록에 이미 있는 정보는 다시 묻지 않습니다. 대상이 여럿이면 인수인계에 더 중요한 것부터 gaps 한도까지 만듭니다."
        )
    elif slot_key:
        lines.append("\n이 장과 관련된 대상이 자료나 대화에 나오면 위 정보를 참고해 분석에 반영하세요(예: 같은 업체·문서). 관련 없는 정보는 무시하세요(부서별 추가 자료 장에서 다룹니다). 목록에 있다는 이유만으로 자료에 없는 내용을 사실로 쓰지 않습니다.")
    else:
        lines.append(
            "\n인계자의 답변이 위 대상(라이센스 업체·관리 가맹점·중요 문서)의 관리 방식에 해당하면, 대상 이름을 '추가 자료'로·답변 내용을 '관리 내용'으로 해 부서별 추가 자료(dept_notes) 장의 항목으로 updates 에 담고 그 질문(gap)도 해소로 처리합니다. "
            "답변이 위 정보와 어긋나면(예: 업체가 바뀌었다, 문서 위치가 다르다) reply 에서 정중하게 어느 쪽이 맞는지 확인합니다. 새 질문은 덧붙이지 않습니다."
        )
    if slot_key in COLLABORATION_SLOTS or (not slot_key and not purpose):
        if info["others"]:
            lines.append("\n### 협업 부서 정보(참고)")
            for name, entries in info["others"].items():
                parts = [*(f"문서 {d['name']}({d['location']})" if d["location"] else f"문서 {d['name']}" for d in entries["documents"]),
                         *(f"가맹점 {f}" for f in entries["franchises"]), *(f"라이센스 {l['vendor']}" for l in entries["licenses"])]
                lines.append(f"- {name}: " + ", ".join(parts))
            lines.append("협의 상대·협업 내용이 위 부서이면 협의내용을 정리하거나 질문할 때 참고합니다.")
    return "\n".join(lines)


def slot_spec_block(spec: SlotSpec) -> str:
    fields = "\n".join(f"- {f.key}({f.label}){': ' + f.hint if f.hint else ''}" for f in spec.fields)
    return (
        f"## 작성할 장: {spec.title}\n{spec.description}\n\n필드:\n{fields}\n"
        f"필수 필드: {', '.join(spec.required)} / 최소 항목 수: {spec.min_items}"
    )


def analysis_user(spec: SlotSpec, profile: dict[str, Any], evidence_text: str, max_gaps: int) -> str:
    team = team_guidelines_block(profile.get("team_guidelines"), slot_key=spec.key)
    dept = dept_notes_block(profile, slot_key=spec.key)
    links = link_names_block(profile["_link_slots"], spec) if profile.get("_link_slots") is not None else ""
    team_section = (f"{team}\n\n" if team else "") + (f"{dept}\n\n" if dept else "") + (f"{links}\n\n" if links else "")
    evidence = evidence_text or "(검색된 근거가 없습니다. 필요하면 search_documents 로 직접 찾아보세요.)"
    return (
        f"## 인계자 정보\n{profile_block(profile)}\n\n{slot_spec_block(spec)}\n\n{team_section}"
        f"## 근거 자료(검색 결과)\n{evidence}\n\n"
        f"위 근거와 필요하면 추가 검색 결과를 바탕으로 '{spec.title}' 장을 작성하세요. gaps 는 최대 {max_gaps}개입니다."
    )


def items_block(slots: dict[str, SlotState], keys: list[str] | None = None) -> str:
    lines = []
    for spec in SLOTS:
        if (keys and spec.key not in keys) or spec.key not in slots:
            continue
        slot = slots.get(spec.key) or {}
        coverage = COVERAGE_LABELS.get(slot.get("coverage", "missing"), "")
        lines.append(f"### {spec.title} [{spec.key}] - {coverage}")
        for item in slot.get("items", []):
            fields = ", ".join(f"{k}={v}" for k, v in (item.get("fields") or {}).items() if v)
            flag = "" if item.get("origin") == "document" else " (인계자 답변)"
            lines.append(f"- id={item.get('id')} | {item.get('title')}{flag} | {fields}")
        if not slot.get("items"):
            lines.append("- (항목 없음)")
    return "\n".join(lines)


def transcript_block(transcript: list[Turn], limit: int = 8) -> str:
    recent = transcript[-limit:]
    out = []
    for t in recent:
        who = "AI" if t.get("role") == "assistant" else "인계자"
        content = (t.get("content") or "").strip()
        if len(content) > 600:
            content = content[:600] + " …"
        out.append(f"{who}: {content}")
    return "\n".join(out) or "(대화 없음)"


def gaps_block(gaps: list[Gap]) -> str:
    if not gaps:
        return "(남은 질문 없음)"
    return "\n".join(f"- {g['id']} [{SLOT_BY_KEY[g['slot']].title}] {g.get('description', '')}" for g in gaps[:12])


def interpret_user(
    *,
    profile: dict[str, Any],
    stage: str,
    current_gap: Gap | None,
    pending_text: str,
    slots: dict[str, SlotState],
    open_gaps: list[Gap],
    transcript: list[Turn],
    message: str,
) -> str:
    if current_gap:
        gap_text = (
            f"- id: {current_gap['id']}\n- 장: {SLOT_BY_KEY[current_gap['slot']].title} [{current_gap['slot']}]\n"
            f"- 관련 항목 id: {current_gap.get('item_id') or '(없음)'}\n- 질문: {current_gap.get('question', '')}"
        )
        if current_gap.get("reopened"):
            gap_text += "\n- (참고) 인계자가 이미 답했던 질문을 다시 열었습니다. 새 메시지는 앞의 답을 고치는 답변이므로, 기존 값과 다르면 새 값으로 updates 에 담으세요."
    else:
        gap_text = "(현재 질문 없음)"
    team = team_guidelines_block(profile.get("team_guidelines"))
    dept = dept_notes_block(profile)
    team_section = (f"{team}\n\n" if team else "") + (f"{dept}\n\n" if dept else "")
    stage_note = "문서 초안을 검토하는 단계입니다. 인계자의 수정 요청을 항목에 반영하세요." if stage == "review" else "질의응답 단계입니다."
    return (
        f"## 인계자 정보\n{profile_block(profile)}\n\n{team_section}## 진행 단계\n{stage_note}\n\n"
        f"## 현재 질문\n{gap_text}\n\n## 확인 대기 중인 정리 내용\n{pending_text or '(없음)'}\n\n"
        f"## 현재까지 정리된 항목\n{items_block(slots)}\n\n## 남은 질문\n{gaps_block(open_gaps)}\n\n"
        f"## 최근 대화\n{transcript_block(transcript)}\n\n## 인계자의 새 메시지\n{message}"
    )


def plan_user(profile: dict[str, Any], gaps: list[Gap]) -> str:
    lines = [f"- {g['id']} [{SLOT_BY_KEY[g['slot']].title}] {g.get('question', '')}" for g in gaps]
    dept = dept_notes_block(profile, purpose="plan")
    team = team_prompt_block(profile.get("team_guidelines"))
    sections = "".join(f"{block}\n\n" for block in (team, dept) if block)
    return f"## 인계자 정보\n{profile_block(profile)}\n\n{sections}## 질문 후보\n" + "\n".join(lines)


def summary_user(profile: dict[str, Any], slots: dict[str, SlotState], sources: list[dict]) -> str:
    names = ", ".join(s.get("name", "") for s in sources[:12]) or "(자료 목록 없음)"
    lines = []
    for spec in SLOTS:
        if spec.key not in slots:
            continue
        slot = slots.get(spec.key) or {}
        coverage = COVERAGE_LABELS.get(slot.get("coverage", "missing"), "")
        lines.append(f"- {spec.title}: {coverage} / {slot.get('summary', '')}")
    team = team_prompt_block(profile.get("team_guidelines"))
    team_section = f"{team}\n\n" if team else ""
    return f"## 인계자 정보\n{profile_block(profile)}\n\n{team_section}## 분석한 자료\n{names}\n\n## 장별 분석 결과\n" + "\n".join(lines)

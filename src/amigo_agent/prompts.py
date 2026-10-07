"""프롬프트(한국어). 역할·원칙은 시스템 프롬프트에, 매번 바뀌는 자료는 사용자 메시지에 둔다(프롬프트 캐시 유지)."""

from __future__ import annotations

from typing import Any

from .state import Gap, SlotState, Turn
from .template import COVERAGE_LABELS, SLOT_BY_KEY, SLOTS, SlotSpec

ANALYSIS_SYSTEM = """\
당신은 공공·금융기관의 업무 인수인계서 작성을 돕는 분석가입니다. 인계자가 올린 업무 자료(문서·메일·링크)를 근거로 인수인계서의 한 장(章)을 채웁니다.

원칙
- 근거 자료에 적힌 사실만 기록합니다. 추측하거나 일반론으로 빈칸을 채우지 않습니다.
- 모든 항목에는 근거 ID(예: E3)를 1개 이상 붙입니다. 근거를 댈 수 없는 내용은 항목으로 만들지 말고 gaps 로 남깁니다.
- 같은 업무가 여러 자료에 흩어져 있으면 하나의 항목으로 합치고 근거 ID 를 모두 적습니다.
- 자료에 이름만 나오고 세부 내용이 없는 대상(시스템, 과제, 부서, 업체, 담당자)은 search_documents 로 추가 검색해 연결 관계를 확인합니다. 검색은 필요할 때만 하고 최대 3번까지 합니다.
- 필드 값은 짧고 구체적으로 한 줄로 씁니다. 자료에 없으면 빈 문자열로 둡니다.
- 자료에 있는 숫자·날짜·기한·비중·금액·건수·이름·연락처는 해당 필드에 빠짐없이 그대로 옮깁니다. 표에 값이 있는데 비워 두지 않습니다.
- coverage: sufficient(인수자가 바로 업무를 이어받을 만큼 필수 정보가 갖춰짐), partial(일부 항목·필수 필드가 비어 있음), missing(관련 근거가 거의 없음).
- summary: 이 장에서 파악한 내용을 1~2문장으로 요약합니다.
- gaps: 인계자에게 물어볼 질문 후보입니다. 인수자가 업무를 이어받는 데 중요한 것부터 고릅니다.
  · 근거 자료에 이미 답이 있는 내용은 묻지 않습니다.
  · 인계자가 파일을 찾지 않고 기억·경험으로 바로 답할 수 있게 묻습니다(담당자 이름·연락처, 실제 처리 방법, 판단 기준, 주의할 점, 현재 진행 상황).
  · 질문 하나에는 정보 하나만 묻습니다. 한 대상의 '담당자와 연락처'는 하나로 보지만, 여러 부서·여러 항목·'완료 여부와 파일과 일정'처럼 여러 가지를 한 질문에 묶지 않습니다.
  · 자료에서 확인한 맥락을 짧게 언급해 무엇을 묻는지 바로 알 수 있게 쓰고, 한 문장으로 끝냅니다.
  · 계약서·매뉴얼처럼 파일로 받는 편이 나은 정보면 ask_for_document 를 true 로 하되, question 은 파일이 없어도 답할 수 있게 핵심 내용을 묻습니다(예: '계약 기간과 월 금액이 어떻게 되나요?').
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
- 질문 문장은 바꾸지 않습니다.
"""

SUMMARY_SYSTEM = """\
당신은 업무 인수인계 도우미입니다. 자료 분석 결과를 인계자에게 알려 주는 짧은 안내문을 씁니다.
- 2~3문장, 존댓말. 무엇을 파악했고 어떤 부분이 부족한지 구체적으로(업무·시스템 이름을 들어) 말합니다.
- 제공된 분석 결과에 없는 내용은 쓰지 않습니다. 질문은 하지 않습니다.
"""

COMPOSE_SYSTEM = """\
당신은 업무 인수인계서 편집자입니다. 정리된 항목을 바탕으로 문서 앞부분에 들어갈 '업무 개요'와 '인수자 핵심 당부사항'을 씁니다.
- overview: 3~5문장. 인계자의 역할과 업무 범위, 현재 진행 상황을 요약합니다.
- key_points: 인수자가 첫 한 달 안에 반드시 챙길 일 3~5개. 한 줄씩, 기한·대상을 포함합니다.
- 제공된 항목에 없는 내용은 쓰지 않습니다. 존댓말이 아닌 평서형(~함, ~필요)으로 씁니다.
"""


def profile_block(profile: dict[str, Any]) -> str:
    rows = [
        ("성명/직책", " ".join(x for x in (profile.get("name"), profile.get("position")) if x)),
        ("소속", profile.get("organization")),
        ("담당 업무(인계자 입력)", profile.get("duties")),
        ("인수자", profile.get("successor")),
        ("인계 예정일", profile.get("handover_date")),
    ]
    return "\n".join(f"- {k}: {v}" for k, v in rows if v)


def slot_spec_block(spec: SlotSpec) -> str:
    fields = "\n".join(f"- {f.key}({f.label}){': ' + f.hint if f.hint else ''}" for f in spec.fields)
    return (
        f"## 작성할 장: {spec.title}\n{spec.description}\n\n필드:\n{fields}\n"
        f"필수 필드: {', '.join(spec.required)} / 최소 항목 수: {spec.min_items}"
    )


def analysis_user(spec: SlotSpec, profile: dict[str, Any], evidence_text: str, max_gaps: int) -> str:
    evidence = evidence_text or "(검색된 근거가 없습니다. 필요하면 search_documents 로 직접 찾아보세요.)"
    return (
        f"## 인계자 정보\n{profile_block(profile)}\n\n{slot_spec_block(spec)}\n\n"
        f"## 근거 자료(검색 결과)\n{evidence}\n\n"
        f"위 근거와 필요하면 추가 검색 결과를 바탕으로 '{spec.title}' 장을 작성하세요. gaps 는 최대 {max_gaps}개입니다."
    )


def items_block(slots: dict[str, SlotState], keys: list[str] | None = None) -> str:
    lines = []
    for spec in SLOTS:
        if keys and spec.key not in keys:
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
    else:
        gap_text = "(현재 질문 없음)"
    stage_note = "문서 초안을 검토하는 단계입니다. 인계자의 수정 요청을 항목에 반영하세요." if stage == "review" else "질의응답 단계입니다."
    return (
        f"## 인계자 정보\n{profile_block(profile)}\n\n## 진행 단계\n{stage_note}\n\n"
        f"## 현재 질문\n{gap_text}\n\n## 확인 대기 중인 정리 내용\n{pending_text or '(없음)'}\n\n"
        f"## 현재까지 정리된 항목\n{items_block(slots)}\n\n## 남은 질문\n{gaps_block(open_gaps)}\n\n"
        f"## 최근 대화\n{transcript_block(transcript)}\n\n## 인계자의 새 메시지\n{message}"
    )


def plan_user(profile: dict[str, Any], gaps: list[Gap]) -> str:
    lines = [f"- {g['id']} [{SLOT_BY_KEY[g['slot']].title}] {g.get('question', '')}" for g in gaps]
    return f"## 인계자 정보\n{profile_block(profile)}\n\n## 질문 후보\n" + "\n".join(lines)


def summary_user(profile: dict[str, Any], slots: dict[str, SlotState], sources: list[dict]) -> str:
    names = ", ".join(s.get("name", "") for s in sources[:12]) or "(자료 목록 없음)"
    lines = []
    for spec in SLOTS:
        slot = slots.get(spec.key) or {}
        coverage = COVERAGE_LABELS.get(slot.get("coverage", "missing"), "")
        lines.append(f"- {spec.title}: {coverage} / {slot.get('summary', '')}")
    return f"## 인계자 정보\n{profile_block(profile)}\n\n## 분석한 자료\n{names}\n\n## 장별 분석 결과\n" + "\n".join(lines)


def compose_user(profile: dict[str, Any], slots: dict[str, SlotState]) -> str:
    return f"## 인계자 정보\n{profile_block(profile)}\n\n## 정리된 항목\n{items_block(slots)}"

"""프롬프트(한국어). 역할·원칙은 시스템 프롬프트에, 매번 바뀌는 자료는 사용자 메시지에 둔다(프롬프트 캐시 유지)."""

from __future__ import annotations

from typing import Any

from .field_guides import guide_for
from .links import link_names_block
from .dept_info import clean_dept_info, has_info, numbered, targets as dept_targets
from .state import Gap, SlotState, Turn
from .template import ASK_AUTO, ASK_AUX, ASK_HIDDEN, COVERAGE_LABELS, MODE_LABELS, SLOT_BY_KEY, SLOTS, SlotSpec, leave_period, mode_of, profile_units, slots_for

ANALYSIS_SYSTEM = """\
당신은 공공·금융기관의 업무 인수인계서 작성을 돕는 분석가입니다. 인계자가 올린 업무 자료(문서·메일·링크)를 근거로 인수인계서의 한 장(章)을 채웁니다.

원칙
- 근거 자료에 적힌 사실만 기록합니다. 추측하거나 일반론으로 빈칸을 채우지 않습니다.
- 모든 항목에는 근거 ID(예: E3)를 1개 이상 붙입니다. 근거를 댈 수 없는 내용은 항목으로 만들지 말고 gaps 로 남깁니다.
- 같은 업무가 여러 자료에 흩어져 있으면 하나의 항목으로 합치고 근거 ID 를 모두 적습니다.
- 자료끼리 값이 다르면 근거 머리글의 유형·작성일을 보고 다음 순위로 하나만 고릅니다: 1) 인계자 답변 2) 작성일이 더 최근인 공식 문서 3) 회의록·메일 4) 날짜가 없는 참고자료. 같은 순위끼리는 시각이 더 최근인 자료를 따릅니다. 이긴 자료의 근거 ID 를 적고, 진 값은 쓰지 않습니다(충돌 처리는 인계자가 최종 검토 때 확인합니다).
- 자료에 이름만 나오고 세부 내용이 없는 대상(시스템, 과제, 부서, 업체, 담당자)은 search_documents 로 추가 검색해 연결 관계를 확인합니다. 검색은 필요할 때만 하고 최대 3번까지 합니다.
- 필드 값은 그 필드의 작성 예시 형식(구조·순서·번호·'수행 주기:'·'주요 의사결정: / 판단 근거:' 같은 라벨)을 따라 구체적으로 씁니다. 여러 단계·사례는 ' / '로 이어 한 줄에 씁니다. 자료에 없으면 빈 문자열로 둡니다.
- 필드 값(인수인계서에 들어가는 내용)의 문체는 개괄식 명사형 종결입니다: '~함.', '~임.', '~예정.', '~중.'(예: '매월 5영업일 이내 실적 검수 후 재무팀에 지급 요청함.', '정상 운영 중임.'). '~합니다·~입니다·~해요' 같은 존댓말 문장으로 쓰지 않습니다. 작성 예시의 말투가 존댓말이어도 형식만 따르고 문체는 개괄식으로 씁니다.
- 지라 이슈 키('[LGL-3]', 'AMG-12'처럼 프로젝트 약자-번호로 된 꼬리표)는 필드 값에 쓰지 않습니다. 근거 자료의 제목·표에 붙어 있어도 옮기지 않고 이슈의 내용만 풀어 쓰며, 어느 자료에서 왔는지는 근거 ID 로만 밝힙니다.
- 세부사항(정보 추출 기준) 중 '(필수)'가 붙은 것은 노션 양식에서 굵은 글씨로 표시된, 반드시 들어가야 하는 내용입니다. 자료에 있으면 필드 값에 빠짐없이 넣고, 자료에서 찾지 못하면 반드시 그 세부사항을 묻는 질문(gaps, element 에 그 세부사항 이름)을 만듭니다(필수 질문). '필수 항목'으로 표시된 필드·장은 비워 둘 수 없습니다.
- 정기업무와 장기휴가 세부 업무의 'references'(기타) 필드는 아래 다섯 줄 양식으로, 근거 자료에서 확인된 줄만 적고 해당 사항이 없는 줄은 통째로 뺍니다. 줄마다 줄바꿈(\n)으로 나눕니다.
  관련 법령·규정: (법령·규정·지침 이름과 조항, 예: ○○법 제○조, ○○업무처리지침)
  업무 매뉴얼: (매뉴얼 이름과 판, 예: ○○업무 표준매뉴얼(2026년 개정판))
  주요 참고자료: (계획서·보고서·계약서·양식 등 문서명 그대로, 여럿이면 ', '로 구분)
  자료 위치: (파일명·폴더 경로·쪽·시트, 예: 사내 공유폴더 > 기획팀 > 사업계획)
  활용 팁: (자료를 쓸 때 도움이 되는 요령, 근거 자료에 있을 때만)
  자료에 이런 내용이 하나도 없으면 짐작해서 쓰지 말고 빈 문자열로 둡니다.
- '주요 결정사항', '후임자에게 바라는 것', '소개 및 배경', '목표', '수행 절차' 같은 서술형 필드는 자료에 그 내용이 직접 적혀 있을 때만 씁니다. 누가 무엇을 하기로 정했다는 말, 협의·합의했다는 말, 후임자에게 당부한다는 말, 배경·목표·절차는 자료에 있는 문장만 옮기고, 그럴듯하게 짐작해서 만들지 않습니다. 자료에 없으면 빈 문자열로 두고 gaps 로 남깁니다. 비워 두는 것이 지어내는 것보다 낫습니다.
- 진행 상태(요청했다, 협의했다, 완료했다, 확정됐다)는 자료의 표현 그대로 옮깁니다. 예정·초안·진행 중을 완료로 바꾸지 않습니다.
- 자료에 있는 숫자·날짜·기한·비중·금액·건수·이름·연락처는 해당 필드에 빠짐없이 그대로 옮깁니다. 표에 값이 있는데 비워 두지 않습니다.
- coverage: sufficient(인수자가 바로 업무를 이어받을 만큼 필수 정보가 갖춰짐), partial(일부 항목·필수 필드가 비어 있음), missing(관련 근거가 거의 없음).
- summary: 이 장에서 파악한 내용을 1~2문장으로 요약합니다.
- gaps: 인계자에게 물어볼 질문입니다. 인수인계서 양식(이 장의 필드)에서 자료로 채우지 못한 칸이나 칸 안의 빠진 세부사항만 묻습니다. 양식에 없는 내용, 이미 채운 칸, 칸과 무관한 궁금증은 묻지 않습니다.
  · 질문마다 related_item_title 에 그 항목 제목을, field 에 필드 키를 정확히 씁니다. 한 질문은 한 칸, 한 가지 세부사항(암묵지 하나)만 묻습니다.
  · 1질문 1암묵지: 암묵지의 단위는 필드가 아니라 필드에 들어갈 세부사항(작성 기준의 '세부사항(정보 추출 기준)': 무엇을·왜·기한·판단 근거 등)입니다. 칸에 일부만 채웠으면 빠진 세부사항마다 질문을 하나씩 만들고 element 에 그 세부사항 이름을 목록의 글자 그대로 씁니다('(필수)'·'(보조)' 표시는 빼도 됩니다).
  · 어떤 세부사항을 묻는지: '(필수)' 세부사항이 빠졌으면 반드시 묻습니다. 표시가 없는 세부사항은 그 필드의 질문 조건이 묻으라고 할 때만 묻습니다. '(보조)' 세부사항은 보조질문(건너뛰어도 되는 질문)으로 묻게 되므로 꼭 필요한 것만 만듭니다. '묻는 세부사항'이 정해진 필드는 그 세부사항만 묻고, 세부사항마다 질문은 하나입니다.
  · 질문 문장은 그 필드의 '구체적 질문 예시'를 따릅니다(자료에서 확인한 내용을 짧게 언급한 뒤 그 세부사항만 묻는 형식). '보완 질문 예시'는 참고용이며 그대로 쓰지 않습니다. 구체적 질문 예시의 괄호 머리말('(첨부자료에서 찾은 경우)' 등)은 어떤 상황에 쓰는 문장인지를 알려 주는 것이고 질문에 넣지 않습니다.
  · 수행 절차(procedure)는 대부분 자료로 작성하고 절차의 정확성만 확인합니다. 자료로 단계를 정리했으면 정리한 단계 목록을 번호로 보여 주고 '위 수행 절차가 실제 업무 진행 순서와 일치하나요? 누락되거나 순서가 다른 단계가 있다면 수정해 주세요.'로 묻는 확인 질문 하나만 만듭니다(element: '단계별 주요 수행 활동 및 처리 순서'. 세부 설명이 빠진 단계가 있으면 이 질문에서 함께 보완을 요청합니다). 그 밖에는 검토·확인 기준, 검토·승인 절차가 자료에 없을 때만 그것을 묻는 질문을 하나씩 만듭니다. 단계마다 따로 묻는 질문은 만들지 않습니다.
  · 칸이 통째로 비었으면(element 빈 문자열) 질문을 만들지 않아도 됩니다. 코드가 노션 양식의 구체적 질문 예시(없으면 보완 질문 예시) 문장 그대로 묻습니다. 다만 '자료에서 구체적인 사례를 찾으면 바로 묻는 칸'(필드 설명에 표시)은 자료에서 찾은 사례(문제·위험·결정·주의사항)를 들어 구체적 질문 예시처럼 묻습니다. 사례를 찾지 못했으면 그 칸의 질문은 만들지 않습니다(코드가 예/아니요로 먼저 묻습니다).
  · '묻지 않음'으로 표시된 필드(미질의·내부 값)와 '질의응답으로만 채움' 필드는 질문하지 않습니다.
  · 근거 자료에 이미 답이 있는 내용은 묻지 않습니다.
  · 인계자가 파일을 찾지 않고 기억·경험으로 바로 답할 수 있게 묻고, 한 문장(길어야 두 문장)으로 끝냅니다.
  · 계약서·매뉴얼처럼 파일로 받는 편이 나은 정보면 ask_for_document 를 true 로 하되, question 은 파일이 없어도 답할 수 있게 핵심 내용을 묻습니다.
- 업무 히스토리(주요 결정사항·협의사항)는 '무엇을 왜 그렇게 결정했는지'와 근거(메일·메신저·회의록)가 드러나게 씁니다. 자료에 없으면 칸을 비워 둡니다(빈 칸은 gaps 로 묻습니다).
- 질문은 인계자가 당시 상황을 떠올릴 수 있게 구체적인 업무·시점·상대를 들어 묻습니다(예: '차액 정산 방식을 지금처럼 정하기까지 어떤 논의가 있었나요?').
- 시스템 ID·비밀번호·토큰 같은 민감 정보는 기록하지 않고 질문하지도 않습니다. 시스템 이름과 권한 신청 방법만 다룹니다.
- 필드마다 '작성 기준'(세부사항·묻는 세부사항·작성 가이드·작성 예시·구체적 질문 예시·보완 질문 예시·질문 조건)이 주어지면 다음처럼 씁니다.
  · 세부사항과 작성 가이드는 그 칸을 자료로 채울 수 있는지 판단하는 기준입니다(암묵지 판단). 핵심 세부사항을 자료로 확인할 수 없으면 짐작해서 채우지 말고, 확인한 세부사항만 씁니다.
  · gaps 의 description 에는 무엇이 빠졌는지(예: '결정 내용은 있으나 판단 근거 없음')를 씁니다.
  · 질문 조건을 따릅니다.
  · 필드 값은 작성 예시를 준용합니다: 작성 예시의 구조(단계 번호, 화살표, 시기순, '번호. 제목: 설명')와 라벨('수행 주기:', '주요 의사결정: / 판단 근거:', '관련 법령·규정:' 등)을 그대로 쓰고 개괄식으로 씁니다. 내용은 자료에 있는 것만 씁니다. 작성 예시의 내용을 옮겨 쓰지 않습니다.
- 인계자 정보에 '인계자가 입력한 세부업무(단위업무)' 목록이 있으면 이 장의 항목은 그 목록의 업무만 만들고, 제목·이름 필드에는 목록의 이름을 글자 그대로 씁니다(자료에 다른 이름으로 나와도 같은 업무면 목록의 이름). 목록에 없는 업무는 항목으로 만들지 않습니다.
- 협업 대상 표의 scope(구분)에는 우리 기관 소속 부서·사람이면 '내부', 다른 기관·업체·외부 사람이면 '외부'를 씁니다. 인계자와 같은 팀 사람은 협업 대상 표에 넣지 않습니다.
- 추진 경과 표(milestones)는 시기순으로 단계마다 한 행을 만들고, status 에 인계 시점 기준 '완료'(끝난 일)·'진행'(지금 하는 일)·'계획'(앞으로 할 일) 중 하나를 씁니다.
- 질문(gaps 의 question)과 summary 는 한국어 존댓말로 씁니다. 필드 값만 개괄식입니다.

"""

INTERPRET_SYSTEM = """\
당신은 업무 인수인계 인터뷰를 진행하는 AI 도우미입니다. 인계자의 답변을 해석해 인수인계서 항목을 갱신합니다.

원칙
- 인계자가 말한 사실만 기록하고, 말하지 않은 내용을 지어내지 않습니다.
- intent: answer(새 정보 제공), confirm(직전에 정리한 내용이 맞다고 함), correct(직전에 정리한 내용을 고침), skip(모름·건너뛰기), finish(문서 생성·종료 요청), question(인계자가 되묻거나 의미를 확인함), other(그 밖의 말).
- 답변이 자료와 다르거나 자료를 확인해야 하면 search_documents 로 확인합니다. 내용이 다르면 reply 에서 정중하게 어느 쪽이 맞는지 확인을 요청합니다.
- updates 의 필드 값(인수인계서에 들어가는 내용)은 개괄식 명사형 종결('~함.', '~임.', '~예정.')로 씁니다. 인계자가 존댓말·반말로 답해도 문서 문체로 바꿔 적습니다(예: '팀장님 지시로 그렇게 정했어요' → '팀장 지시에 따라 결정함.'). reply 는 존댓말입니다.
- updates: 기존 항목을 고치면 그 item_id 를 쓰고, 새 항목이면 item_id 를 빈 문자열로 둡니다. fields 에는 해당 장의 필드 키만 씁니다. 검색 결과를 근거로 썼으면 evidence_ids 에 E 번호를 적습니다.
- resolved_gap_ids: 이번 답변으로 해소된 질문 ID(현재 질문 외에 함께 해소된 것도 포함).
- 현재 질문에 '묻는 칸'이 있으면 답변을 그 칸(필드)에 정리합니다. '현재 질문의 작성 기준'이 주어지면 작성 예시를 준용합니다: 작성 예시의 구조(단계 번호, 시기순, '번호. 제목: 설명')와 라벨('수행 주기:', '주요 의사결정: / 판단 근거:' 등)을 따르고 개괄식으로 쓰되, 내용은 인계자의 말만 씁니다. '(필수)' 세부사항은 답에 있으면 빠뜨리지 않습니다.
- '묻는 세부사항'이 있으면 그 칸에는 이미 값이 있습니다. 답이 빠진 내용을 보태는 것이면 기존 값을 지우지 말고 기존 값 뒤에 답변 내용을 이어 붙인 칸 전체 값을 updates 에 씁니다. 답이 기존 값을 확인·정정하는 것(예: 절차 순서가 맞는지 물었는데 순서를 고쳐 말함)이면 같은 내용을 두 번 쓰지 말고 고친 내용으로 칸 전체를 다시 씁니다.
- '상위 항목'이 주어지면(협업 유의사항·추진 경과 질문) 답변을 그 표의 행으로 정리하고, 연결 필드(task/project)에 상위 항목 이름을 글자 그대로 씁니다. 이미 있는 행(같은 사람·같은 단계)이면 그 item_id 를 씁니다.
- reply: 인계자에게 보낼 메시지(존댓말, 4문장 이내).
  · answer/correct 이고 updates 가 있으면 정리한 내용을 항목별로 짧게 요약하고 "맞으면 '네', 고칠 부분이 있으면 말씀해 주세요."로 끝냅니다(교차 확인).
  · 정리한 뒤 더 알려 달라거나 덧붙여 달라는 요청(추가 질문)을 하지 않습니다. 빈 칸은 코드가 따로 묻습니다.
  · question 이면 질문에 답한 뒤 원래 질문을 짧게 다시 묻습니다.
  · 그 밖에는 짧게 응답합니다. 새 질문을 덧붙이지 않습니다(한 번에 하나의 질문 원칙).
"""

PLAN_SYSTEM = """\
당신은 업무 인수인계 인터뷰를 설계합니다. 장(章)별 분석에서 나온 질문 후보를 받아, 사실상 같은 정보를 묻는 중복 질문을 찾습니다.
- duplicates: 사실상 같은 정보를 묻는 질문 쌍입니다(장이 달라도 같은 답이 나오면 중복). 더 잘 쓰인 쪽을 keep_id, 다른 쪽을 drop_id 로 적습니다. 묻는 대상이나 정보가 다르면 중복이 아닙니다.
- 질문을 묻는 순서는 인수인계서 양식의 장 순서대로 코드가 정하므로 순서는 정하지 않습니다.
- 질문 문장은 바꾸지 않습니다.
"""

SUMMARY_SYSTEM = """\
당신은 업무 인수인계 도우미입니다. 자료 분석 결과를 인계자에게 알려 주는 짧은 안내문을 씁니다.
- 2~3문장, 존댓말. 무엇을 파악했고 어떤 부분이 부족한지 구체적으로(업무·시스템 이름을 들어) 말합니다.
- 제공된 분석 결과에 없는 내용은 쓰지 않습니다. 질문은 하지 않습니다.
"""


AUTO_FILL_SYSTEM = """\
당신은 업무 인수인계서를 마무리합니다. 인계자에게 묻지 않는 칸(미질의 항목: 업무 개요·프로젝트 개요·계획기간·진행상황 요약 등)을 지금까지 수집된 내용으로 채웁니다.
- 칸마다 그 칸의 이름과 작성 기준에 맞게 1~3문장, 개괄식 명사형 종결('~함.', '~임.', '~예정.')로 짧게 씁니다. '~합니다' 같은 존댓말로 쓰지 않습니다.
- 작성 기준에 작성 예시가 있으면 그 형식(구조·순서·라벨)을 준용하고, '(필수)' 세부사항(노션 양식 굵은 글씨, 예: 업무 개요의 목적·주요 내용·최종 결과물)은 수집된 내용에 있으면 빠뜨리지 않습니다.
- 주어진 '수집된 항목'(자료 분석 결과와 인계자 답변이 반영된 값)에 있는 내용만 씁니다. 새 사실·날짜·숫자를 만들지 않습니다.
- 업무 단위 요약의 진행상황 요약은 그 단위업무에 속한 정기업무·비정기 업무 항목의 진행 상태·일정·추진 경과를 모아 요약합니다.
- 진행상황 요약(팀장 양식의 비고)은 일정표 칸에 들어가므로 짧게 씁니다: 핵심 성과·현재 단계·다음 단계만 1~2문장, 공백 포함 80자 안팎. 세부 항목 나열·배경 설명·괄호 속 부연은 빼고, 날짜는 꼭 필요한 것만 남깁니다.
- 팀장 양식(원내 양식)의 현안 개요는 같은 현안의 진행상황·고려사항(인계자 답변 포함)을 바탕으로 '무엇이 왜 현안인지' 1~2문장으로 반드시 채웁니다. 미결사항의 처리대책은 같은 항목의 경과·인계자 답변에서 앞으로의 조치를 옮깁니다.
- 계획기간은 '2026.03~2026.12' 또는 '상시(매월)'처럼 월을 읽을 수 있게 씁니다. 수집된 일정으로 알 수 없으면 빈 문자열로 둡니다.
- 수집된 내용으로 알 수 없으면 value 를 빈 문자열로 둡니다.
- values 에는 주어진 target_id 를 그대로 씁니다.
"""


def units_text(profile: dict[str, Any]) -> str:
    """인계자가 고른 단위업무와 단위업무별로 입력한 정기/비정기 세부업무(프롬프트용 한 줄씩)."""
    lines = []
    for unit in profile_units(profile):
        parts = [f"정기: {', '.join(unit['regular'])}" if unit["regular"] else "", f"비정기: {', '.join(unit['irregular'])}" if unit["irregular"] else ""]
        detail = " / ".join(p for p in parts if p) or "세부업무 입력 없음"
        lines.append(f"\n  · {unit['name'] + ' — ' if unit['name'] else ''}{detail}")
    return "".join(lines)


def profile_block(profile: dict[str, Any]) -> str:
    rows = [
        ("단위 업무명", profile.get("task_name")),
        ("성명/직책", " ".join(x for x in (profile.get("name"), profile.get("position")) if x)),
        ("소속", profile.get("organization")),
        ("인계 유형", MODE_LABELS[mode_of(profile)]),
        ("담당 업무(인계자 입력)", profile.get("duties")),
        ("인계자가 입력한 세부업무(단위업무)", units_text(profile)),
        ("인수자", profile.get("successor")),
        ("휴가 기간", leave_period(profile)),
        ("인계 예정일", profile.get("handover_date")),
    ]
    return "\n".join(f"- {k}: {v}" for k, v in rows if v)


# 팀 관리자가 정한 지침의 상한. BackEnd 가 저장할 때도 같은 한도로 검증하지만, 프롬프트가 부풀지 않도록 여기서도 자른다.
MAX_TEAM_ITEMS = 30
MAX_TEAM_ITEM_NAME = 100
MAX_TEAM_ITEM_INSTRUCTION = 500
MAX_TEAM_NOTES = 2000
MAX_TEAM_PROMPT = 2000  # 팀 전용 프롬프트(AI 가 항상 따라야 하는 역할과 판단 기준)
MAX_TEAM_INTRO = 300  # 팀 소개(3줄 이내)
MAX_TEAM_TERMS = 50  # 업무 용어집 용어 수
MAX_TEAM_TERM = 60
MAX_TEAM_TERM_MEANING = 300


def clean_team_guidelines(guidelines: dict[str, Any] | None) -> dict[str, Any]:
    """팀 지침(profile["team_guidelines"])을 정리한다: 이름이 없는 항목·빈 값을 버리고 길이를 자른다."""
    if not isinstance(guidelines, dict):
        return {"items": [], "notes": "", "prompt": "", "intro": "", "glossary": []}
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
    intro = str(guidelines.get("intro") or "").strip()[:MAX_TEAM_INTRO]
    glossary: list[dict[str, str]] = []
    for raw in guidelines.get("glossary") or []:
        if not isinstance(raw, dict):
            continue
        term = " ".join(str(raw.get("term") or "").split())[:MAX_TEAM_TERM]
        if not term:
            continue
        glossary.append({"term": term, "meaning": " ".join(str(raw.get("meaning") or "").split())[:MAX_TEAM_TERM_MEANING]})
    return {"items": items[:MAX_TEAM_ITEMS], "notes": notes, "prompt": prompt, "intro": intro, "glossary": glossary[:MAX_TEAM_TERMS]}


def team_prompt_block(guidelines: dict[str, Any] | None) -> str:
    """팀 전용 프롬프트만(안내문 쓰기용). 비어 있으면 빈 문자열."""
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
    if not data["items"] and not data["notes"] and not data["prompt"] and not data["intro"] and not data["glossary"]:
        return ""
    lines = [
        "## 우리 팀 인수인계서 지침 (팀 관리자가 정한 내용)",
        "이 지침은 무엇을 찾아 담고 어떻게 쓸지 정하는 것입니다. 사실의 근거가 아니므로, 항목의 내용은 여전히 근거 자료와 인계자 답변에서만 가져옵니다. "
        "근거 ID 를 붙이는 원칙과 시스템 ID·비밀번호 같은 민감 정보를 다루지 않는 원칙은 이 지침으로 바뀌지 않습니다.",
    ]
    if data["intro"]:
        lines.append("")
        lines.append("### 팀 소개 (이 팀이 하는 일)")
        lines.append(data["intro"])
    if data["glossary"]:
        lines.append("")
        lines.append("### 업무 용어집 (우리 팀에서 쓰는 용어와 뜻)")
        for entry in data["glossary"]:
            lines.append(f"- {entry['term']}: {entry['meaning']}" if entry["meaning"] else f"- {entry['term']}")
        lines.append("자료와 인계자 답변에 위 용어가 나오면 이 뜻으로 이해하고, 문서에서도 같은 용어를 씁니다. 용어집은 뜻풀이일 뿐 사실의 근거가 아닙니다.")
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
        lines.append("### 기타 중요 포인트 (우리 팀의 특징·작성 지침)")
        lines.append(data["notes"])
    return "\n".join(lines)


# 부서・팀(반, 사무소)별 인수인계 목록(사내 문서의 요약): 부팀명 / 목록 / 비고. 정보일 뿐 사실의 근거가 아니다(dept_info.py).
COLLABORATION_SLOTS = ("stakeholders", "partners", "irregular", "regular", "leave_tasks")


def _info_lines(info: dict[str, Any]) -> list[str]:
    lines = ["- 목록:" + ("" if info["items"] else " 없음")]
    lines += [f"  {line}" for line in numbered(info["items"])]
    if info["note"]:
        lines.append("- 비고: " + info["note"].replace("\n", " / "))
    return lines


def dept_notes_block(profile: dict[str, Any], *, slot_key: str = "", purpose: str = "") -> str:
    """소속 부·팀의 인수인계 목록(목록·비고)을 프롬프트 한 구간으로 만든다. 정보가 없으면 빈 문자열.

    - slot_key == "dept_notes": 이 장에서 목록의 서류·물품이 자료에서 어떻게 관리되는지, 비고의 첨부·기재 사항이 있는지 찾고, 자료에 없으면 인계자에게 묻는다.
    - 그 밖의 장(slot_key): 관련 있는 정보만 분석·질문에 참고한다. 협업 장에서는 협업 부·팀의 목록도 함께 준다.
    - purpose == "plan": 중복 질문을 찾는 프롬프트용(이름만, 같은 서류·물품을 묻는 질문은 중복으로 본다).
    - 그 밖(slot_key 없음): 인계자와 대화하는 프롬프트용(답변을 목록의 서류·물품 이름으로 정리).
    이 정보는 사실의 근거가 아니다. 근거 ID·민감 정보 원칙은 바뀌지 않는다.
    """
    info = clean_dept_info((profile or {}).get("dept_notes"))
    if not has_info(info):
        return ""
    lines = [
        f"## 우리 부서・팀 인수인계 목록 ({info['department'] or profile.get('organization', '')})",
        "사내 '부서・팀(반, 사무소)별 인수인계 목록'의 요약으로, 이 부·팀이 인계해야 하는 서류·물품의 목록입니다. 정보 자체는 인수인계서 항목의 근거가 아닙니다. "
        "항목의 내용은 여전히 근거 자료와 인계자 답변에서만 가져오고, 근거 ID 를 붙이는 원칙과 시스템 ID·비밀번호 같은 민감 정보를 다루지 않는 원칙은 바뀌지 않습니다.",
    ]
    if purpose == "plan":
        names = [t["name"] for t in dept_targets(info)]
        lines.append("인계 대상: " + ", ".join(names))
        lines.append("같은 서류·물품(또는 비고)을 묻는 질문끼리는 중복으로 봅니다.")
        return "\n".join(lines)
    lines += _info_lines(info)
    if slot_key == "dept_notes":
        lines.append(
            "\n이 장(부서별 추가 자료)에서는 위 목록의 서류·물품 각각이 근거 자료에서 실제로 어떻게 관리되는지(보관 위치·담당자·최신본 갱신 주기·관리 방식) 찾고, "
            "비고에 적힌 첨부·인계물과 함께 적을 사항(예: 열쇠구분, 수량)도 자료에 있는지 확인합니다. "
            "자료에서 찾은 서류·물품은 그 이름을 '추가 자료'로, 자료에 적힌 구체적인 내용을 '관리 내용'으로 적고 근거 ID 를 붙입니다. 위 목록의 이름만 옮겨 적지 않습니다. "
            "자료에 관리 내용이 없는 서류·물품과 비고 사항은 인계자에게 확인할 질문(gaps)으로 만듭니다. 이 장의 gaps 는 이런 질문으로만 채우고(목록과 무관한 일반 질문은 이 장에서 만들지 않습니다), "
            "서류·물품 이름이나 비고의 기재 사항을 넣어 구체적으로 묻습니다. 목록의 이름처럼 이미 있는 정보는 다시 묻지 않습니다. 대상이 여럿이면 인수인계에 더 중요한 것부터 gaps 한도까지 만듭니다."
        )
    elif slot_key:
        lines.append("\n이 장과 관련된 서류·물품이 자료나 대화에 나오면 위 정보를 참고해 분석에 반영하세요(예: 같은 명세표·현황표). 관련 없는 정보는 무시하세요(부서별 추가 자료 장에서 다룹니다). 목록에 있다는 이유만으로 자료에 없는 내용을 사실로 쓰지 않습니다.")
    else:
        lines.append(
            "\n인계자의 답변이 위 목록의 서류·물품이나 비고 사항에 해당하면, 그 이름을 '추가 자료'로·답변 내용을 '관리 내용'으로 해 부서별 추가 자료(dept_notes) 장의 항목으로 updates 에 담고 그 질문(gap)도 해소로 처리합니다. "
            "답변이 위 정보와 어긋나면(예: 더 이상 쓰지 않는 서류다, 이름이 바뀌었다) reply 에서 정중하게 어느 쪽이 맞는지 확인합니다. 새 질문은 덧붙이지 않습니다."
        )
    if slot_key in COLLABORATION_SLOTS or (not slot_key and not purpose):
        if info["others"]:
            lines.append("\n### 협업 부서・팀 인수인계 목록(참고)")
            for name, entries in info["others"].items():
                note = entries["note"].replace("\n", " / ")
                lines.append(f"- {name}: " + (", ".join(entries["items"]) or "목록 없음") + (f" (비고: {note})" if note else ""))
            lines.append("협의 상대·협업 내용이 위 부·팀이면 협의내용을 정리하거나 질문할 때 참고합니다.")
    return "\n".join(lines)


def guide_lines(spec: SlotSpec, key: str, indent: str = "  · ") -> str:
    guide = guide_for(spec.key, key)
    return "".join(f"\n{indent}{line}" for line in guide.lines()) if guide else ""


def ask_note(spec: SlotSpec, key: str) -> str:
    """필드를 묻는 방식(분석 프롬프트용 표시)."""
    f = spec.field(key)
    if f is None:
        return ""
    if f.answers_only:
        return " [질의응답으로만 채움: 자료에서 채우지 않고 질문하지 않음]"
    if f.ask == ASK_HIDDEN:
        return " [내부 값: 문서에 싣지 않음, 묻지 않음]"
    if f.ask == ASK_AUTO:
        return " [묻지 않음(미질의): 자료에 있으면 채우고, 없으면 비워 둠]"
    notes = ["보조질문" if f.ask == ASK_AUX else "필수 질문"]
    if f.specific:
        notes.append("자료에서 구체적인 사례를 찾으면 그 사례를 들어 바로 묻는 칸")
    return f" [{', '.join(notes)}]"


def slot_spec_block(spec: SlotSpec) -> str:
    """장의 필드 정의. 필드마다 핵심 질문(hint), 묻는 방식, 노션 양식의 작성 기준(field_guides)을 함께 준다."""
    fields = "\n".join(f"- {f.key}({f.label}){': ' + f.hint if f.hint else ''}{ask_note(spec, f.key)}{guide_lines(spec, f.key)}" for f in spec.fields)
    whole = guide_lines(spec, "*", indent="- ")
    if whole:
        fields += f"\n\n이 장 전체의 작성 기준:{whole}"
    conditional = "".join(
        f"\n조건부: {key}({spec.label(key)}) 는 {when}({spec.label(when)}) 에 '{'·'.join(values)}' 이(가) 들어갈 때 {'필수 질문' if ask == 'main' else '보조질문'}입니다."
        for key, when, values, ask in spec.ask_when
    )
    if spec.embed_into:
        conditional += f"\n이 장은 '{SLOT_BY_KEY[spec.embed_into].title}' 카드 안의 표입니다. 표의 칸은 자료로만 채우고 이 장의 gaps 는 만들지 않습니다(필요한 질문은 코드가 카드마다 만듭니다)."
    return f"## 작성할 장: {spec.title}\n{spec.description}\n\n필드:\n{fields}\n최소 항목 수: {spec.min_items}{conditional}"


def analysis_user(spec: SlotSpec, profile: dict[str, Any], evidence_text: str) -> str:
    team = team_guidelines_block(profile.get("team_guidelines"), slot_key=spec.key)
    dept = dept_notes_block(profile, slot_key=spec.key)
    links = link_names_block(profile["_link_slots"], spec) if profile.get("_link_slots") is not None else ""
    team_section = (f"{team}\n\n" if team else "") + (f"{dept}\n\n" if dept else "") + (f"{links}\n\n" if links else "")
    evidence = evidence_text or "(검색된 근거가 없습니다. 필요하면 search_documents 로 직접 찾아보세요.)"
    return (
        f"## 인계자 정보\n{profile_block(profile)}\n\n{slot_spec_block(spec)}\n\n{team_section}"
        f"## 근거 자료(검색 결과)\n{evidence}\n\n"
        f"위 근거와 필요하면 추가 검색 결과를 바탕으로 '{spec.title}' 장을 작성하세요. gaps 에는 인계자에게 확인해야 할 내용(특히 문서로 확인되지 않는 경험·판단 근거·주의점)을 개수 제한 없이 모두 담습니다."
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
    return "\n".join(f"- {g['id']} [{SLOT_BY_KEY[g['slot']].title}] {g.get('description', '')}" for g in gaps)


def parent_line(gap: Gap, slots: dict[str, SlotState]) -> str:
    """카드 안 표의 질문이면 그 표가 들어가는 카드(정기업무·프로젝트)의 이름."""
    spec = SLOT_BY_KEY[gap["slot"]]
    if not spec.embed_into or not gap.get("parent_id"):
        return ""
    parent = SLOT_BY_KEY[spec.embed_into]
    item = next((it for it in (slots.get(spec.embed_into) or {}).get("items", []) if it.get("id") == gap["parent_id"]), None)
    if item is None:
        return ""
    name = (item.get("fields") or {}).get(parent.title_field) or item.get("title", "")
    return f"\n- 상위 항목: {parent.title} '{name}' (이 표 [{spec.key}] 의 행으로 정리하고 {spec.embed_link} 에 '{name}' 을 씁니다)"


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
            + (f"\n- 묻는 칸: {current_gap['field']}({SLOT_BY_KEY[current_gap['slot']].label(current_gap['field'])})" if current_gap.get("field") else "")
            + (f"\n- 묻는 세부사항: {current_gap['element']} (기존 값 뒤에 이어 붙입니다)" if current_gap.get("element") else "")
            + parent_line(current_gap, slots)
        )
        spec = SLOT_BY_KEY[current_gap["slot"]]
        criteria = "\n".join(f"- {f.label}{guide_lines(spec, f.key)}" for f in spec.fields if guide_for(spec.key, f.key))
        if guide_for(spec.key):
            criteria += f"\n- {spec.title} 전체{guide_lines(spec, '*')}"
        if criteria.strip():
            gap_text += f"\n\n## 현재 질문의 작성 기준({spec.title})\n{criteria.strip()}"
        if current_gap.get("reopened"):
            gap_text += "\n- (참고) 인계자가 이미 답했던 질문을 다시 열었습니다. 새 메시지는 앞의 답을 고치는 답변이므로, 기존 값과 다르면 새 값으로 updates 에 담으세요."
    else:
        gap_text = "(현재 질문 없음)"
    team = team_guidelines_block(profile.get("team_guidelines"))
    dept = dept_notes_block(profile)
    team_section = (f"{team}\n\n" if team else "") + (f"{dept}\n\n" if dept else "")
    # 이 인수인계서에 실리는 장만 고르게 한다(다른 버전 전용 장에 써 넣으면 문서에 나오지 않는다)
    usable = "\n".join(f"- {s.title} [{s.key}]: {', '.join(f'{f.key}({f.label})' for f in s.fields)}" for s in slots_for(profile))
    team_section += f"## 이 인수인계서에서 쓰는 장(updates 의 slot 은 이 중에서만 고르세요)\n{usable}\n\n"
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
    dept_section = f"{dept}\n\n" if dept else ""
    return f"## 인계자 정보\n{profile_block(profile)}\n\n{dept_section}## 질문 후보\n" + "\n".join(lines)


def auto_fill_user(profile: dict[str, Any], slots: dict[str, SlotState], targets: list[dict[str, Any]]) -> str:
    lines = []
    for i, t in enumerate(targets):
        spec = SLOT_BY_KEY[t["slot"]]
        guide = guide_lines(spec, t["field"], indent="  · ")
        current = f" | 자료에서 읽은 값: {t['current']}" if t.get("current") else ""
        lines.append(f"- target_id=t{i} | {spec.title} > {t['title']} > {t['label']}{current}{guide}")
    return (
        f"## 인계자 정보\n{profile_block(profile)}\n\n## 채울 칸\n" + "\n".join(lines)
        + f"\n\n## 수집된 항목\n{items_block(slots)}\n\n각 칸의 값을 작성하세요."
    )


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

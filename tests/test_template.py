"""노션 '인수인계서 형식' 페이지의 최종 템플릿과 슬롯 정의가 같은지 확인한다."""

from amigo_agent import HandoverAgent
from amigo_agent.template import MODE_LEAVE, SLOT_BY_KEY, mode_of, slots_for

from conftest import PROFILE, FakeKB

LEAVE = {**PROFILE, "mode": MODE_LEAVE}


def keys(profile):
    return [s.key for s in slots_for(profile)]


def labels(slot_key):
    return [f.label for f in SLOT_BY_KEY[slot_key].fields]


def test_two_versions_have_their_own_chapters_in_template_order():
    # [인사발령] A 업무 단위 요약 → B 정기업무 → C 비정기 업무(+협업 표) → D 시스템 접근 권한 → E 부서별 추가 자료
    assert keys(PROFILE) == ["overview", "regular", "irregular", "stakeholders", "systems", "dept_notes"]
    # [장기휴가] A 부재 중 처리할 단위 업무 → B 세부 업무 → C 시스템 접근 권한
    assert keys(LEAVE) == ["leave_units", "leave_tasks", "leave_contacts", "systems"]  # leave_contacts 는 세부 업무 카드 안에 들어간다
    assert mode_of({}) != MODE_LEAVE and mode_of({"mode": "LEAVE"}) == MODE_LEAVE


def test_unit_summary_table_columns():
    assert labels("overview") == ["단위업무명", "계획기간", "비고"]
    assert SLOT_BY_KEY["overview"].layout == "gantt" and SLOT_BY_KEY["overview"].evidence is False  # 월별 막대, 근거 없음


def test_regular_task_items_match_the_template_questions():
    assert labels("regular")[1:] == [
        "업무 개요", "수행 시기", "수행 절차", "중점관리사항", "주요 의사결정 및 판단 근거", "주요 협업 및 협의사항", "향후 과제 및 발전 방향", "참고자료 및 관련 근거",
    ]
    hints = {f.label: f.hint for f in SLOT_BY_KEY["regular"].fields}
    assert hints["업무 개요"] == "무엇을, 왜 하는가?"
    assert hints["주요 의사결정 및 판단 근거"] == "무엇을, 왜 그렇게 결정했는가?"


def test_irregular_project_items_match_the_template_and_collaboration_is_a_table():
    # 7개 항목 중 '4. 주요 협업관계 및 협의사항'은 표(별도 슬롯)로 구성한다
    assert labels("irregular")[1:] == [
        "프로젝트 개요 및 추진 배경", "프로젝트 목표 및 기대성과", "추진 경과 및 현황", "주요 의사결정 및 판단 근거", "중점관리사항", "향후 추진과제 및 발전 방향",
    ]
    collab = SLOT_BY_KEY["stakeholders"]
    assert (collab.embed_into, collab.embed_after) == ("irregular", "progress")  # 비정기 업무 카드 안, '추진 경과 및 현황' 뒤
    assert [f.key for f in collab.fields] == ["party", "project", "discussed", "upcoming"]  # 구분·관계 유형·담당자·연락처는 삭제
    assert "팀 또는 개인" in collab.fields[0].hint
    assert labels("stakeholders")[:4] == ["담당자", "프로젝트명", "협의내용", "추후 고려사항"]


def test_leave_detail_items_match_the_template():
    # 7개 항목 중 '5. 비상 대응 및 협조사항'은 담당자별 표(별도 슬롯)로 구성한다
    assert labels("leave_tasks")[1:] == ["업무 개요", "주요 일정 및 처리사항", "업무 처리방법", "중점관리사항", "미결사항 및 후속 조치", "참고자료"]
    assert labels("leave_units")[0] == "단위업무명" and SLOT_BY_KEY["leave_units"].layout == "gantt"
    contacts = SLOT_BY_KEY["leave_contacts"]
    assert (contacts.embed_into, contacts.embed_after, contacts.embed_label) == ("leave_tasks", "key_points", "비상 대응 및 협조사항")
    assert labels("leave_contacts") == ["담당자", "세부 업무명", "대응·협조 내용", "추후 고려사항"]


def test_system_access_slot_is_shared_and_keeps_passwords_blank():
    spec = SLOT_BY_KEY["systems"]
    assert spec.title == "시스템 접근 권한" and spec.leave and spec.handwritten


def test_leave_version_runs_end_to_end_with_only_its_chapters():
    kb = FakeKB()
    agent = HandoverAgent()
    seen = []
    result = agent.start("leave1", LEAVE, kb, on_event=seen.append)
    assert {e["key"] for e in seen if e["type"] == "slot"} == {"leave_units", "leave_tasks", "leave_contacts", "systems"}
    assert set(result.snapshot["slots"]) == {"leave_units", "leave_tasks", "leave_contacts", "systems"}
    assert all(g["slot"] in {"leave_units", "leave_tasks", "leave_contacts", "systems"} for g in result.snapshot["gaps"])
    doc = agent.finish("leave1", kb).snapshot["document"]
    headings = [line for line in doc.splitlines() if line.startswith("## ")]
    assert headings[:3] == ["## 1. 부재 중 처리할 단위 업무", "## 2. 세부 업무", "## 3. 시스템 접근 권한"]
    assert "정기업무" not in doc and "비정기" not in doc and "부서별 추가 자료" not in doc
    assert "ID: ________ / PW: ________" in doc


def test_full_version_document_follows_the_template_chapters():
    kb = FakeKB()
    agent = HandoverAgent()
    agent.start("full1", PROFILE, kb)
    doc = agent.finish("full1", kb).snapshot["document"]
    headings = [line for line in doc.splitlines() if line.startswith("## ")]
    # 협업관계는 별도 장이 아니라 각 프로젝트 카드 안에 들어가므로 장이 5개다
    assert headings[:5] == [
        "## 1. 업무 단위 요약 + 일정 명세표",
        "## 2. 정기업무",
        "## 3. 비정기 업무(프로젝트성)",
        "## 4. 시스템 접근 권한",
        "## 5. 부서별 추가 자료",
    ]
    assert "## 1. 업무 개요" not in doc and "인수자 핵심 당부사항" not in doc  # 문서 앞 요약 장은 없다
    assert "<th>1월</th>" in doc and "<th>12월</th>" in doc and "<th>비고</th>" in doc  # 업무 단위 요약은 월별 일정표
    assert "| 항목 | 내용 |" in doc and "| 업무 개요 |" in doc and "| 수행 시기 |" in doc

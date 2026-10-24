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
    # [인사발령] A 업무 단위 요약 → B 정기업무(+협업 표) → C 비정기 업무(+협업 표) → D 시스템 접근 권한 → E 부서별 추가 자료
    assert keys(PROFILE) == ["overview", "regular", "partners", "irregular", "stakeholders", "systems", "dept_notes"]
    # [장기휴가] A 세부 업무 → B 시스템 접근 권한 → C 주요 연락망
    assert keys(LEAVE) == ["leave_tasks", "systems", "contacts"]
    assert mode_of({}) != MODE_LEAVE and mode_of({"mode": "LEAVE"}) == MODE_LEAVE


def test_every_field_is_required_except_conditional_ones():
    for spec in slots_for(PROFILE) + slots_for(LEAVE):
        assert set(spec.required) == {f.key for f in spec.fields} - set(spec.auto), spec.key  # 필수/추천 구분 없이 모두 필수(자동 작성 칸만 제외)
    assert SLOT_BY_KEY["overview"].auto == ("note",)  # 진행상황 요약은 묻지 않고 문서를 만들 때 수집된 내용으로 쓴다
    assert SLOT_BY_KEY["leave_tasks"].required_when == (("status", "task_type", ("프로젝트",)),)


def test_unit_summary_table_columns():
    assert labels("overview") == ["단위업무명", "계획기간", "진행상황 요약"]
    assert SLOT_BY_KEY["overview"].layout == "gantt" and SLOT_BY_KEY["overview"].evidence is False  # 월별 막대, 근거 없음


def test_regular_task_items_match_the_template_questions():
    assert labels("regular")[1:] == [
        "업무 개요", "수행 시기", "수행 절차", "중점 관리사항", "주요 의사결정 및 판단 근거", "향후 과제 및 발전 방향", "참고자료 및 관련 근거",
    ]  # '주요 협업관계'는 표(별도 슬롯 partners)로 의사결정 다음 자리에 들어간다
    partners = SLOT_BY_KEY["partners"]
    assert (partners.embed_into, partners.embed_after, partners.embed_label) == ("regular", "decisions", "주요 협업관계")
    assert [f.label for f in partners.fields if f.key != partners.embed_link] == ["소속", "이름", "주요역할", "연락처", "비고(유의사항)"]
    assert partners.title_field == "name"
    hints = {f.label: f.hint for f in SLOT_BY_KEY["regular"].fields}
    assert hints["업무 개요"] == "무엇을, 왜 하는가?" and hints["수행 시기"] == "언제 하는가?"
    assert hints["주요 의사결정 및 판단 근거"] == "무엇을, 왜 그렇게 결정했는가?"


def test_irregular_project_items_match_the_template_and_collaboration_is_a_table():
    # 7개 항목 중 '4. 주요 협업관계 및 협의사항'은 표(별도 슬롯)로 구성한다
    assert labels("irregular")[1:] == [
        "프로젝트 개요", "프로젝트 목표", "추진 경과", "주요 의사결정 및 판단 근거", "중점 관리사항", "향후 추진과제 및 발전 방향",
    ]
    collab = SLOT_BY_KEY["stakeholders"]
    assert (collab.embed_into, collab.embed_after, collab.embed_label) == ("irregular", "progress", "주요 협업관계 및 협의사항")  # 3. 추진 경과 다음 4번
    assert [f.key for f in collab.fields] == ["party", "project", "discussed", "upcoming"]
    assert labels("stakeholders") == ["담당자", "프로젝트명", "협의내용", "추후 고려사항"]


def test_leave_detail_items_match_the_template():
    # 장기휴가 A. 세부 업무: 1 업무 유형 2 주요 일정 및 처리사항 3 현재 진행 상태(프로젝트성만) 4 업무 처리방법 5 주의사항 및 후속조치 6 참고자료
    assert labels("leave_tasks")[1:] == ["업무 유형", "주요 일정 및 처리사항", "현재 진행 상태", "업무 처리방법", "주의사항 및 후속조치", "참고자료"]
    assert SLOT_BY_KEY["leave_tasks"].field("schedule").hint == "부재 기간 중 언제, 무엇을 처리해야 하는가?"  # 템플릿의 핵심 질문
    assert "leave_units" not in SLOT_BY_KEY and "leave_contacts" not in SLOT_BY_KEY


def test_leave_contacts_chapter_matches_the_template():
    # 장기휴가 C. 주요 연락망: 이름 / 기관 / 부서 / 직책 / 연락처 / 관련 업무
    assert labels("contacts") == ["이름", "기관", "부서", "직책", "연락처", "관련 업무"]
    assert SLOT_BY_KEY["contacts"].modes == (MODE_LEAVE,)


def test_project_status_is_required_only_for_project_type_tasks():
    from amigo_agent.state import missing_fields

    spec = SLOT_BY_KEY["leave_tasks"]
    base = {"name": "정산", "schedule": "매월 말", "method": "절차", "cautions": "주의", "references": "매뉴얼"}
    assert missing_fields(spec, {"fields": {**base, "task_type": "정기"}}) == []  # 정기 업무는 진행 상태를 묻지 않는다
    assert missing_fields(spec, {"fields": {**base, "task_type": "프로젝트성"}}) == ["status"]
    assert missing_fields(spec, {"fields": base}) == ["task_type"]  # 유형부터 묻는다


def test_system_access_slot_is_shared_and_keeps_passwords_blank():
    spec = SLOT_BY_KEY["systems"]
    assert spec.title == "시스템 접근 권한" and spec.leave and spec.handwritten


def test_leave_version_runs_end_to_end_with_only_its_chapters():
    kb = FakeKB()
    agent = HandoverAgent()
    seen = []
    result = agent.start("leave1", LEAVE, kb, on_event=seen.append)
    chapters = {"leave_tasks", "systems", "contacts"}
    assert {e["key"] for e in seen if e["type"] == "slot"} == chapters
    assert set(result.snapshot["slots"]) == chapters
    assert all(g["slot"] in chapters for g in result.snapshot["gaps"])
    doc = agent.finish("leave1", kb).snapshot["document"]
    headings = [line for line in doc.splitlines() if line.startswith("## ")]
    assert headings[:3] == ["## 1. 세부 업무", "## 2. 시스템 접근 권한", "## 3. 주요 연락망"]
    assert "정기업무" not in doc and "비정기" not in doc and "부서별 추가 자료" not in doc and "<th>1월</th>" not in doc  # 장기휴가에는 월별 일정표가 없다
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
    assert '<th colspan="12">계획기간</th>' in doc and "<th>1월</th>" in doc and '<th rowspan="2">진행상황 요약</th>' in doc  # 월별 일정표, 머리글 2행
    assert "<th>업무 개요</th>" in doc and "<th>수행 시기</th>" in doc and "<th>주요 협업관계</th>" in doc


def test_code_asks_every_empty_required_field_the_model_did_not_ask():
    from amigo_agent.nodes import _missing_field_gaps

    item = {"id": "leave_tasks-1", "title": "홈페이지 개편", "fields": {"name": "홈페이지 개편", "task_type": "프로젝트성", "schedule": "11월 검수", "method": "검수", "cautions": "일정"}}
    slots = {"leave_tasks": {"items": [item]}}
    model_gap = {"id": "gap-1", "slot": "leave_tasks", "item_id": "leave_tasks-1", "question": "현재 진행단계는 어디까지인가요?", "status": "open"}
    made = _missing_field_gaps(slots, slots_for(LEAVE), [model_gap])
    # 진행 상태는 모델이 이미 묻고, 참고자료만 빠졌다. 빠진 항목이 하나면 노션 양식의 질문 예시로 묻는다
    assert [g["question"] for g in made] == ["'홈페이지 개편'의 참고자료에 대해 여쭤볼게요. 업무 중에 자주 열어보는 자료는 무엇인가요?"]
    assert _missing_field_gaps(slots, slots_for(LEAVE), [model_gap, *made]) == []


def test_partner_name_holds_only_a_person_team_or_organization():
    from amigo_agent.nodes import fix_party_names

    slots = {"regular": {"items": [{"title": "SSL 인증서 갱신"}]}, "systems": {"items": [{"title": "도메인·SSL 인증서 관리"}]}}
    items = [
        {"title": "x", "fields": {"name": "도메인·SSL 인증서 관리 (총무팀)", "org": "", "task": "SSL 인증서 갱신"}},  # 시스템명 + 괄호 속 팀 → 팀명
        {"title": "x", "fields": {"name": "SSL 인증서 갱신", "org": "총무팀"}},  # 업무명만 → 비워서 묻는다
        {"title": "박지훈 차장", "fields": {"name": "박지훈 차장", "org": "재무팀"}},  # 사람 이름은 그대로
    ]
    fix_party_names(SLOT_BY_KEY["partners"], items, slots)
    assert [it["fields"]["name"] for it in items] == ["총무팀", "", "박지훈 차장"]
    assert items[0]["fields"]["org"] == "총무팀"
    assert "업무명·시스템명은 쓰지 않는다" in SLOT_BY_KEY["partners"].field("name").hint


def test_unit_summary_asks_only_about_schedule_and_note_is_written_at_compose():
    from amigo_agent.nodes import _missing_field_gaps, in_scope

    item = {"id": "overview-1", "title": "공동망 운영", "fields": {"name": "공동망 운영", "period": "", "note": ""}}
    made = _missing_field_gaps({"overview": {"items": [item]}}, [SLOT_BY_KEY["overview"]], [])
    assert [g["description"] for g in made] == ["'공동망 운영'의 계획기간 비어 있음"]  # 진행상황 요약은 묻지 않는다
    assert in_scope({"slot": "overview", "question": "공동망 운영은 언제부터 언제까지 진행하나요?"})
    assert not in_scope({"slot": "overview", "question": "공동망 운영에서 최근 장애는 어떻게 처리했나요?"})  # 내용은 정기·비정기 업무 장에서
    assert in_scope({"slot": "regular", "question": "최근 장애는 어떻게 처리했나요?"})

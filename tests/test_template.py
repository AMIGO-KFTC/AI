"""노션 '인수인계서 형식' 페이지의 최종 템플릿·'수정사항'(10.25)과 슬롯 정의가 같은지 확인한다."""

from amigo_agent import HandoverAgent
from amigo_agent.template import ASK_AUTO, ASK_AUX, ASK_HIDDEN, ASK_MAIN, MODE_LEAVE, SLOT_BY_KEY, mode_of, profile_units, seed_items, slots_for

from conftest import PROFILE, FakeKB

LEAVE = {**PROFILE, "mode": MODE_LEAVE}


def keys(profile):
    return [s.key for s in slots_for(profile)]


def labels(slot_key):
    return [f.label for f in SLOT_BY_KEY[slot_key].shown]


def asks(slot_key):
    spec = SLOT_BY_KEY[slot_key]
    return {f.key: f.ask for f in spec.fields}


def test_two_versions_have_their_own_chapters_in_template_order():
    # [인사발령] A 업무 단위 요약 → B 정기업무(+협업 표) → C 비정기 업무(+추진 경과·협업 표) → D 기타 자료(시스템 접근 권한 · 부서별 추가 자료)
    assert keys(PROFILE) == ["overview", "regular", "partners", "irregular", "milestones", "stakeholders", "systems", "dept_notes"]
    # [장기휴가] A 세부 업무 → B 주요 연락망 → C 기타 자료(시스템 접근 권한)
    assert keys(LEAVE) == ["leave_tasks", "contacts", "systems"]
    assert mode_of({}) != MODE_LEAVE and mode_of({"mode": "LEAVE"}) == MODE_LEAVE


def test_each_field_has_the_ask_type_of_the_notion_checklist():
    # A. 업무 단위 요약: 질문하지 않음(미질의)
    assert set(asks("overview").values()) == {ASK_AUTO}
    # B. 정기업무: 업무개요 미질의, 수행시기 주기 선택·달력, 중점관리·의사결정 예/아니요, 향후 과제 예/아니요 + 보조질문, 기타(참고자료 등) 보조질문
    regular = SLOT_BY_KEY["regular"]
    assert regular.field("overview").ask == ASK_AUTO
    assert regular.field("timing").picker == "period"
    assert regular.field("key_points").gate and regular.field("decisions").gate
    assert regular.field("future").ask == ASK_AUX and regular.field("future").gate
    assert regular.field("references").ask == ASK_AUTO and regular.field("references").placeholder  # 묻지 않고 문서에 흐린 안내(노션 표)
    assert regular.field("references").label == "기타(참고자료 등)"
    # 협업관계: 유의사항만 묻는다(나머지 칸은 자료로)
    for key in ("partners", "stakeholders"):
        spec = SLOT_BY_KEY[key]
        assert {f.ask for f in spec.shown} == {ASK_AUTO}
    assert not SLOT_BY_KEY["partners"].gate_question  # 정기: 자료에 유의사항이 없으면 묻지 않고 문서에서 고친다(노션 표)
    assert SLOT_BY_KEY["stakeholders"].gate_question
    # C. 비정기: 프로젝트 개요 미질의, 의사결정 예/아니요, 기타(중점 관리사항) 보조질문(자료에서 찾으면 그 사항을 묻는다)
    irregular = SLOT_BY_KEY["irregular"]
    assert irregular.field("overview").ask == ASK_AUTO and irregular.field("decisions").gate
    assert irregular.field("key_points").ask == ASK_AUX and irregular.field("key_points").specific
    # D. 기타 자료: 시스템 접근 권한은 묻지 않고, 부서별 추가 자료는 관리 내용을 묻는다
    assert {f.ask for f in SLOT_BY_KEY["systems"].fields} == {ASK_AUTO}
    assert SLOT_BY_KEY["dept_notes"].field("detail").ask == ASK_AUTO and not SLOT_BY_KEY["dept_notes"].empty_ask  # 부서별 추가 자료는 묻지 않는다
    assert SLOT_BY_KEY["systems"].group == SLOT_BY_KEY["dept_notes"].group == "기타 자료"
    # 장기휴가 A: 업무유형 미질의, 진행 상태는 비정기 필수·정기 보조, 주의사항·참고자료 보조(참고자료는 질의응답으로만)
    leave = SLOT_BY_KEY["leave_tasks"]
    assert leave.field("task_type").ask == ASK_HIDDEN  # 장기휴가는 정기/비정기로 나누지 않아 문서에 싣지 않는다
    assert leave.ask_of("status", {"task_type": "비정기"}) == ASK_AUX and leave.ask_of("status", {}) == ASK_AUX
    assert leave.field("cautions").ask == ASK_AUX and leave.field("references").answers_only
    # 장기휴가 B 주요 연락망: 보조질문(비어 있을 때만)
    assert SLOT_BY_KEY["contacts"].empty_ask == ASK_AUX


def test_unit_summary_table_columns():
    assert labels("overview") == ["단위업무명", "계획기간", "비고"]  # 진행상황 요약 → 비고(내용은 진행상황 요약)
    assert SLOT_BY_KEY["overview"].layout == "gantt" and SLOT_BY_KEY["overview"].evidence is False  # 월별 막대, 근거 없음


def test_regular_task_items_match_the_template_questions():
    assert labels("regular")[1:] == [
        "업무 개요", "수행 시기", "수행 절차", "중점 관리사항", "주요 의사결정 및 판단 근거", "향후 과제 및 발전 방향", "기타(참고자료 등)",
    ]  # '주요 협업관계'는 표(별도 슬롯 partners)로 의사결정 다음 자리에 들어간다. 단위업무명은 내부 값
    assert asks("regular")["unit"] == ASK_HIDDEN
    partners = SLOT_BY_KEY["partners"]
    assert (partners.embed_into, partners.embed_after, partners.embed_label) == ("regular", "decisions", "주요 협업관계")
    assert labels("partners") == ["소속", "이름", "주요역할", "연락처", "비고(유의사항)"]  # 외부 표. 내부 표에는 연락처가 없다
    assert partners.split_field == "scope" and dict(partners.split_drop) == {"내부": ("phone",)}
    hints = {f.label: f.hint for f in SLOT_BY_KEY["regular"].fields}
    assert hints["업무 개요"] == "무엇을, 왜 하는가?" and hints["수행 시기"] == "언제 하는가?"


def test_irregular_project_items_match_the_template_and_tables():
    # 5개 항목: 1 프로젝트 개요 2 추진 경과(표) 3 주요 협업관계(표) 4 주요 의사결정 및 판단 근거 5 기타(중점 관리사항)
    assert labels("irregular")[1:] == ["프로젝트 개요", "주요 의사결정 및 판단 근거", "기타(중점 관리사항)"]
    milestones, collab = SLOT_BY_KEY["milestones"], SLOT_BY_KEY["stakeholders"]
    assert (milestones.embed_into, milestones.embed_after, milestones.embed_label) == ("irregular", "overview", "추진 경과")
    assert labels("milestones") == ["시기", "주요 추진 내용", "비고"]  # 상태(완료/진행/계획)는 내부 값
    assert asks("milestones")["status"] == ASK_HIDDEN
    assert (collab.embed_into, collab.embed_after, collab.embed_label) == ("irregular", "overview", "주요 협업관계")
    assert labels("stakeholders") == labels("partners")  # 정기업무와 같은 형식


def test_leave_detail_items_match_the_template():
    assert labels("leave_tasks")[1:] == ["주요 일정 및 처리사항", "현재 진행 상태", "업무 처리방법", "주의사항 및 후속조치", "참고자료"]
    assert SLOT_BY_KEY["leave_tasks"].field("schedule").hint == "부재 기간 중 언제, 무엇을 처리해야 하는가?"  # 템플릿의 핵심 질문


def test_leave_contacts_chapter_matches_the_reassignment_format():
    assert labels("contacts") == ["소속", "이름", "주요역할", "연락처", "비고(유의사항)"]
    assert SLOT_BY_KEY["contacts"].modes == (MODE_LEAVE,) and SLOT_BY_KEY["contacts"].split_field == "scope"


def test_project_status_is_required_only_for_irregular_tasks():
    from amigo_agent.state import missing_fields

    spec = SLOT_BY_KEY["leave_tasks"]
    base = {"name": "정산", "schedule": "매월 말", "method": "절차", "cautions": "주의", "references": "매뉴얼"}
    assert missing_fields(spec, {"fields": {**base, "task_type": "정기"}}) == []  # 정기 업무의 진행 상태는 보조질문
    assert missing_fields(spec, {"fields": {**base, "task_type": "정기"}}, aux=True) == ["status"]
    assert missing_fields(spec, {"fields": {**base, "task_type": "비정기"}}) == []  # 정기/비정기와 상관없이 보조질문


def test_units_from_the_giver_become_items():
    profile = {**PROFILE, "units": [{"name": "공동망 운영", "regular": ["차액 정산", " "], "irregular": ["망 고도화"]}, {"name": "", "regular": [], "irregular": []}]}
    assert profile_units(profile) == [{"name": "공동망 운영", "regular": ["차액 정산"], "irregular": ["망 고도화"]}]
    assert seed_items(SLOT_BY_KEY["overview"], profile) == [{"name": "공동망 운영"}]
    assert seed_items(SLOT_BY_KEY["regular"], profile) == [{"name": "차액 정산", "unit": "공동망 운영"}]
    leave = {**LEAVE, "units": [{"name": "", "regular": ["월간 보고"], "irregular": ["시스템 개편"]}]}
    assert seed_items(SLOT_BY_KEY["leave_tasks"], leave) == [{"name": "월간 보고"}, {"name": "시스템 개편"}]


def test_system_access_slot_is_shared_and_keeps_passwords_blank():
    spec = SLOT_BY_KEY["systems"]
    assert spec.title == "시스템 접근 권한" and spec.leave and spec.handwritten
    assert spec.muted_note == "시스템 접근 권한이 있을 경우 해당 내용을 작성해주세요."  # 화면 전용 회색 안내(내려받는 문서에서는 빠진다)


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
    assert headings[:3] == ["## 1. 세부 업무", "## 2. 주요 연락망", "## 3. 기타 자료"]
    assert "### 시스템 접근 권한" in doc and "AMIGO에게 요청하셔도 돼요" not in doc
    assert "정기업무" not in doc and "부서별 추가 자료" not in doc and "<th>1월</th>" not in doc  # 장기휴가에는 월별 일정표가 없다
    assert "ID: ________ / PW: ________" in doc


def test_full_version_document_follows_the_template_chapters():
    kb = FakeKB()
    agent = HandoverAgent()
    agent.start("full1", PROFILE, kb)
    doc = agent.finish("full1", kb).snapshot["document"]
    headings = [line for line in doc.splitlines() if line.startswith("## ")]
    # 협업관계·추진 경과는 별도 장이 아니라 각 카드 안에, 시스템 접근 권한·부서별 추가 자료는 '기타 자료' 아래 소제목으로 들어간다
    assert headings[:4] == ["## 1. 업무 단위별 일정 명세표", "## 2. 주요 내용", "## 3. 기타 자료"]
    assert "### 시스템 접근 권한" in doc and "### 부서별 추가 자료" in doc
    assert '<th colspan="12">계획기간</th>' in doc and "<th>1월</th>" in doc and '<th rowspan="2">비고</th>' in doc  # 월별 일정표, 머리글 2행
    assert "<th>업무 개요</th>" in doc and "<th>수행 시기</th>" in doc


def test_code_asks_every_empty_field_with_the_notion_question_and_ask_type():
    from amigo_agent.nodes import _missing_field_gaps

    item = {"id": "leave_tasks-1", "title": "홈페이지 개편", "fields": {"name": "홈페이지 개편", "task_type": "비정기", "schedule": "11월 검수", "method": "검수", "cautions": "일정"}}
    slots = {"leave_tasks": {"items": [item]}}
    model_gap = {"id": "gap-1", "slot": "leave_tasks", "item_id": "leave_tasks-1", "field": "status", "question": "현재 진행단계는 어디까지인가요?", "status": "open"}
    made = _missing_field_gaps(slots, slots_for(LEAVE), [model_gap])
    # 진행 상태는 모델이 이미 묻고, 참고자료만 빠졌다. 노션 양식의 질문 예시로, 보조질문으로 묻는다
    assert [(g["question"], g["aux"]) for g in made] == [("'홈페이지 개편'의 참고자료에 대해 여쭤볼게요.\n업무 처리를 위해 가장 빈번하게 참고하는 자료는 무엇인가요?", True)]
    assert _missing_field_gaps(slots, slots_for(LEAVE), [model_gap, *made]) == []


def test_gate_and_picker_questions_for_regular_tasks():
    from amigo_agent.nodes import _missing_field_gaps

    item = {"id": "regular-1", "title": "월간 보고", "fields": {"name": "월간 보고", "procedure": "1. 취합"}}
    made = {g["field"]: g for g in _missing_field_gaps({"regular": {"items": [item]}}, [SLOT_BY_KEY["regular"]], [])}
    assert set(made) == {"timing", "key_points", "decisions", "future"}  # 업무 개요·기타(참고자료 등)는 묻지 않는다
    assert made["timing"]["picker"] == "period"
    assert made["decisions"]["gate"] == "ask" and "주요하게 결정한 사항이 있나요?" in made["decisions"]["question"]
    assert "어떤 이유와 기준으로 결정했나요?" in made["decisions"]["follow"]  # 예면 노션 질문 예시로 이어서 묻는다
    assert made["future"]["gate"] == "ask" and made["future"]["aux"]
    assert not made["key_points"]["aux"]


def test_partner_name_holds_only_a_person_team_or_organization():
    from amigo_agent.nodes import fix_party_names

    slots = {"regular": {"items": [{"title": "SSL 인증서 갱신"}]}, "systems": {"items": [{"title": "도메인·SSL 인증서 관리"}]}}
    items = [
        {"title": "x", "fields": {"name": "도메인·SSL 인증서 관리 (총무팀)", "org": "", "task": "SSL 인증서 갱신"}},  # 시스템명 + 괄호 속 팀 → 팀명
        {"title": "x", "fields": {"name": "SSL 인증서 갱신", "org": "총무팀"}},  # 업무명만 → 소속 이름
        {"title": "박지훈 차장", "fields": {"name": "박지훈 차장", "org": "재무팀"}},  # 사람 이름은 그대로
    ]
    fix_party_names(SLOT_BY_KEY["partners"], items, slots)
    assert [it["fields"]["name"] for it in items] == ["총무팀", "총무팀", "박지훈 차장"]
    assert items[0]["fields"]["org"] == "총무팀"
    assert "업무명·시스템명은 쓰지 않는다" in SLOT_BY_KEY["partners"].field("name").hint


def test_unit_summary_is_never_asked():
    from amigo_agent.nodes import _missing_field_gaps

    item = {"id": "overview-1", "title": "공동망 운영", "fields": {"name": "공동망 운영", "period": "", "note": ""}}
    assert _missing_field_gaps({"overview": {"items": [item]}}, [SLOT_BY_KEY["overview"]], []) == []  # 계획기간·진행상황 요약 모두 묻지 않는다


def test_collaboration_tables_leave_out_the_givers_own_team():
    from amigo_agent.nodes import settle_items

    profile = {**PROFILE, "organization": "디지털전략부 · 웹서비스팀"}
    slot = {"items": [
        {"id": "p1", "title": "김하늘 대리", "fields": {"org": "웹서비스팀", "name": "김하늘 대리", "task": "월간 보고"}},
        {"id": "p2", "title": "박지훈 차장", "fields": {"org": "재무팀", "name": "박지훈 차장", "task": "월간 보고"}},
        {"id": "p3", "title": "이서연", "fields": {"org": "디지털전략부 웹서비스팀", "name": "이서연", "task": "월간 보고"}},
    ]}
    settle_items(SLOT_BY_KEY["partners"], profile, slot)
    assert [it["id"] for it in slot["items"]] == ["p2"]  # 같은 팀은 협업관계 표에 넣지 않는다


def test_team_lead_handover_uses_the_manager_form():
    """관리자(팀장) 인사발령 양식(노션 '추가 요구사항 정의' 5): 1 사업계획 → 2 현안 → 3 제계약서 → 4 미결 → 5~15 → 해당 팀만 16."""
    from amigo_agent.template import OFFICE_ITEMS, OFFICE_SLOT

    profile = {**PROFILE, "form": "office", "plans": ["은행 공동 본인확인 플랫폼 구축 추진", "대고객 마케팅 플랫폼 구축 추진"],
               "issues": ["은행 공동 본인확인 플랫폼 구축 추진", "인증서 발급 채널 혁신"],
               "dept_notes": {"department": "IT아미고팀", "items": ["대리점 운영 현황", "판매 실적 현황"], "note": ""},
               "dept_uploads": {"판매 실적 현황": ["판매실적_2026.xlsx"]}}
    assert keys(profile) == ["office_plans", "office_issues", "office_pending", "office_items"]
    agent = HandoverAgent()
    snap = agent.start("office1", profile, FakeKB()).snapshot
    assert [it["title"] for it in snap["slots"]["office_plans"]["items"]] == profile["plans"]  # 1번 행은 고른 사업계획
    assert [it["title"] for it in snap["slots"]["office_issues"]["items"]] == profile["issues"]  # 2번은 고른 현안(직접 추가 포함)
    assert [it["title"] for it in snap["slots"]["office_items"]["items"]] == [name for name, _ in OFFICE_ITEMS]  # 3번, 5~15번(8~10번 포함)
    asked = {g["slot"] for g in snap["gaps"]}
    assert asked <= {"office_plans", "office_issues", "office_pending"} and "office_items" not in asked  # 1·2·4번만 묻는다
    issue_questions = [g["question"] for g in snap["gaps"] if g["slot"] == "office_issues"]
    assert any("지금까지 어떤 진행 경과가 있었나요?" in q for q in issue_questions) and any("후임 관리자가 특별히 알아야 할 사항이 있나요?" in q for q in issue_questions)
    pending = [g for g in snap["gaps"] if g["slot"] == "office_pending"]
    assert len(pending) == 1 and pending[0]["question"].startswith("현재까지 처리되지 않았거나")  # <1> 미결 업무가 있는지
    doc = agent.finish("office1", FakeKB()).snapshot["document"]
    headings = [line for line in doc.splitlines() if line.startswith("## ")]
    assert headings[:4] == ["## 1. 주요 사업계획 및 집행상황표", "## 2. 주요 현안사항", "## 3. 제계약서 명세표", "## 4. 주요 미결사항의 경과 및 처리대책"]
    assert headings[4:] == [f"## {name}" for name, _ in OFFICE_ITEMS[1:]] + ["## 16. 부서·팀(반, 사무소)별 추가자료"]
    assert "<th rowspan=\"2\">주요 사업계획</th>" in doc and "<th rowspan=\"2\">비고</th>" in doc  # 단위업무명 → 주요 사업계획, 진행상황 요약 → 비고
    assert "### 1) 은행 공동 본인확인 플랫폼 구축 추진" in doc and "**가. 개요**" in doc and "**다. 고려사항**" in doc
    assert "| 대리점 운영 현황 | 자료 등록 필요 |" in doc and "| 판매 실적 현황 | 등록됨: 판매실적_2026.xlsx |" in doc
    assert "## 5. 중요 인장 보관 명세표\n\n□ 해당 없음  \n" in doc and "※" not in doc and "보관자 직책·성명" not in doc  # 비고는 문서에 싣지 않는다
    assert "인장명, 인영, 보관자 직책·성명" in OFFICE_SLOT.description and "원내 양식(팀장)" in doc and "정기업무" not in doc


def test_manager_form_without_dept_list_has_no_section_16():
    from datetime import date

    from amigo_agent import render_document

    md = render_document(profile={"form": "office", "plans": ["A 사업"]}, slots={}, gaps=[], sources=[], version=1, today=date(2026, 10, 28))
    assert "16. 부서" not in md and "| 주요 사업계획 | A 사업 |" in md


def test_leave_contacts_leave_out_the_givers_own_team():
    from amigo_agent.nodes import settle_items

    profile = {**LEAVE, "organization": "디지털전략부 · 웹서비스팀"}
    slot = {"items": [{"id": "c1", "title": "김하늘", "fields": {"org": "웹서비스팀", "name": "김하늘"}}, {"id": "c2", "title": "박지훈", "fields": {"org": "재무팀", "name": "박지훈"}}]}
    settle_items(SLOT_BY_KEY["contacts"], profile, slot)
    assert [it["id"] for it in slot["items"]] == ["c2"]


def test_regular_references_show_a_faded_hint_when_empty():
    from datetime import date

    from amigo_agent import render_document
    from amigo_agent.state import empty_slots

    slots = empty_slots()
    slots["regular"]["items"] = [{"id": "r1", "title": "월간 보고", "fields": {"name": "월간 보고"}, "citations": []}]
    md = render_document(profile={"name": "김민수"}, slots=slots, gaps=[], sources=[], version=1, today=date(2026, 10, 25))
    assert '<span class="doc-hint">인수자가 참고할 좋은 노하우, 팁, 참고자료가 있다면 작성해 주세요.</span>' in md


def test_manager_form_keeps_dates_and_asks_pending_items():
    from datetime import date

    from amigo_agent import render_document
    from amigo_agent.nodes import settle_items

    slot = {"items": [{"id": "p1", "title": "사업 A", "fields": {"name": "사업 A"}, "origin": "document"}, {"id": "p2", "title": "대금 지급", "fields": {"name": "대금 지급"}, "origin": "user"}]}
    settle_items(SLOT_BY_KEY["office_pending"], {"form": "office"}, slot)
    assert [it["id"] for it in slot["items"]] == ["p2"]  # 미결사항은 자료에서 만들지 않고 질의 답으로만
    issues = {"items": [{"id": "i1", "title": "현안", "fields": {"name": "현안", "progress": "2026.03 착수함 / 1. 2026.06 1차 오픈함"}}]}
    md = render_document(profile={"form": "office"}, slots={"office_issues": issues}, gaps=[], sources=[], version=1, today=date(2026, 10, 28))
    assert "□ 2026.03 착수함  " in md and "□ 2026.06 1차 오픈함  " in md


def test_manager_form_keeps_selected_names_on_answers():
    from amigo_agent.nodes import _apply_updates
    from amigo_agent.engine import Interpretation

    slots = {"office_issues": {"key": "office_issues", "items": [{"id": "i1", "title": "AMIGO 고도화", "fields": {"name": "AMIGO 고도화"}, "citations": []}]}}
    update = {"slot": "office_issues", "item_id": "i1", "title": "AMIGO 17개 팀 적용 지연 우려", "fields": {"name": "AMIGO 17개 팀 적용 지연 우려", "progress": "협의 중"}, "citations": []}
    _apply_updates(None, {"profile": {"form": "office"}}, slots, Interpretation(intent="answer", updates=[update]), "답", None)
    item = slots["office_issues"]["items"][0]
    assert item["title"] == "AMIGO 고도화" and item["fields"]["name"] == "AMIGO 고도화"

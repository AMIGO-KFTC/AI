"""노션 양식의 항목별 작성 기준(질문 예시·작성 예시·작성 가이드·질문 조건)이 분석·질문·답변 해석에 쓰이는지 확인한다."""

from amigo_agent.field_guides import GUIDES, first_ask_example, guide_for
from amigo_agent.prompts import ANALYSIS_SYSTEM, INTERPRET_SYSTEM, interpret_user, slot_spec_block
from amigo_agent.template import SLOT_BY_KEY

from conftest import PROFILE


def test_every_guide_points_at_a_real_field():
    for slot_key, fields in GUIDES.items():
        spec = SLOT_BY_KEY[slot_key]
        for key in fields:
            assert key == "*" or spec.field(key), (slot_key, key)


def test_analysis_prompt_carries_the_criteria_and_ask_type_of_each_field():
    block = slot_spec_block(SLOT_BY_KEY["regular"])
    decisions = block[block.index("- decisions("):block.index("- future(")]
    assert "작성 가이드:" in decisions and "판단 근거" in decisions and "질문 조건:" in decisions
    assert "구체적 질문 예시(질문 문장은 이 예시를 따름):" in decisions and "보완 질문 예시(참고용):" in decisions  # 질문은 구체적 질문 예시를 따르고 보완 질문 예시는 참고용
    assert "세부사항(정보 추출 기준): 결정 내용 / 결정 당시의 상황과 판단 근거" in decisions  # 1질문 1암묵지의 단위
    assert "자료에서 구체적인 사례를 찾으면 그 사례를 들어 바로 묻는 칸" in decisions
    assert "[묻지 않음(미질의)" in block[block.index("- overview("):block.index("- timing(")]
    assert "작성 예시: 1. 실적자료 요청 및 취합:" in block  # 수행 절차는 단계 번호 + 세부 설명 형식
    assert "이 장 전체의 작성 기준:" in slot_spec_block(SLOT_BY_KEY["partners"])  # 카드 안 표는 장 전체 기준
    assert "gaps 는 만들지 않습니다" in slot_spec_block(SLOT_BY_KEY["partners"])
    assert "질의응답으로만 채움" in slot_spec_block(SLOT_BY_KEY["leave_tasks"])
    assert "작성 기준" in ANALYSIS_SYSTEM and "1질문 1암묵지" in ANALYSIS_SYSTEM and "구체적 질문 예시(없으면 보완 질문 예시) 문장 그대로" in ANALYSIS_SYSTEM


def test_interpret_prompt_shows_the_criteria_and_the_missing_detail_of_the_current_question():
    gap = {"id": "gap-1", "slot": "irregular", "item_id": "", "question": "결정 이유가 뭔가요?", "field": "decisions", "element": "결정 배경과 판단 근거"}
    text = interpret_user(profile=PROFILE, stage="qna", current_gap=gap, pending_text="", slots={}, open_gaps=[], transcript=[], message="단계적으로 하기로 했어요")
    assert "## 현재 질문의 작성 기준(비정기 업무(프로젝트성))" in text and "검토한 주요 대안과 선택 이유" in text
    assert "- 묻는 세부사항: 결정 배경과 판단 근거" in text
    assert "followup" not in INTERPRET_SYSTEM  # 양식 밖 보충 질문은 만들지 않는다
    assert "기존 값 뒤에 답변 내용을 이어 붙인" in INTERPRET_SYSTEM


def test_interpret_prompt_names_the_card_of_a_collaboration_question():
    slots = {"regular": {"items": [{"id": "regular-1", "title": "차액 정산", "fields": {"name": "차액 정산"}}]}}
    gap = {"id": "gap-2", "slot": "partners", "item_id": "", "parent_id": "regular-1", "field": "note", "question": "누구와 협업할 때 무엇을 유의하나요?"}
    text = interpret_user(profile=PROFILE, stage="qna", current_gap=gap, pending_text="", slots=slots, open_gaps=[], transcript=[], message="재무팀 박지훈 차장, 마감 엄수")
    assert "- 상위 항목: 정기업무 '차액 정산'" in text and "task 에 '차액 정산'" in text


def test_code_made_question_uses_the_notion_question_example():
    assert first_ask_example("leave_tasks", "references") == "업무 처리를 위해 가장 빈번하게 참고하는 자료는 무엇인가요?"
    assert first_ask_example("leave_tasks", "cautions") == "인수자가 업무를 처리할 때 알고 있으면 좋을 사항이 무엇이 있나요?"
    assert first_ask_example("partners", "phone") == ""  # 장 전체 기준의 질문 예시는 한 칸만 묻는 질문에 쓰지 않는다
    assert guide_for("regular", "name") is None


def test_code_asks_one_field_per_question():
    from amigo_agent.nodes import _missing_field_gaps
    from amigo_agent.template import slots_for

    item = {"id": "regular-1", "title": "SSL 인증서 갱신", "fields": {"name": "SSL 인증서 갱신", "overview": "갱신", "timing": "12월", "procedure": "결제", "key_points": "만료 주의", "decisions": "자동 갱신", "references": "매뉴얼"}}
    made = _missing_field_gaps({"regular": {"items": [item]}}, [s for s in slots_for(PROFILE) if s.key == "regular"], [])
    assert [(g["field"], g["question"]) for g in made] == [("future", "'SSL 인증서 갱신' — 현재 담당 업무와 관련하여 향후 추진이 필요한 과제나 개선·발전이 필요한 사항이 있나요?")]
    assert made[0]["follow"] == "'SSL 인증서 갱신'의 향후 과제 및 발전 방향에 대해 여쭤볼게요.\n현재 진행 중이거나 해결되지 않은 과제는 무엇이며, 앞으로 어떤 방향으로 개선하면 좋을까요?"
    partner = {"id": "partners-1", "title": "홍보팀", "fields": {"org": "홍보팀", "name": "홍보팀", "role": "보도자료 확인", "task": "SSL 인증서 갱신"}}
    assert _missing_field_gaps({"partners": {"items": [partner]}}, [SLOT_BY_KEY["partners"]], []) == []  # 협업관계 표의 칸은 묻지 않는다


def test_code_made_question_follows_the_specific_question_example():
    from amigo_agent.nodes import _missing_field_gaps

    # 구체적 질문 예시가 있으면 보완 질문 예시 대신 그 문장으로 묻는다(보완 질문 예시는 참고용)
    item = {"id": "irregular-1", "title": "보고체계 개선", "fields": {"name": "보고체계 개선", "overview": "개선 사업임.", "decisions": "단계적 확대 결정함."}}
    made = _missing_field_gaps({"irregular": {"items": [item]}}, [SLOT_BY_KEY["irregular"]], [])
    key_points = next(g for g in made if g["field"] == "key_points")
    assert key_points["question"] == "'보고체계 개선'의 기타(중점 관리사항)에 대해 여쭤볼게요.\n프로젝트의 성공적인 추진을 위해 중점적으로 관리하거나 주의해야 할 사항은 무엇인가요?"
    assert key_points["aux"] is True
    # 자료를 전제하는 구체적 질문 예시(수행 절차의 순서 확인)뿐이면 보완 질문 예시로 묻는다
    assert first_ask_example("regular", "procedure") == "업무는 어떤 순서로 진행하며, 각 단계에서 무엇을 해야 하나요?"
    for slot_key, fields in GUIDES.items():
        for key, guide in fields.items():
            assert not guide.empty_ask or guide.empty_ask in guide.detail_examples, (slot_key, key)
            assert set(guide.ask_elements) <= set(guide.elements), (slot_key, key)


def test_bold_elements_are_required_and_asked_as_main_questions():
    from amigo_agent.field_guides import is_required_element
    from amigo_agent.nodes import element_is_aux

    timing = guide_for("regular", "timing")
    assert timing.required_elements == ("(필수) 업무 수행 주기(일간·주간·월간·분기·연간·상시 등)", "(필수) 주요 제출일 및 완료 기한")
    assert is_required_element("regular", "timing", "완료 기한") and not is_required_element("regular", "timing", "필수 중간 일정")
    assert is_required_element("regular", "procedure", "단계별 처리 순서") and not element_is_aux("regular", "procedure", "단계별 처리 순서")
    assert element_is_aux("regular", "procedure", "검토·확인 기준")
    assert len(guide_for("regular", "overview").required_elements) == 3  # 목적·주요 내용·최종 결과물(작성 가이드의 굵은 글씨)
    assert len(guide_for("irregular", "overview").required_elements) == 3 and len(guide_for("milestones").required_elements) == 3
    assert guide_for("regular", "procedure") is not None and guide_for("milestones").required
    block = slot_spec_block(SLOT_BY_KEY["regular"])
    assert "(필수) 업무 수행 주기" in block and "필수 항목(노션 양식 굵은 글씨)" in slot_spec_block(SLOT_BY_KEY["milestones"])
    assert "'(필수)'가 붙은 것은 노션 양식에서 굵은 글씨" in ANALYSIS_SYSTEM and "반드시 그 세부사항을 묻는 질문" in ANALYSIS_SYSTEM


def test_procedure_questions_follow_the_notion_row():
    from amigo_agent.field_guides import cap_element_questions
    from amigo_agent.nodes import allowed_gap

    def gap(element, question, field="procedure"):
        return {"id": question, "slot": "regular", "item_id": "regular-1", "field": field, "element": element, "question": question}

    proposals = [
        gap("단계별 주요 수행 활동 및 처리 순서", "순서 확인"),
        gap("각 단계에서 업무를 처리하기 위해 필요한 설명", "2단계 설명"),
        gap("각 단계에서 사용하는 주요 자료·양식·시스템", "양식"),
        gap("업무 처리에 필요한 주요 검토·확인 기준", "검토 기준"),
        gap("검토·확인 기준", "검토 기준 또"),
        gap("필수 검토·승인 및 결과 공유 절차", "승인 절차"),
        gap("처리 순서", "순서 또"),
        gap("특별한 유의사항", "지연 문제", field="key_points"),
        gap("특별한 유의사항", "집계 기준 문제", field="key_points"),
    ]
    kept = [g["question"] for g in cap_element_questions("regular", proposals)]
    assert kept == ["순서 확인", "검토 기준", "승인 절차", "지연 문제", "집계 기준 문제"]  # 수행 절차는 순서 확인 1개 + 목록의 세부사항만, 다른 칸은 그대로
    item = {"id": "regular-1", "title": "월간 보고", "fields": {"name": "월간 보고", "procedure": "1. 자료 요청 / 2. 보고서 작성", "key_points": "제출 지연 주의"}}
    slots = {"regular": {"items": [item]}}
    assert allowed_gap(proposals[0], slots, PROFILE) and allowed_gap(proposals[3], slots, PROFILE)
    assert not allowed_gap(proposals[1], slots, PROFILE) and not allowed_gap(proposals[2], slots, PROFILE)
    assert allowed_gap(proposals[7], slots, PROFILE)
    assert "구체적 질문 예시 첫째 문장 형식" in guide_for("regular", "procedure").ask_when and "단계마다 따로 묻는 질문은 만들지 않습니다" in ANALYSIS_SYSTEM


def test_prompts_say_values_follow_the_writing_example():
    from amigo_agent.prompts import AUTO_FILL_SYSTEM

    assert "작성 예시를 준용합니다" in ANALYSIS_SYSTEM and "'주요 의사결정: / 판단 근거:'" in ANALYSIS_SYSTEM
    assert "작성 예시를 준용합니다" in INTERPRET_SYSTEM and "'수행 주기:'" in INTERPRET_SYSTEM
    assert "형식(구조·순서·라벨)을 준용" in AUTO_FILL_SYSTEM
    assert "수행 주기: 매월 1회" in guide_for("regular", "timing").example
    assert "주요 의사결정:" in guide_for("irregular", "decisions").example and "판단 근거:" in guide_for("irregular", "decisions").example

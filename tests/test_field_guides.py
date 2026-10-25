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
    assert "작성 가이드:" in decisions and "판단 근거" in decisions and "질문 예시:" in decisions and "질문 조건:" in decisions
    assert "세부사항(정보 추출 기준): 결정 내용 / 결정 당시의 상황과 판단 근거" in decisions  # 1질문 1암묵지의 단위
    assert "자료에서 구체적인 사례를 찾으면 그 사례를 들어 바로 묻는 칸" in decisions
    assert "[묻지 않음(미질의)" in block[block.index("- overview("):block.index("- timing(")]
    assert "작성 예시: 1. 실적자료 요청 및 취합:" in block  # 수행 절차는 단계 번호 + 세부 설명 형식
    assert "이 장 전체의 작성 기준:" in slot_spec_block(SLOT_BY_KEY["partners"])  # 카드 안 표는 장 전체 기준
    assert "gaps 는 만들지 않습니다" in slot_spec_block(SLOT_BY_KEY["partners"])
    assert "질의응답으로만 채움" in slot_spec_block(SLOT_BY_KEY["leave_tasks"])
    assert "작성 기준" in ANALYSIS_SYSTEM and "1질문 1암묵지" in ANALYSIS_SYSTEM and "질문 예시 문장 그대로" in ANALYSIS_SYSTEM


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
    assert [(g["field"], g["question"]) for g in made] == [("future", "'SSL 인증서 갱신' — 이 업무의 향후 과제나 발전 방향이 있나요?")]
    assert made[0]["follow"] == "'SSL 인증서 갱신'의 향후 과제 및 발전 방향에 대해 여쭤볼게요. 현재 진행 중이거나 해결되지 않은 과제는 무엇이며, 앞으로 어떤 방향으로 개선하면 좋을까요?"
    partner = {"id": "partners-1", "title": "홍보팀", "fields": {"org": "홍보팀", "name": "홍보팀", "role": "보도자료 확인", "task": "SSL 인증서 갱신"}}
    assert _missing_field_gaps({"partners": {"items": [partner]}}, [SLOT_BY_KEY["partners"]], []) == []  # 협업관계 표의 칸은 묻지 않는다

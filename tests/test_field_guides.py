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


def test_analysis_prompt_carries_the_criteria_of_each_field():
    block = slot_spec_block(SLOT_BY_KEY["regular"])
    decisions = block[block.index("- decisions("):block.index("- future(")]
    assert "작성 가이드:" in decisions and "판단 근거" in decisions and "질문 예시:" in decisions and "질문 조건:" in decisions
    assert "작성 예시: 1. 경영방침 확인 → 2." in block  # 수행 절차는 단계 번호 형식
    assert "이 장 전체의 작성 기준:" in slot_spec_block(SLOT_BY_KEY["partners"])  # 카드 안 표는 장 전체 기준
    assert "작성 기준" in ANALYSIS_SYSTEM and "비어 있는 칸만 묻습니다" in ANALYSIS_SYSTEM


def test_interpret_prompt_shows_the_criteria_of_the_current_question():
    gap = {"id": "gap-1", "slot": "irregular", "item_id": "", "question": "결정 이유가 뭔가요?"}
    text = interpret_user(profile=PROFILE, stage="qna", current_gap=gap, pending_text="", slots={}, open_gaps=[], transcript=[], message="단계적으로 하기로 했어요")
    assert "## 현재 질문의 작성 기준(비정기 업무(프로젝트성))" in text and "검토한 대안과 선택 이유" in text
    assert "followup" not in INTERPRET_SYSTEM  # 양식 밖 보충 질문은 만들지 않는다


def test_code_made_question_uses_the_notion_question_example():
    assert first_ask_example("leave_tasks", "references") == "업무 중에 자주 열어보는 자료는 무엇인가요?"
    assert first_ask_example("partners", "phone") == ""  # 장 전체 기준의 질문 예시는 한 칸만 묻는 질문에 쓰지 않는다
    assert guide_for("regular", "name") is None


def test_code_asks_one_field_per_question():
    from amigo_agent.nodes import _missing_field_gaps
    from amigo_agent.template import slots_for

    item = {"id": "regular-1", "title": "SSL 인증서 갱신", "fields": {"name": "SSL 인증서 갱신", "overview": "갱신", "timing": "12월", "procedure": "결제", "key_points": "만료 주의", "references": "매뉴얼"}}
    made = _missing_field_gaps({"regular": {"items": [item]}}, [s for s in slots_for(PROFILE) if s.key == "regular"], [])
    assert [g["question"] for g in made] == [
        "'SSL 인증서 갱신'의 주요 의사결정 및 판단 근거에 대해 여쭤볼게요. 과거에 중요하게 결정한 사항은 무엇이며, 어떤 이유와 기준으로 결정했나요?",
        "'SSL 인증서 갱신'의 향후 과제 및 발전 방향에 대해 여쭤볼게요. 현재 진행 중이거나 해결되지 않은 과제는 무엇이며, 앞으로 어떤 방향으로 개선하면 좋을까요?",
    ]
    partner = {"id": "partners-1", "title": "홍보팀", "fields": {"org": "홍보팀", "name": "홍보팀", "role": "보도자료 확인", "note": "-", "task": "SSL 인증서 갱신"}}
    made = _missing_field_gaps({"partners": {"items": [partner]}}, [SLOT_BY_KEY["partners"]], [])
    assert [g["question"] for g in made] == ["'홍보팀'의 연락처를 알려 주시겠어요?"]

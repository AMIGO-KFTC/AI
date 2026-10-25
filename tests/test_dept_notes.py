"""부서・팀(반, 사무소)별 인수인계 목록(부팀명 / 목록 / 비고): 프롬프트 반영, 자료 검색어, 규칙 엔진의 확인 질문과 답변 정리."""

from conftest import PROFILE, FakeKB

from amigo_agent import HandoverAgent
from amigo_agent.dept_info import clean_dept_info, numbered, pick_questions, targets
from amigo_agent.prompts import analysis_user, dept_notes_block, interpret_user, plan_user
from amigo_agent.template import SLOT_BY_KEY

INFO = {
    "department": "디지털전략부 웹서비스팀",
    "items": ["홈페이지 유지보수 계약 명세표", "웹 접근성 인증 현황표", "도메인·SSL 인증서 등록 현황표", "홈페이지 관리자 계정 발급 명부"],
    "note": "웹 접근성 인증서 사본 첨부\n- 인증 유효기간, 갱신 예정일",
    "others": {"경영지원본부 재무팀": {"items": ["법인 예금계좌 현황표", "예금 잔액증명서"], "note": "잔액증명서 첨부\n- 금고 열쇠구분, 수량"}},
}
WITH_INFO = {**PROFILE, "dept_notes": INFO}


def test_clean_and_targets():
    messy = {"department": " 가 부서 ", "items": ["", "명세표", 3, "명세표", " 현황표 "], "note": "  첨부  \n\n- 열쇠구분, 수량 "}
    info = clean_dept_info(messy)
    assert info["items"] == ["명세표", "현황표"] and info["note"] == "첨부\n- 열쇠구분, 수량" and info["others"] == {}
    assert numbered(["a", "b", "c"]) == ["가. a", "나. b", "다. c"]
    found = targets(clean_dept_info(INFO))
    assert [t["kind"] for t in found] == ["item", "item", "item", "item", "note"]
    assert "'웹 접근성 인증서 사본 첨부'" in found[-1]["question"] and "인증 유효기간, 갱신 예정일" in found[-1]["question"]
    picked = pick_questions(clean_dept_info(INFO), 3)
    assert [t["name"] for t in picked] == ["홈페이지 유지보수 계약 명세표", "웹 접근성 인증 현황표", "웹 접근성 인증서 사본 첨부"]  # 비고는 꼭 넣는다
    assert [t["kind"] for t in pick_questions(clean_dept_info({**INFO, "note": ""}), 3)] == ["item"] * 3


def test_no_info_means_no_prompt_section():
    assert dept_notes_block(PROFILE) == "" and dept_notes_block({"dept_notes": {"items": [], "note": ""}}) == ""
    assert "인수인계 목록" not in analysis_user(SLOT_BY_KEY["dept_notes"], PROFILE, "근거")


def test_analysis_prompt_shows_numbered_list_and_note():
    text = analysis_user(SLOT_BY_KEY["dept_notes"], WITH_INFO, "근거")
    assert "우리 부서・팀 인수인계 목록" in text and "가. 홈페이지 유지보수 계약 명세표" in text and "라. 홈페이지 관리자 계정 발급 명부" in text
    assert "비고: 웹 접근성 인증서 사본 첨부 / - 인증 유효기간, 갱신 예정일" in text
    assert "관리 방식" in text and "gaps" in text and "근거 ID" in text
    assert "이미 있는 정보는 다시 묻지 않습니다" in text and "협업 부서" not in text


def test_collaborator_info_only_for_collaboration_slots_and_chat():
    assert "예금 잔액증명서" in dept_notes_block(WITH_INFO, slot_key="stakeholders")
    assert "금고 열쇠구분" in dept_notes_block(WITH_INFO, slot_key="stakeholders")
    assert "예금 잔액증명서" not in dept_notes_block(WITH_INFO, slot_key="systems")
    assert "예금 잔액증명서" in dept_notes_block(WITH_INFO)  # 대화 프롬프트


def test_chat_and_plan_prompts():
    chat = interpret_user(profile=WITH_INFO, stage="qna", current_gap=None, pending_text="", slots={}, open_gaps=[], transcript=[], message="네")
    assert "웹 접근성 인증 현황표" in chat and "updates" in chat
    plan = plan_user(WITH_INFO, [])
    assert "웹 접근성 인증 현황표" in plan and "중복으로 봅니다" in plan and "가. " not in plan  # 중복 찾기에는 이름만


def test_offline_engine_asks_management_of_uncovered_targets_and_turns_answers_into_items():
    agent = HandoverAgent()
    snap = agent.start("d1", WITH_INFO, FakeKB()).snapshot
    note_gaps = [g for g in snap["gaps"] if g.get("note_topic")]
    assert len(note_gaps) == 3 and all(g["slot"] == "dept_notes" for g in note_gaps)
    assert [g["note_topic"] for g in note_gaps][:3] == ["홈페이지 유지보수 계약 명세표", "웹 접근성 인증 현황표", "도메인·SSL 인증서 등록 현황표"]  # 목록 순서대로

    current = next(g for g in snap["gaps"] if g["id"] == snap["current_gap_id"])
    while not current.get("note_topic"):
        snap = agent.skip("d1", FakeKB()).snapshot
        current = next(g for g in snap["gaps"] if g["id"] == snap["current_gap_id"])
    topic = current["note_topic"]
    agent.send_message("d1", "공유드라이브에 보관하고 매년 12월에 갱신하며 담당자는 김민수 과장입니다", FakeKB())
    snap = agent.send_message("d1", "네", FakeKB()).snapshot
    item = next(it for it in snap["slots"]["dept_notes"]["items"] if it["title"] == topic)
    assert "12월에 갱신" in item["fields"]["detail"] and item["fields"]["topic"] == topic


def test_targets_found_in_documents_become_items_with_citations():
    docs = [{"file_name": "운영매뉴얼.pdf", "source_type": "pdf", "text": "웹 접근성 인증 현황표는 공유드라이브에 두고 매 분기 점검 후 박지훈 차장이 갱신한다."}]
    snap = HandoverAgent().start("d2", WITH_INFO, FakeKB(docs)).snapshot
    items = snap["slots"]["dept_notes"]["items"]
    assert any(it["title"] == "웹 접근성 인증 현황표" and it["citations"] for it in items)
    assert "웹 접근성 인증 현황표" not in {g.get("note_topic") for g in snap["gaps"]}

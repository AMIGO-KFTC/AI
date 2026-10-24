"""부서별 특이사항(부서 / 중요문서 위치 / 관리 가맹점 / 라이센스 업체): 프롬프트 반영, 자료 검색어, 규칙 엔진의 확인 질문과 답변 정리."""

from conftest import PROFILE, FakeKB

from amigo_agent import HandoverAgent
from amigo_agent.dept_info import clean_dept_info, pick_questions, targets
from amigo_agent.prompts import analysis_user, dept_notes_block, interpret_user, plan_user
from amigo_agent.template import SLOT_BY_KEY

INFO = {
    "department": "디지털전략부 웹서비스팀",
    "documents": [{"name": "웹 접근성 인증 신청서류", "location": "공유드라이브 \\\\ds-share\\웹서비스팀\\인증"}],
    "franchises": ["한울 제휴몰"],
    "licenses": [{"vendor": "한빛웹솔루션", "product": "홈페이지 CMS"}, {"vendor": "누리폰트", "product": "서체"}],
    "others": {"재무팀": {"documents": [{"name": "대금 지급 요청 대장", "location": "공유드라이브 재무팀"}], "franchises": [], "licenses": [{"vendor": "회계더하기", "product": "회계 시스템"}]}},
}
WITH_INFO = {**PROFILE, "dept_notes": INFO}


def test_clean_and_targets():
    messy = {"department": " 가 부서 ", "documents": [{"name": "", "location": "x"}, {"name": "문서", "location": "위치"}], "franchises": ["", "가맹점"], "licenses": [{"vendor": "업체"}, "문자열"]}
    info = clean_dept_info(messy)
    assert info["documents"] == [{"name": "문서", "location": "위치"}] and info["franchises"] == ["가맹점"] and info["licenses"] == [{"vendor": "업체", "product": ""}]
    kinds = [t["kind"] for t in targets(clean_dept_info(INFO))]
    assert kinds == ["license", "license", "franchise", "document"]
    assert [t["kind"] for t in pick_questions(clean_dept_info(INFO), 3)] == ["license", "franchise", "document"]  # 종류별로 번갈아
    assert all("위치" not in t["question"] and "공유드라이브" not in t["question"] for t in targets(clean_dept_info(INFO)))  # 이미 아는 정보는 묻지 않는다


def test_no_info_means_no_prompt_section():
    assert dept_notes_block(PROFILE) == "" and dept_notes_block({"dept_notes": {"documents": [], "franchises": [], "licenses": []}}) == ""
    assert "특이사항" not in analysis_user(SLOT_BY_KEY["dept_notes"], PROFILE, "근거")


def test_analysis_prompt_shows_facts_and_asks_for_management_not_known_info():
    text = analysis_user(SLOT_BY_KEY["dept_notes"], WITH_INFO, "근거")
    assert "우리 부서 특이사항" in text and "한울 제휴몰" in text and "한빛웹솔루션(홈페이지 CMS)" in text and "\\\\ds-share" in text
    assert "관리 방식" in text and "gaps" in text and "근거 ID" in text
    assert "이미 있는 정보는 다시 묻지 않습니다" in text and "협업 부서" not in text


def test_collaborator_info_only_for_collaboration_slots_and_chat():
    assert "회계더하기" in dept_notes_block(WITH_INFO, slot_key="stakeholders")
    assert "회계더하기" not in dept_notes_block(WITH_INFO, slot_key="systems")
    assert "회계더하기" in dept_notes_block(WITH_INFO)  # 대화 프롬프트


def test_chat_and_plan_prompts():
    chat = interpret_user(profile=WITH_INFO, stage="qna", current_gap=None, pending_text="", slots={}, open_gaps=[], transcript=[], message="네")
    assert "한울 제휴몰" in chat and "updates" in chat
    plan = plan_user(WITH_INFO, [])
    assert "한울 제휴몰" in plan and "중복으로 봅니다" in plan and "ds-share" not in plan  # 중복 찾기에는 이름만


def test_offline_engine_asks_management_of_uncovered_targets_and_turns_answers_into_items():
    agent = HandoverAgent()
    snap = agent.start("d1", WITH_INFO, FakeKB()).snapshot
    note_gaps = [g for g in snap["gaps"] if g.get("note_topic")]
    assert len(note_gaps) == 3 and all(g["slot"] == "dept_notes" for g in note_gaps)
    assert [g["note_topic"] for g in note_gaps][:3] == ["한빛웹솔루션", "한울 제휴몰", "웹 접근성 인증 신청서류"]  # 라이센스 → 가맹점 → 문서 순으로 번갈아

    current = next(g for g in snap["gaps"] if g["id"] == snap["current_gap_id"])
    while not current.get("note_topic"):
        snap = agent.skip("d1", FakeKB()).snapshot
        current = next(g for g in snap["gaps"] if g["id"] == snap["current_gap_id"])
    topic = current["note_topic"]
    agent.send_message("d1", "계약은 매년 12월에 갱신하고 담당자는 김민수 과장입니다", FakeKB())
    snap = agent.send_message("d1", "네", FakeKB()).snapshot
    item = next(it for it in snap["slots"]["dept_notes"]["items"] if it["title"] == topic)
    assert "12월에 갱신" in item["fields"]["detail"] and item["fields"]["topic"] == topic


def test_targets_found_in_documents_become_items_with_citations():
    docs = [{"file_name": "운영매뉴얼.pdf", "source_type": "pdf", "text": "한빛웹솔루션은 홈페이지 CMS 라이센스를 매년 12월에 갱신하며 담당자는 박지훈 차장이다."}]
    snap = HandoverAgent().start("d2", WITH_INFO, FakeKB(docs)).snapshot
    items = snap["slots"]["dept_notes"]["items"]
    assert any(it["title"] == "한빛웹솔루션" and it["citations"] for it in items)
    assert "한빛웹솔루션" not in {g.get("note_topic") for g in snap["gaps"]}

import pytest

from amigo_agent import AgentStateError, HandoverAgent

from conftest import PROFILE


def kinds(result):
    return [m["kind"] for m in result.messages]


def answered_first_question(kb):
    """첫 질문(비정기 프로젝트의 추진 경과)에 답하고 확인까지 마쳐, 두 번째 질문이 나온 상태로 만든다."""
    agent = HandoverAgent()
    first = agent.start("s1", PROFILE, kb).messages[-1]
    agent.send_message("s1", "3월 기본계획 수립 후 지금은 RFP를 작성하고 있습니다", kb)
    second = agent.send_message("s1", "네", kb).messages[-1]
    assert first["kind"] == second["kind"] == "question" and first["meta"]["gap_id"] != second["meta"]["gap_id"]
    return agent, first["meta"]["gap_id"], second["meta"]["gap_id"]


def test_reopen_asks_the_earlier_question_again_and_overwrites_the_answer(kb_ordered):
    kb = kb_ordered
    agent, first_id, second_id = answered_first_question(kb)

    result = agent.reopen_question("s1", first_id, kb)
    assert kinds(result) == ["question"]
    question = result.messages[0]
    assert question["meta"]["gap_id"] == first_id and "고르신 질문으로 이동했어요" in question["content"]
    snap = result.snapshot
    assert snap["waiting"] and snap["current_gap_id"] == first_id
    assert snap["question_count"] == 1  # 지난 질문 수 + 1: 첫 질문으로 돌아가면 1번째
    status = {g["id"]: g["status"] for g in snap["gaps"]}
    assert status[first_id] == "asked" and status[second_id] == "open"  # 묻던 질문은 순서를 기다린다

    # 새 답이 앞의 답을 대신한다
    result = agent.send_message("s1", "지금은 업체 선정을 마치고 개발 중입니다", kb)
    assert kinds(result) == ["confirm"] and "개발 중" in result.messages[0]["content"]
    items = [item for item in result.snapshot["slots"]["milestones"]["items"] if "개발 중" in str(item.get("fields"))]
    assert items, "고친 답이 슬롯에 반영돼야 한다"

    # 확인하면 원래 이어지던 질문으로 돌아간다
    result = agent.send_message("s1", "네", kb)
    assert kinds(result) == ["question"] and result.messages[0]["meta"]["gap_id"] == second_id
    assert result.snapshot["question_count"] == 2


def test_reopen_works_for_a_skipped_question(kb_ordered):
    kb = kb_ordered
    agent = HandoverAgent()
    first = agent.start("s1", PROFILE, kb).messages[-1]["meta"]["gap_id"]
    result = agent.skip("s1", kb)
    assert {g["id"]: g["status"] for g in result.snapshot["gaps"]}[first] == "skipped"
    result = agent.reopen_question("s1", first, kb)
    assert result.messages[-1]["meta"]["gap_id"] == first


def test_reopen_rejects_only_the_question_being_asked(kb_ordered):
    kb = kb_ordered
    agent, first_id, second_id = answered_first_question(kb)
    with pytest.raises(AgentStateError, match="지금 묻고 있는"):
        agent.reopen_question("s1", second_id, kb)  # 지금 묻고 있는 질문
    with pytest.raises(AgentStateError, match="찾지 못했습니다"):
        agent.reopen_question("s1", "no-such-gap", kb)


def test_jump_to_any_question_from_the_document_and_regenerate(kb_ordered):
    kb = kb_ordered
    agent, first_id, second_id = answered_first_question(kb)
    done = agent.finish("s1", kb)  # 문서 생성 → review 단계
    assert done.snapshot["stage"] == "review" and done.snapshot["document_version"] == 1

    # 최종 화면에서 '질의응답으로 돌아가기' 후 아직 답하지 않은 질문을 골라도 그 질문으로 이동한다
    result = agent.reopen_question("s1", second_id, kb)
    assert result.snapshot["stage"] == "qna" and result.messages[-1]["meta"]["gap_id"] == second_id
    assert "답하신 뒤 인수인계서를 다시 만들게요" in result.messages[-1]["content"]

    # 답하지 않고 건너뛰어도 다른 질문으로 넘어가지 않고 바로 문서를 다시 만든다
    result = agent.skip("s1", kb)
    assert result.snapshot["stage"] == "review" and result.snapshot["document_version"] == 2

    # 이미 답한 질문도 고를 수 있고, 고친 답으로 문서를 다시 만든다
    result = agent.reopen_question("s1", first_id, kb)
    assert result.messages[-1]["meta"]["gap_id"] == first_id
    agent.send_message("s1", "지금은 업체 선정을 마치고 개발 중입니다", kb)
    result = agent.send_message("s1", "네", kb)
    assert result.snapshot["stage"] == "review" and result.snapshot["document_version"] == 3
    assert any("개발 중" in str(it.get("fields")) for it in result.snapshot["slots"]["milestones"]["items"])  # 고친 답이 반영된 상태로 다시 만든다


def test_reopening_a_yes_no_question_asks_the_yes_no_question_again(kb):
    from test_offline_flow import UNITS

    agent = HandoverAgent()
    first = agent.start("g1", UNITS, kb).messages[-1]["meta"]["gap_id"]
    agent.send_message("g1", "아니요", kb)  # 중점 관리사항 없음
    second = agent.send_message("g1", "예", kb).messages[-1]  # 의사결정 있음 → 주관식으로 바뀐 질문
    assert second["meta"]["quick_replies"] != ["예", "아니요"]
    agent.skip("g1", kb)
    result = agent.reopen_question("g1", second["meta"]["gap_id"], kb)
    assert result.messages[-1]["meta"]["quick_replies"] == ["예", "아니요"] and "주요하게 결정한 사항이 있나요?" in result.messages[-1]["content"]
    assert first != second["meta"]["gap_id"]

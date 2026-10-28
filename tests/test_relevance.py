"""인계자 답변 처리: 상관있는 답인지·건너뛸지·값의 근거는 모델(Opus)의 판단을 따르고, 코드는 정해진 문구와 '해당 없음' 처리만 한다."""

from conftest import PROFILE

from amigo_agent import HandoverAgent


def _skipping_agent():
    """무엇이든 '모름(skip)'으로 판단하고, 건너뛰는 응답에 자기 질문을 끼워 넣는 모델(가짜)."""
    from amigo_agent import AgentConfig
    from amigo_agent.engine import Interpretation

    agent = HandoverAgent(AgentConfig(llm_mode="offline"))
    agent.engine.interpret = lambda data, kb_: Interpretation("skip", reply="알겠습니다. 그럼 지어낸 다른 질문은 무엇인가요?")
    return agent


def test_model_judgement_is_followed_without_code_filters(kb_ordered):
    """답이 질문과 상관있는지·건너뛸지·값이 말에 근거하는지는 모델(Opus)의 판단을 따른다(코드가 다시 막지 않는다)."""
    from amigo_agent import AgentConfig
    from amigo_agent.engine import Interpretation

    kb = kb_ordered
    agent = _skipping_agent()
    gap_id = agent.start("sk1", PROFILE, kb).messages[-1]["meta"]["gap_id"]
    result = agent.send_message("sk1", "그런 거 생각해 본 적 없는데", kb)  # 정해진 표현이 아니어도 모델이 건너뛰기로 보면 건너뛴다
    assert {g["id"]: g["status"] for g in result.snapshot["gaps"]}[gap_id] == "skipped"

    answering = HandoverAgent(AgentConfig(llm_mode="offline"))
    first = answering.start("ans", PROFILE, kb)
    current = next(g for g in first.snapshot["gaps"] if g["id"] == first.snapshot["current_gap_id"])
    value = "질문의 결정 내용과 함께 정리한 값(팀장 지시)"  # 인계자의 말과 글자가 거의 겹치지 않는 값
    answering.engine.interpret = lambda data, kb_: Interpretation(
        "answer", updates=[{"slot": current["slot"], "item_id": current["item_id"], "title": "", "fields": {"project": value}, "citations": []}], reply="정리했어요. 맞으면 '네'"
    )
    result = answering.send_message("ans", "팀장님이 그렇게 지시했어", kb)
    assert result.messages[-1]["kind"] == "confirm"  # 다시 묻지 않고 정리 내용을 확인받는다


def test_explicit_skip_still_skips_with_a_fixed_message_and_no_invented_question(kb_ordered):
    kb = kb_ordered
    agent = _skipping_agent()
    gap_id = agent.start("sk2", PROFILE, kb).messages[-1]["meta"]["gap_id"]
    result = agent.send_message("sk2", "모르겠어요 (건너뛰기)", kb)
    assert {g["id"]: g["status"] for g in result.snapshot["gaps"]}[gap_id] == "skipped"
    text = result.messages[-2]["content"]
    assert result.messages[-2]["kind"] == "info" and "건너뛰었어요" not in result.messages[-1]["content"]
    assert text.startswith("건너뛰었어요. '확인 필요 사항'으로 남겨 둘게요.") and "지어낸 다른 질문" not in text  # 모델이 끼워 넣은 질문은 쓰지 않는다


def test_model_saying_it_moves_on_while_staying_is_replaced(kb_ordered):
    """모델이 '다음 질문으로 넘어가겠습니다'라고 해도 실제로는 같은 질문에 머무므로 그 문장을 쓰지 않는다."""
    from amigo_agent import AgentConfig
    from amigo_agent.engine import Interpretation

    kb = kb_ordered
    agent = HandoverAgent(AgentConfig(llm_mode="offline"))
    agent.engine.interpret = lambda data, kb_: Interpretation("other", reply="알겠습니다. 그럼 다음 질문으로 넘어가겠습니다.")
    gap_id = agent.start("mv", PROFILE, kb).messages[-1]["meta"]["gap_id"]
    result = agent.send_message("mv", "ㅋㅋ", kb)
    text = result.messages[-1]["content"]
    assert "다음 질문으로 넘어가겠습니다" not in text and "넘어가지 않았어요" in text
    assert result.snapshot["current_gap_id"] == gap_id


def test_model_rephrasing_the_question_is_kept_and_only_one_question_shows(kb_ordered):
    from amigo_agent import AgentConfig
    from amigo_agent.engine import Interpretation

    kb = kb_ordered
    agent = HandoverAgent(AgentConfig(llm_mode="offline"))
    agent.engine.interpret = lambda data, kb_: Interpretation("other", reply="다시 말씀해 주시겠어요? 계획 기간이 궁금합니다.")
    first = agent.start("rp", PROFILE, kb)
    question = first.messages[-1]["content"]
    text = agent.send_message("rp", "ㅇㅇ", kb).messages[-1]["content"]
    assert text.startswith("다시 말씀해 주시겠어요?") and question not in text  # 모델의 되묻기는 그대로 쓰고 원래 질문은 덧붙이지 않는다
    friendly = HandoverAgent(AgentConfig(llm_mode="offline"))
    friendly.engine.interpret = lambda data, kb_: Interpretation("other", reply="편하신 때에 답해 주셔도 괜찮아요.")
    friendly.start("rp2", PROFILE, kb)
    assert friendly.send_message("rp2", "ㅋㅋ", kb).messages[-1]["content"].startswith("편하신 때에 답해 주셔도 괜찮아요.")  # 질문이 없는 짧은 응답은 그대로 쓴다


def test_none_answer_fills_the_cell_with_not_applicable_and_moves_on(kb):
    from amigo_agent.relevance import is_none_answer

    assert is_none_answer("그런 것은 없어.") and is_none_answer("딱히 없어요") and is_none_answer("해당 없음")
    assert not is_none_answer("모르겠어요") and not is_none_answer("문제는 없지만 마감일은 꼭 지켜야 해요")

    from amigo_agent.state import missing_fields
    from amigo_agent.template import SLOT_BY_KEY

    agent = HandoverAgent()
    snap = agent.start("none1", PROFILE, kb).snapshot
    config = agent._config("none1")
    values = agent.graph.get_state(config).values
    gaps = [dict(g) for g in values["gaps"]]
    target = next(g for g in gaps if g["id"] == values["current_gap_id"])  # 지금 묻는 질문을 한 칸(빈 필드)을 묻는 질문으로 만든다
    item = next(it for it in values["slots"][target["slot"]]["items"] if it["id"] == target["item_id"])
    target["field"] = missing_fields(SLOT_BY_KEY[target["slot"]], item)[0]
    agent.graph.update_state(config, {"gaps": gaps})
    result = agent.send_message("none1", "그런 것은 없어.", kb)
    gap = next(g for g in result.snapshot["gaps"] if g["id"] == target["id"])
    assert gap["status"] == "resolved"
    item = next(it for it in result.snapshot["slots"][target["slot"]]["items"] if it["id"] == target["item_id"])
    assert item["fields"][target["field"]] == "해당 없음"
    assert "'해당 없음'으로 기록했어요" in result.messages[0]["content"] and result.messages[0]["kind"] == "info"
    assert result.messages[1]["kind"] == "question" and "해당 없음" not in result.messages[1]["content"]  # 다음 질문에는 붙이지 않는다

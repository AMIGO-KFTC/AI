"""상관없는 답 거르기: 코드가 질문과 무관한 말·근거 없는 값을 한 번 더 검사해, 항목으로 기록하지 않고 같은 질문을 다시 묻는다."""

import pytest
from conftest import PROFILE

from amigo_agent import HandoverAgent
from amigo_agent.relevance import grounded_updates, off_topic_reason

GAP = {"id": "g1", "slot": "stakeholders", "item_id": "", "question": "재무팀과는 어느 프로젝트에서 협의하셨나요?", "description": "'재무팀'의 프로젝트명 정보 없음", "status": "asked"}


@pytest.mark.parametrize("text", ["오늘 점심 뭐 먹지", "ㅋㅋㅋㅋ", "안녕하세요", "...", "날씨 좋네요", "음", "테스트"])
def test_clearly_unrelated_words_are_rejected(text):
    assert off_topic_reason(text, GAP) is not None


@pytest.mark.parametrize(
    "text",
    [
        "홈페이지 리뉴얼 건입니다",  # 질문·필드 낱말과 무관해도 업무 이야기(길이·구체성)
        "예산 3억 원 협의했어요",  # 숫자
        "재무팀이랑은 리뉴얼 때문에요",  # 질문 낱말(재무팀)
        "내선 2345",
        "프로젝트는 개인정보처리방침 개정입니다",
        "그 건은 작년 11월에 협의했습니다 그리고 계약은 올해 12월에 갱신합니다",  # 긴 답은 검사하지 않는다
    ],
)
def test_real_answers_pass(text):
    assert off_topic_reason(text, GAP) is None


def test_no_question_means_no_check():
    assert off_topic_reason("오늘 점심", None) is None


def test_values_the_user_never_said_are_dropped_unless_backed_by_sources():
    spoken = "홈페이지 리뉴얼 건입니다"
    updates = [{"slot": "stakeholders", "item_id": "", "title": "재무팀", "fields": {"project": "홈페이지 리뉴얼", "discussed": "예산 3억 원 승인 완료"}, "citations": []}]
    kept = grounded_updates(updates, spoken)
    assert kept[0]["fields"] == {"project": "홈페이지 리뉴얼"}  # 말하지 않은 '예산 3억 원 승인 완료'는 지어낸 값
    assert grounded_updates([{**updates[0], "fields": {"discussed": "전혀 무관한 값입니다"}}], spoken) == []  # 남는 값이 없으면 항목도 없다
    sourced = [{**updates[0], "fields": {"discussed": "예산 3억 원 승인 완료"}, "citations": [{"label": "회의록.docx"}]}]
    assert grounded_updates(sourced, spoken) == sourced  # 자료를 근거로 쓴 값은 말에 없어도 된다
    assert grounded_updates([{**updates[0], "fields": {"timing": "3월~5월"}}], "3월부터 5월까지") != []  # 말을 정리한 값은 통과


def test_agent_asks_the_same_question_again_for_an_unrelated_answer_and_accepts_the_next_real_one(kb_ordered):
    kb = kb_ordered
    agent = HandoverAgent()
    first = agent.start("rl", PROFILE, kb)
    gap_id = first.messages[-1]["meta"]["gap_id"]
    count = first.snapshot["question_count"]

    result = agent.send_message("rl", "오늘 점심 뭐 먹지", kb)
    assert [m["kind"] for m in result.messages] == ["info"] and "기록하지 않았어요" in result.messages[0]["content"]
    assert "프로젝트" in result.messages[0]["content"] and "모르겠어요" in result.messages[0]["content"]  # 질문을 다시 보여 주고 건너뛰는 방법을 알려 준다
    snap = result.snapshot
    assert snap["waiting"] and snap["current_gap_id"] == gap_id and snap["question_count"] == count and not snap["pending"]
    status = {g["id"]: g["status"] for g in snap["gaps"]}
    assert status[gap_id] == "asked"  # 답으로 치지도, 건너뛴 것으로 치지도 않는다
    assert not any(it["fields"].get("project") == "오늘 점심 뭐 먹지" for it in snap["slots"]["stakeholders"]["items"])

    result = agent.send_message("rl", "홈페이지 리뉴얼 건입니다", kb)  # 진짜 답은 평소처럼 정리·확인으로 이어진다
    assert [m["kind"] for m in result.messages] == ["confirm"]
    again = agent.send_message("rl", "ㅋㅋㅋ", kb)  # 확인 대기 중의 의미 없는 말도 기록하지 않는다
    assert "기록하지 않았어요" in again.messages[0]["content"] or again.messages[0]["kind"] in ("confirm", "info")


def test_skip_still_works_after_an_unrelated_answer(kb_ordered):
    kb = kb_ordered
    agent = HandoverAgent()
    gap_id = agent.start("rl2", PROFILE, kb).messages[-1]["meta"]["gap_id"]
    agent.send_message("rl2", "하하하 안녕", kb)
    result = agent.send_message("rl2", "모르겠어요", kb)
    assert {g["id"]: g["status"] for g in result.snapshot["gaps"]}[gap_id] == "skipped"


def test_claude_misreading_an_unrelated_message_as_an_answer_is_caught_by_the_code_check(kb_ordered):
    """모델(가짜)이 '점심 얘기'를 프로젝트명 답으로 정리해 버려도 코드 검사가 기록을 막는다."""
    from amigo_agent import AgentConfig
    from amigo_agent.engine import Interpretation

    kb = kb_ordered
    agent = HandoverAgent(AgentConfig(llm_mode="offline"))
    real = agent.engine.interpret

    def credulous(data, kb_):  # 무엇이든 답으로 받아 정리하는 모델
        if data.message.strip() in ("네",):
            return real(data, kb_)
        gap = data.current_gap
        return Interpretation("answer", updates=[{"slot": gap["slot"], "item_id": gap.get("item_id", ""), "title": "재무팀", "fields": {"project": "오로라 프로젝트 승인"}, "citations": []}], resolved_gap_ids=[gap["id"]], reply="정리했어요.")

    agent.engine.interpret = credulous
    agent.start("rl3", PROFILE, kb)
    result = agent.send_message("rl3", "저는 오늘 점심에 김치찌개를 먹었어요", kb)  # 모델은 답으로 정리하지만 값이 말에 근거가 없다
    assert [m["kind"] for m in result.messages] == ["info"] and "기록하지 않았어요" in result.messages[0]["content"]
    assert not any("오로라" in str(it["fields"]) for it in result.snapshot["slots"]["stakeholders"]["items"])


@pytest.mark.parametrize("text", ["모르겠어요 (건너뛰기)", "몰라요", "잘 모르겠습니다", "건너뛸게요", "패스", "해당 없음", "없어요", "기억이 안 나요", "다음 질문으로 넘어가 주세요"])
def test_explicit_skip_expressions(text):
    from amigo_agent.relevance import is_explicit_skip

    assert is_explicit_skip(text)


@pytest.mark.parametrize("text", ["ㅇㅇ", "음", "네", "글쎄", "ㅋㅋ", "흠..."])
def test_vague_words_are_not_a_skip(text):
    from amigo_agent.relevance import is_explicit_skip

    assert not is_explicit_skip(text)


def _skipping_agent():
    """무엇이든 '모름(skip)'으로 판단하고, 건너뛰는 응답에 자기 질문을 끼워 넣는 모델(가짜)."""
    from amigo_agent import AgentConfig
    from amigo_agent.engine import Interpretation

    agent = HandoverAgent(AgentConfig(llm_mode="offline"))
    agent.engine.interpret = lambda data, kb_: Interpretation("skip", reply="알겠습니다. 그럼 지어낸 다른 질문은 무엇인가요?")
    return agent


def test_model_skip_for_a_vague_word_is_turned_into_asking_again(kb_ordered):
    kb = kb_ordered
    agent = _skipping_agent()
    first = agent.start("sk1", PROFILE, kb)
    gap_id, count = first.messages[-1]["meta"]["gap_id"], first.snapshot["question_count"]
    for text in ("ㅇㅇ", "음"):
        result = agent.send_message("sk1", text, kb)
        assert [m["kind"] for m in result.messages] == ["info"] and "넘어가지 않았어요" in result.messages[0]["content"]
        snap = result.snapshot
        assert {g["id"]: g["status"] for g in snap["gaps"]}[gap_id] == "asked" and snap["current_gap_id"] == gap_id and snap["question_count"] == count


def test_explicit_skip_still_skips_with_a_fixed_message_and_no_invented_question(kb_ordered):
    kb = kb_ordered
    agent = _skipping_agent()
    gap_id = agent.start("sk2", PROFILE, kb).messages[-1]["meta"]["gap_id"]
    result = agent.send_message("sk2", "모르겠어요 (건너뛰기)", kb)
    assert {g["id"]: g["status"] for g in result.snapshot["gaps"]}[gap_id] == "skipped"
    text = result.messages[-1]["content"]
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


def test_model_rephrasing_the_question_is_replaced_so_only_one_question_shows(kb_ordered):
    from amigo_agent import AgentConfig
    from amigo_agent.engine import Interpretation

    kb = kb_ordered
    agent = HandoverAgent(AgentConfig(llm_mode="offline"))
    agent.engine.interpret = lambda data, kb_: Interpretation("other", reply="다시 말씀해 주시겠어요? 계획 기간이 궁금합니다.")
    first = agent.start("rp", PROFILE, kb)
    text = agent.send_message("rp", "ㅇㅇ", kb).messages[-1]["content"]
    assert "다시 말씀해 주시겠어요" not in text and text.count("넘어가지 않았어요") == 1
    friendly = HandoverAgent(AgentConfig(llm_mode="offline"))
    friendly.engine.interpret = lambda data, kb_: Interpretation("other", reply="편하신 때에 답해 주셔도 괜찮아요.")
    friendly.start("rp2", PROFILE, kb)
    assert friendly.send_message("rp2", "ㅋㅋ", kb).messages[-1]["content"].startswith("편하신 때에 답해 주셔도 괜찮아요.")  # 질문이 없는 짧은 응답은 그대로 쓴다

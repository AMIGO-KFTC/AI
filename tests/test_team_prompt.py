"""팀장이 정한 프롬프트가 팀원의 AI 질문 생성·순서 정하기·안내문·대화에 반영되고, 분석 뒤에 고쳐도 다음 턴부터 반영되는지."""

from conftest import PROFILE, FakeKB

from amigo_agent import HandoverAgent
from amigo_agent.prompts import analysis_user, interpret_user, plan_user, summary_user, team_prompt_block
from amigo_agent.template import SLOT_BY_KEY

PROMPT = "인수자가 첫 주에 해야 할 일 중심으로 묻고, 금액과 기한은 반드시 확인한다."
WITH_PROMPT = {**PROFILE, "team_guidelines": {"items": [], "notes": "", "prompt": PROMPT}}


def test_team_prompt_reaches_every_question_related_prompt():
    assert PROMPT in analysis_user(SLOT_BY_KEY["regular"], WITH_PROMPT, "근거")
    assert "질문(gaps)에도 적용" in analysis_user(SLOT_BY_KEY["regular"], WITH_PROMPT, "근거")
    chat = interpret_user(profile=WITH_PROMPT, stage="qna", current_gap=None, pending_text="", slots={}, open_gaps=[], transcript=[], message="네")
    assert PROMPT in chat
    assert PROMPT not in plan_user(WITH_PROMPT, [])  # 질문 순서는 양식의 장 순서로 정해지므로 중복 찾기에는 팀 프롬프트를 주지 않는다
    assert PROMPT in summary_user(WITH_PROMPT, {}, [])


def test_no_prompt_changes_nothing():
    assert team_prompt_block(None) == "" and team_prompt_block({"items": [{"name": "항목"}], "prompt": ""}) == ""
    assert "팀 전용 프롬프트" not in plan_user(PROFILE, []) and "팀 전용 프롬프트" not in summary_user(PROFILE, {}, [])


def test_update_profile_applies_a_prompt_saved_after_analysis():
    agent = HandoverAgent()
    agent.start("tp", PROFILE, FakeKB())
    assert agent.update_profile("tp", {"team_guidelines": WITH_PROMPT["team_guidelines"]}) is True
    assert agent.update_profile("tp", {"team_guidelines": WITH_PROMPT["team_guidelines"]}) is False  # 같은 값이면 바뀐 것 없음
    values = agent.graph.get_state(agent._config("tp")).values
    assert values["profile"]["team_guidelines"]["prompt"] == PROMPT and values["profile"]["name"] == PROFILE["name"]  # 다른 인계자 정보는 그대로
    assert agent.update_profile("tp", {"team_guidelines": None}) is True  # 지침을 지우면 키도 지운다
    assert "team_guidelines" not in agent.graph.get_state(agent._config("tp")).values["profile"]
    assert agent.update_profile("없는 대화", {"team_guidelines": {}}) is False

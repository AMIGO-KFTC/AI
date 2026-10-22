"""정리한 내용이 맞는지 물었을 때 '네, 맞아요' 같은 확인 답은 LLM 에 묻지 않고 바로 확정하고 다음 질문으로 넘어간다."""

from amigo_agent import HandoverAgent
from amigo_agent.nodes import is_plain_confirm

from conftest import PROFILE


def test_plain_confirm_phrases():
    for text in ["네", "네, 맞아요", " 네  맞아요 ", "맞습니다.", "OK", "확인했어요"]:
        assert is_plain_confirm(text), text
    for text in ["네, 그리고 내선은 3311이에요", "아니요", "수정할게요", "네 맞는데 이름은 이수진입니다", ""]:
        assert not is_plain_confirm(text), text


def test_quick_reply_confirm_moves_to_next_question(kb):
    agent = HandoverAgent()
    agent.start("c1", PROFILE, kb)
    result = agent.send_message("c1", "CD공동망 고도화 프로젝트에서 협의했습니다", kb)
    assert [m["kind"] for m in result.messages] == ["confirm"]
    gap_id = result.snapshot["current_gap_id"]

    result = agent.send_message("c1", "네, 맞아요", kb)  # 화면의 빠른 답변 단추 문구 그대로
    assert [m["kind"] for m in result.messages] == ["question"]
    assert result.messages[0]["content"].startswith("**[")  # 이전 답변을 다시 정리하는 문구 없이 질문만
    snap = result.snapshot
    assert not snap["pending"] and snap["current_gap_id"] != gap_id
    assert next(g for g in snap["gaps"] if g["id"] == gap_id)["status"] == "resolved"

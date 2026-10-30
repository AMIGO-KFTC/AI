"""이미지(스크린샷·대화 캡처) 속 글자 읽기: 모델 선택, 요청 모양, 긴 이미지가 잘린 경우."""

from types import SimpleNamespace

import pytest

from amigo_agent.config import AgentConfig
from amigo_agent.vision import MAX_OUTPUT_TOKENS, PROMPT, TRUNCATED, make_image_reader


class FakeClient:
    def __init__(self, text="2026년 10월 28일 수요일\n오후 2:14 박지훈 과장: 마감이 25일로 당겨졌어요.", stop_reason="end_turn"):
        self.calls = []
        self.response = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_vision_model_is_opus_by_default_and_independent_of_the_chat_model(monkeypatch):
    monkeypatch.delenv("AMIGO_VISION_MODEL", raising=False)
    monkeypatch.setenv("AMIGO_LLM_MODEL", "claude-haiku-4-5")  # 대화용을 싸게 써도 이미지 읽기는 정확한 모델로
    config = AgentConfig()
    assert config.model == "claude-haiku-4-5" and config.vision_model == "claude-opus-5-5"


def test_vision_model_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("AMIGO_VISION_MODEL", "claude-sonnet-5-5")
    assert AgentConfig().vision_model == "claude-sonnet-5-5"


def test_reader_sends_the_image_with_the_chat_capture_prompt_and_returns_the_text():
    client = FakeClient()
    read = make_image_reader(AgentConfig(vision_model="claude-opus-5-5"), client=client)
    assert read(b"\x89PNG-bytes", "image/png") == "2026년 10월 28일 수요일\n오후 2:14 박지훈 과장: 마감이 25일로 당겨졌어요."
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["max_tokens"] == MAX_OUTPUT_TOKENS >= 16000
    image, text = call["messages"][0]["content"]
    assert image["source"]["media_type"] == "image/png" and image["source"]["data"]
    assert text["text"] == PROMPT
    # 대화 캡처에서 누가 언제 말했는지, 내 메시지, 틀리면 안 되는 값을 요청한다
    for needle in ("보낸 사람", "'나'", "날짜 구분선", "한 글자씩", "[판독 불가]"):
        assert needle in PROMPT


def test_effort_is_sent_only_to_models_that_accept_it():
    opus, haiku = FakeClient(), FakeClient()
    make_image_reader(AgentConfig(vision_model="claude-opus-5-5"), client=opus)(b"x", "image/png")
    make_image_reader(AgentConfig(vision_model="claude-haiku-4-5"), client=haiku)(b"x", "image/png")
    assert opus.calls[0]["output_config"] == {"effort": "low"}
    assert "output_config" not in haiku.calls[0]  # Haiku 4.5 는 effort 를 보내면 400


def test_truncated_output_is_reported_instead_of_silently_dropping_the_rest():
    client = FakeClient(text="앞부분만…", stop_reason="max_tokens")
    read = make_image_reader(AgentConfig(vision_model="claude-opus-5-5"), client=client)
    with pytest.raises(RuntimeError) as error:
        read(b"x", "image/png")
    assert str(error.value) == TRUNCATED and "나눠서" in TRUNCATED


def test_image_without_text_returns_an_empty_string():
    read = make_image_reader(AgentConfig(vision_model="claude-opus-5-5"), client=FakeClient(text="  "))
    assert read(b"x", "image/png") == ""

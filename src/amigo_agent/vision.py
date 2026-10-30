"""이미지(스크린샷) 속 글자 읽기. Claude 비전으로 이미지를 텍스트로 옮겨 RAG 의 이미지 파서에 건넨다."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

from .config import AgentConfig
from .llm import model_features

PROMPT = (
    "이 이미지는 업무 화면 캡처나 사진입니다. 이미지에 보이는 글자를 순서대로 빠짐없이 그대로 옮겨 적어 주세요.\n"
    "- 표는 마크다운 표로, 제목·목록은 보이는 구조대로 적습니다.\n"
    "- 메신저 대화 캡처이면 메시지마다 한 줄로 '시각 보낸 사람: 내용' 형식으로 적습니다"
    "(예: '오후 2:14 박지훈 과장: 마감이 25일로 당겨졌어요.'). 말풍선 안의 줄바꿈은 한 문장으로 이어 붙입니다. "
    "이름이 없는 말풍선(주로 화면 오른쪽, 다른 색)은 보낸 사람을 '나'로 적고, 같은 사람이 이어서 보낸 말풍선은 이름이 한 번만 보여도 같은 이름을 붙입니다. "
    "날짜 구분선('2026년 10월 28일 수요일')은 보이는 위치에 한 줄로 그대로 적고, 첨부 파일 이름·링크·답장(인용)한 글도 빠뜨리지 않습니다.\n"
    "- 사람 이름·숫자·날짜·금액·전화번호·메일 주소·시스템 이름은 한 글자씩 정확히 옮깁니다. "
    "흐리거나 잘려서 읽을 수 없는 글자는 추측하지 말고 [판독 불가]로 적습니다.\n"
    "- 해설·요약·추측은 붙이지 말고, 이미지에 있는 글자만 적습니다.\n"
    "- 글자가 전혀 없으면 빈 응답으로 둡니다."
)

MAX_OUTPUT_TOKENS = 16000  # 긴 대화 캡처도 끝까지 옮길 수 있게 넉넉히 둔다(예전 4096 은 긴 대화에서 조용히 잘렸다)
TRUNCATED = "이미지가 길어서 끝까지 읽지 못했어요.\n화면을 나눠서(예: 절반씩 캡처해) 여러 장으로 올려 주세요."


def make_image_reader(config: AgentConfig | None = None, client: Any | None = None) -> Callable[[bytes, str], str]:
    """(이미지 바이트, 형식) → 읽은 글자. 클라이언트는 처음 읽을 때 만든다(자격증명이 없으면 그때 오류)."""
    cfg = config or AgentConfig()
    holder: dict[str, Any] = {"client": client}

    def read(data: bytes, media_type: str) -> str:
        import anthropic

        if holder["client"] is None:
            holder["client"] = anthropic.Anthropic(timeout=cfg.request_timeout, max_retries=2)
        kwargs: dict[str, Any] = {}
        if "effort" in model_features(cfg.vision_model):
            kwargs["output_config"] = {"effort": "low"}  # 옮겨 적기는 오래 생각할 일이 아니다(Opus 는 thinking 을 끌 수 없어 effort 로 줄인다)
        try:
            response = holder["client"].messages.create(
                model=cfg.vision_model,
                max_tokens=MAX_OUTPUT_TOKENS,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode("ascii")}},
                        {"type": "text", "text": PROMPT},
                    ],
                }],
                **kwargs,
            )
        except anthropic.AuthenticationError as exc:
            raise RuntimeError("Claude API 인증에 실패했습니다.\nANTHROPIC_API_KEY 를 확인해 주세요.") from exc
        except anthropic.APIError as exc:
            raise RuntimeError(f"Claude 호출에 실패했습니다({exc.__class__.__name__}).") from exc
        if getattr(response, "stop_reason", None) == "max_tokens":  # 끊긴 글을 그대로 쓰면 뒷부분이 조용히 빠진다
            raise RuntimeError(TRUNCATED)
        return "".join(getattr(block, "text", "") for block in response.content if getattr(block, "type", None) == "text").strip()

    return read

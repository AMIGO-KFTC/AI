"""이미지(스크린샷) 속 글자 읽기. Claude 비전으로 이미지를 텍스트로 옮겨 RAG 의 이미지 파서에 건넨다."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

from .config import AgentConfig

PROMPT = (
    "이 이미지는 업무 화면 캡처나 사진입니다. 이미지에 보이는 글자를 순서대로 빠짐없이 그대로 옮겨 적어 주세요.\n"
    "- 표는 마크다운 표로, 제목·목록은 보이는 구조대로 적습니다.\n"
    "- 해설·요약·추측은 붙이지 말고, 이미지에 있는 글자만 적습니다.\n"
    "- 글자가 전혀 없으면 빈 응답으로 둡니다."
)


def make_image_reader(config: AgentConfig | None = None, client: Any | None = None) -> Callable[[bytes, str], str]:
    """(이미지 바이트, 형식) → 읽은 글자. 클라이언트는 처음 읽을 때 만든다(자격증명이 없으면 그때 오류)."""
    cfg = config or AgentConfig()
    holder: dict[str, Any] = {"client": client}

    def read(data: bytes, media_type: str) -> str:
        import anthropic

        if holder["client"] is None:
            holder["client"] = anthropic.Anthropic(timeout=cfg.request_timeout, max_retries=2)
        try:
            response = holder["client"].messages.create(
                model=cfg.vision_model,
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode("ascii")}},
                        {"type": "text", "text": PROMPT},
                    ],
                }],
            )
        except anthropic.AuthenticationError as exc:
            raise RuntimeError("Claude API 인증에 실패했습니다.\nANTHROPIC_API_KEY 를 확인해 주세요.") from exc
        except anthropic.APIError as exc:
            raise RuntimeError(f"Claude 호출에 실패했습니다({exc.__class__.__name__}).") from exc
        return "".join(getattr(block, "text", "") for block in response.content if getattr(block, "type", None) == "text").strip()

    return read

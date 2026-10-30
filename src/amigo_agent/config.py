"""에이전트 설정(환경변수 기반)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# 모든 Claude 호출(분석·질문·답변 해석·챗봇·이미지 읽기)은 기본으로 Claude Opus 5.5 를 쓴다.
# 비용을 줄이려면 AMIGO_LLM_MODEL=claude-sonnet-5-5(약 절반), claude-haiku-4-5(가장 저렴하지만 이름·숫자를 자주 틀린다)
DEFAULT_MODEL = "claude-opus-5-5"
VISION_DEFAULT_MODEL = "claude-opus-5-5"  # 이미지(스크린샷) 속 글자 읽기 전용(AMIGO_VISION_MODEL 로 따로 바꿀 수 있다)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class AgentConfig:
    # auto: Anthropic 자격증명이 있으면 Claude, 없으면 규칙 기반 오프라인 엔진
    llm_mode: str = field(default_factory=lambda: os.getenv("AMIGO_LLM_MODE", "auto").lower())
    model: str = field(default_factory=lambda: os.getenv("AMIGO_LLM_MODEL", DEFAULT_MODEL))
    # 스크린샷(PNG·JPG) 속 글자를 읽을 때 쓰는 모델. 대화용 모델(AMIGO_LLM_MODEL)과 따로 정하고 기본은 Opus 다.
    # 이름·숫자·연락처를 한 글자라도 틀리면 인수인계서에 틀린 사실이 들어가므로 읽기는 가장 정확한 모델로 한다(Haiku 는 사람 이름도 틀렸다).
    # 비용을 줄이려면 AMIGO_VISION_MODEL=claude-sonnet-5-5(약 절반).
    vision_model: str = field(default_factory=lambda: os.getenv("AMIGO_VISION_MODEL") or VISION_DEFAULT_MODEL)
    # Claude Opus 5.5 는 thinking 을 끌 수 없고 effort 로 깊이를 조절한다(기본 medium).
    effort_analysis: str = field(default_factory=lambda: os.getenv("AMIGO_EFFORT_ANALYSIS", "medium"))
    effort_chat: str = field(default_factory=lambda: os.getenv("AMIGO_EFFORT_CHAT", "low"))
    # 안전 분류기가 요청을 거절하면 서버 측에서 다른(권장) 모델로 바꿔 재시도하는 기능(server-side fallbacks).
    # 모든 호출을 Opus 로만 보내려고 기본은 끈다. 거절될 때 다른 모델로라도 이어 가려면 AMIGO_LLM_FALLBACKS=true.
    use_fallbacks: bool = field(default_factory=lambda: _env_bool("AMIGO_LLM_FALLBACKS", False))
    # 질문 수는 제한하지 않는다(암묵지를 확인할 질문은 모두 묻는다). 아래 값은 규칙 엔진(오프라인)의 장별 질문 후보 수에만 쓴다.
    max_gaps_per_slot: int = field(default_factory=lambda: _env_int("AMIGO_MAX_GAPS_PER_SLOT", 2))
    max_concurrency: int = field(default_factory=lambda: _env_int("AMIGO_MAX_CONCURRENCY", 3))
    search_top_k: int = field(default_factory=lambda: _env_int("AMIGO_SEARCH_TOP_K", 6))
    # search_documents 도구 결과에서 질문과 관련 없다고 판정된 조각을 숨기고 "자료에 없을 가능성이 큼"이라고 알린다.
    # (장별 기본 검색은 여러 주제를 묶은 넓은 탐색이라 이 판정을 적용하지 않는다.)
    hide_irrelevant: bool = field(default_factory=lambda: _env_bool("AMIGO_HIDE_IRRELEVANT", True))
    max_tool_rounds: int = field(default_factory=lambda: _env_int("AMIGO_MAX_TOOL_ROUNDS", 3))
    request_timeout: float = field(default_factory=lambda: float(os.getenv("AMIGO_LLM_TIMEOUT", "300")))

    def resolved_mode(self) -> str:
        if self.llm_mode in ("claude", "offline"):
            return self.llm_mode
        if any(os.getenv(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")):
            return "claude"
        if (Path.home() / ".config" / "anthropic").is_dir():  # `ant auth login` 프로필
            return "claude"
        return "offline"

"""에이전트 설정(환경변수 기반)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"


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
    # Claude Opus 5.5 는 thinking 을 끌 수 없고 effort 로 깊이를 조절한다(기본 medium).
    effort_analysis: str = field(default_factory=lambda: os.getenv("AMIGO_EFFORT_ANALYSIS", "medium"))
    effort_chat: str = field(default_factory=lambda: os.getenv("AMIGO_EFFORT_CHAT", "low"))
    effort_compose: str = field(default_factory=lambda: os.getenv("AMIGO_EFFORT_COMPOSE", "medium"))
    # 안전 분류기가 요청을 거절하면 서버 측에서 권장 모델로 재시도(server-side fallbacks)
    use_fallbacks: bool = field(default_factory=lambda: _env_bool("AMIGO_LLM_FALLBACKS", True))
    max_questions: int = field(default_factory=lambda: _env_int("AMIGO_MAX_QUESTIONS", 12))
    max_gaps_per_slot: int = field(default_factory=lambda: _env_int("AMIGO_MAX_GAPS_PER_SLOT", 2))
    max_concurrency: int = field(default_factory=lambda: _env_int("AMIGO_MAX_CONCURRENCY", 3))
    search_top_k: int = field(default_factory=lambda: _env_int("AMIGO_SEARCH_TOP_K", 6))
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

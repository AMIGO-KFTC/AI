"""Claude 호출 래퍼(공식 Anthropic Python SDK).

- 구조화 출력: output_config.format(JSON 스키마)으로 최종 응답을 Pydantic 모델로 받는다.
- 도구 호출(Function Calling): 모델이 search_documents 를 부르면 RAG 검색을 실행해 tool_result 로 돌려주는
  수동 루프. 도구 결과에 thinking 블록이 섞여 있어도 응답 content 를 그대로 다시 넣는다(append-only).
- Claude Opus 5.5 는 thinking 을 끌 수 없으므로 thinking 파라미터는 보내지 않고 effort 로 깊이를 조절한다.
- 안전 분류기 거절에 대비해 서버 측 fallbacks("default")를 기본으로 켠다(AMIGO_LLM_FALLBACKS=false 로 끔).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from .config import AgentConfig

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# 비용 추정용 단가(USD / 100만 토큰): 입력, 출력, 캐시 쓰기(5분), 캐시 읽기. 표에 없는 모델은 비용을 계산하지 않는다.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 5.0, 0.20)}
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


class UsageMeter:
    """여러 스레드(슬롯 병렬 분석)에서 부르는 API 호출의 토큰 사용량을 모은다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests = 0
        self.tokens = dict.fromkeys(USAGE_KEYS, 0)

    def add(self, usage: Any) -> dict[str, int]:
        counted = {k: int(getattr(usage, k, 0) or 0) for k in USAGE_KEYS}
        with self._lock:
            self.requests += 1
            for k, v in counted.items():
                self.tokens[k] += v
        return counted

    def summary(self, model: str) -> dict[str, Any]:
        with self._lock:
            out: dict[str, Any] = {"requests": self.requests, **self.tokens}
        cost = estimate_usd(model, out)
        if cost is not None:
            out["estimated_usd"] = round(cost, 4)
        return out


def estimate_usd(model: str, tokens: dict[str, int]) -> float | None:
    """토큰 수로 추정 비용(USD)을 계산한다. 단가표(PRICES)에 없는 모델이면 None."""
    price = PRICES.get(model)
    if not price:
        return None
    return (
        tokens.get("input_tokens", 0) * price[0] + tokens.get("output_tokens", 0) * price[1]
        + tokens.get("cache_creation_input_tokens", 0) * price[2] + tokens.get("cache_read_input_tokens", 0) * price[3]
    ) / 1_000_000


def _emit_usage(model: str, counted: dict[str, int]) -> None:
    """LangGraph 실행 중이면 사용량을 이벤트로 흘려 보낸다(백엔드가 세션별로 누적). 밖에서 부르면 무시."""
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:  # 그래프 밖(평가 스크립트 등)에서 호출된 경우
        return
    cost = estimate_usd(model, counted)
    writer({"type": "usage", "model": model, "requests": 1, **counted, "cost_usd": round(cost or 0.0, 6)})

SEARCH_TOOL = {
    "name": "search_documents",
    "description": (
        "인계자가 올린 업무 자료(문서·메일·링크·이전 답변)를 검색합니다. 제공된 근거만으로 부족하거나, "
        "자료에 이름만 나온 시스템·과제·부서·담당자의 세부 내용을 확인해야 할 때 호출하세요. "
        "결과는 [E번호] 출처와 함께 반환되며, 항목의 근거로는 이 번호만 쓸 수 있습니다."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "검색어. 예: '홈페이지 유지보수 업체 담당자 연락처'"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


class LLMError(RuntimeError):
    """사용자에게 보여줄 수 있는 한국어 메시지를 담는다."""


class LLMRefusal(LLMError):
    pass


class LLMOutputError(LLMError):
    pass


ToolHandler = Callable[[str, dict[str, Any]], str]


class ClaudeLLM:
    def __init__(self, config: AgentConfig, client: Any | None = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(timeout=config.request_timeout, max_retries=3)
        self.client = client
        self.config = config
        self.usage = UsageMeter()

    def structured(
        self,
        *,
        system: str,
        user: str,
        output_model: type[BaseModel],
        effort: str,
        max_tokens: int = 16000,
        tools: list[dict] | None = None,
        tool_handler: ToolHandler | None = None,
        max_tool_rounds: int = 3,
    ) -> BaseModel:
        """시스템/사용자 프롬프트로 Claude 를 호출하고, 필요하면 도구를 실행한 뒤 구조화된 결과를 반환한다."""
        import anthropic

        schema = anthropic.transform_schema(output_model)
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        tool_rounds = 0
        for _ in range(max_tool_rounds + 4):
            response = self._create(system=system, messages=messages, schema=schema, effort=effort, max_tokens=max_tokens, tools=tools)
            stop = getattr(response, "stop_reason", None)
            if stop == "refusal":
                raise LLMRefusal("Claude 가 이 요청에 대한 응답을 거절했습니다. 자료 내용을 확인해 주세요.")
            if stop == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            if stop == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": self._run_tools(response.content, tool_handler, tool_rounds >= max_tool_rounds)})
                tool_rounds += 1
                continue
            if stop == "max_tokens":
                raise LLMOutputError("Claude 응답이 너무 길어 잘렸습니다. 다시 시도해 주세요.")
            return parse_output(output_model, final_text(response.content))
        raise LLMOutputError("도구 호출이 반복되어 결과를 받지 못했습니다.")

    def _run_tools(self, content: list[Any], handler: ToolHandler | None, exhausted: bool) -> list[dict[str, Any]]:
        results = []
        for block in content:
            if getattr(block, "type", None) != "tool_use":
                continue
            if exhausted or handler is None:
                text, is_error = "검색 한도에 도달했습니다. 지금까지 확보한 근거만으로 최종 결과를 작성하세요.", True
            else:
                try:
                    text, is_error = handler(block.name, dict(block.input or {})), False
                except Exception as exc:  # 도구 오류는 모델에게 알려 스스로 복구하게 한다
                    logger.warning("tool %s failed: %s", block.name, exc)
                    text, is_error = f"도구 실행 중 오류가 발생했습니다: {exc}", True
            result = {"type": "tool_result", "tool_use_id": block.id, "content": text}
            if is_error:
                result["is_error"] = True
            results.append(result)
        return results

    def _create(self, *, system: str, messages: list, schema: dict, effort: str, max_tokens: int, tools: list[dict] | None):
        import anthropic

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            # 시스템 프롬프트는 슬롯·턴이 바뀌어도 같으므로 캐시하고, 도구 루프의 이전 턴은 자동 캐시로 재사용한다.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            kwargs["tools"] = tools
        if self.config.use_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        try:
            response = self.client.beta.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise LLMError("Claude API 인증에 실패했습니다. ANTHROPIC_API_KEY 를 확인해 주세요.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError("Claude API 사용 권한이 없습니다. API 키의 권한을 확인해 주세요.") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"모델을 찾을 수 없습니다: {self.config.model} (AMIGO_LLM_MODEL 확인)") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Claude API 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.") from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"Claude API 요청 형식 오류: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API 오류가 발생했습니다(HTTP {exc.status_code}). 잠시 후 다시 시도해 주세요.") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Claude API 에 연결하지 못했습니다. 네트워크(프록시) 설정을 확인해 주세요.") from exc
        usage = getattr(response, "usage", None)
        if usage is not None:
            counted = self.usage.add(usage)
            logger.info("claude %s stop=%s tokens=%s", effort, getattr(response, "stop_reason", None), counted)
            _emit_usage(self.config.model, counted)
        return response


def final_text(content: list[Any]) -> str:
    texts = [getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text"]
    return texts[-1] if texts else ""


def parse_output(model: type[BaseModel], text: str) -> BaseModel:
    try:
        return model.model_validate_json(text)
    except (ValidationError, ValueError):
        match = re.search(r"\{.*\}", text or "", re.S)
        if match:
            try:
                return model.model_validate(json.loads(match.group(0)))
            except (ValidationError, ValueError):
                pass
    raise LLMOutputError("Claude 응답을 해석하지 못했습니다(JSON 형식 오류).")

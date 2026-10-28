"""Model boundary: DeepSeek through the openai SDK, or an injected scripted client.

Only the interface is product code; transport details stay behind ``complete``.
The SDK auto-retry is disabled and thinking is explicitly non-thinking.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openai import OpenAI

from cta.config import Config


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def as_openai(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True)
class ModelResponse:
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    model: str | None = None
    usage: dict | None = None
    error: str | None = None
    mock: bool = False
    elapsed_ms: int | None = None
    raw_id: str | None = None


class ModelUnavailable(Exception):
    pass


class ModelClient(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], *, cursor: int) -> ModelResponse: ...


class DeepSeekClient:
    def __init__(self, config: Config):
        if not config.api_key:
            raise ModelUnavailable("DEEPSEEK_API_KEY 未配置，不能进行真实模型调用。")
        self.config = config
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=float(os.environ.get("CTA_LLM_TIMEOUT", "180")),
            max_retries=0,
        )

    def complete(self, messages: list[dict], tools: list[dict], *, cursor: int) -> ModelResponse:
        started = time.time()
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                stream=False,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception as exc:  # noqa: BLE001 - boundary records the real error
            message = self.config.redact(f"{type(exc).__name__}: {exc}")[:800]
            return ModelResponse(
                error=message,
                model=self.config.model,
                elapsed_ms=int((time.time() - started) * 1000),
            )

        choice = response.choices[0]
        message = choice.message
        calls = tuple(
            ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments or "{}")
            for call in (message.tool_calls or ())
        )
        usage = None
        if response.usage is not None:
            dumped = response.usage.model_dump() if hasattr(response.usage, "model_dump") else dict(response.usage)
            usage = {
                "prompt_tokens": dumped.get("prompt_tokens"),
                "completion_tokens": dumped.get("completion_tokens"),
                "total_tokens": dumped.get("total_tokens"),
                "prompt_cache_hit_tokens": dumped.get("prompt_cache_hit_tokens"),
                "prompt_cache_miss_tokens": dumped.get("prompt_cache_miss_tokens"),
                "raw": dumped,
            }
        return ModelResponse(
            content=message.content,
            tool_calls=calls,
            finish_reason=choice.finish_reason,
            model=response.model,
            usage=usage,
            elapsed_ms=int((time.time() - started) * 1000),
            raw_id=response.id,
        )


class ScriptedClient:
    """Deterministic injection at the model boundary, for offline tests.

    The script is a JSON array; the cursor is the task's used-round count so a
    real new process continues at the same position.
    """

    def __init__(self, path: Path, redact=None):
        self.path = Path(path)
        self.redact = redact or (lambda text: text)
        if not self.path.is_file():
            raise ModelUnavailable(f"CTA_MODEL_SCRIPT 指向的脚本不存在：{self.path}")

    def _items(self) -> list[dict]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelUnavailable(f"模型脚本无法读取：{exc}") from exc
        if not isinstance(payload, list):
            raise ModelUnavailable("模型脚本必须是 JSON 数组。")
        return payload

    def complete(self, messages: list[dict], tools: list[dict], *, cursor: int) -> ModelResponse:
        items = self._items()
        if cursor >= len(items):
            return ModelResponse(error="model_unavailable: scripted responses exhausted", model="scripted", mock=True)
        item = items[cursor]
        kind = item.get("type", "tool")
        if kind == "error":
            return ModelResponse(
                error=self.redact(str(item.get("message", "scripted model error"))),
                model="scripted",
                mock=True,
            )
        if kind == "text":
            return ModelResponse(
                content=item.get("content", ""),
                finish_reason=item.get("finish_reason", "stop"),
                model="scripted",
                usage=item.get("usage"),
                mock=True,
            )
        if kind == "length":
            return ModelResponse(
                content=item.get("content", ""),
                finish_reason="length",
                model="scripted",
                usage=item.get("usage"),
                mock=True,
            )
        if kind == "multi":
            calls = tuple(
                ToolCall(
                    id=call.get("id", f"call_{cursor + 1}_{index}"),
                    name=call["name"],
                    arguments=call.get("arguments", "{}")
                    if isinstance(call.get("arguments", "{}"), str)
                    else json.dumps(call.get("arguments", {}), ensure_ascii=False),
                )
                for index, call in enumerate(item["calls"])
            )
            return ModelResponse(
                content=item.get("content"),
                tool_calls=calls,
                finish_reason="tool_calls",
                model="scripted",
                usage=item.get("usage"),
                mock=True,
            )
        arguments = item.get("arguments", {})
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        return ModelResponse(
            content=item.get("content"),
            tool_calls=(
                ToolCall(id=item.get("id", f"call_{cursor + 1}"), name=item["name"], arguments=arguments),
            ),
            finish_reason="tool_calls",
            model="scripted",
            usage=item.get("usage"),
            mock=True,
        )


def create_client(config: Config) -> ModelClient:
    if config.mode == "mock":
        script = os.environ.get("CTA_MODEL_SCRIPT")
        if not script:
            raise ModelUnavailable("Mock 模式需要 CTA_MODEL_SCRIPT 注入模型响应脚本。")
        return ScriptedClient(Path(script), redact=config.redact)
    return DeepSeekClient(config)

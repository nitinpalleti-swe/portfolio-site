"""Ask Nitin — the only module that talks to an LLM provider.

Provider and model names come from .env (LLM_PROVIDER, CHAT_MODEL, FIT_MODEL), so another
provider can be added here without touching app.py.
"""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")


# ── Model calls: the ONE place that talks to an LLM provider ──────────────────
# Provider and model names come from .env. Everything provider-specific (request
# shape, response blocks, tool-result format) stays in this section, so another
# provider can be added here without touching the chat loop below.

class ModelRefusal(Exception):
    pass


@dataclass
class ModelTurn:
    stop_reason: str            # "end_turn", "tool_use", "max_tokens", ...
    tool_calls: list[dict]      # [{"id", "name", "input"}]
    assistant_message: dict     # append to messages as-is (keeps thinking/tool blocks unchanged)


_client = None


async def stream_model(model: str, system: str, messages: list[dict], tools: list[dict] | None = None,
                       tool_choice: dict | None = None, max_tokens: int = 2048) -> AsyncIterator[tuple[str, object]]:
    """Yields ("text", str) while the reply streams, then one ("turn", ModelTurn)."""
    global _client
    if LLM_PROVIDER == "anthropic":
        import anthropic

        _client = _client or anthropic.AsyncAnthropic(timeout=20.0, max_retries=1)  # reads ANTHROPIC_API_KEY
        extra = {"tools": tools} if tools else {}
        if tool_choice:
            extra["tool_choice"] = tool_choice
        async with _client.messages.stream(
            model=model,
            max_tokens=max_tokens,  # includes adaptive-thinking tokens, so leave headroom
            output_config={"effort": "low"},
            # Cache tools + the frozen system prompt; per-request sources go in the user turn, after it.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            **extra,
        ) as stream:
            async for text in stream.text_stream:
                yield "text", text
            final = await stream.get_final_message()
        if final.stop_reason == "refusal":
            raise ModelRefusal()
        u = final.usage
        calls = [{"id": b.id, "name": b.name, "input": b.input} for b in final.content if b.type == "tool_use"]
        print(f"[llm] {model} in={u.input_tokens} out={u.output_tokens} cache_write={u.cache_creation_input_tokens} "
              f"cache_read={u.cache_read_input_tokens} stop={final.stop_reason} tools={[c['name'] for c in calls]}")
        yield "turn", ModelTurn(final.stop_reason, calls, {"role": "assistant", "content": final.content})
    else:
        raise RuntimeError(f"LLM_PROVIDER={LLM_PROVIDER!r} is not implemented yet")


async def structured_model(model: str, system: str, user: str, schema: type[BaseModel], max_tokens: int = 16000) -> BaseModel:
    """One non-streaming call whose reply is validated against a Pydantic schema (structured output)."""
    global _client
    if LLM_PROVIDER == "anthropic":
        import anthropic

        _client = _client or anthropic.AsyncAnthropic(timeout=20.0, max_retries=1)
        resp = await _client.with_options(timeout=90.0).beta.messages.parse(
            model=model,
            max_tokens=max_tokens,  # includes adaptive-thinking tokens
            output_config={"effort": "medium"},
            output_format=schema,
            # Server-side fallback: if a safety classifier declines, the API retries on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        u = resp.usage
        print(f"[llm] {resp.model} in={u.input_tokens} out={u.output_tokens} cache_write={u.cache_creation_input_tokens} "
              f"cache_read={u.cache_read_input_tokens} stop={resp.stop_reason}")
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise ModelRefusal()
        return resp.parsed_output
    raise RuntimeError(f"LLM_PROVIDER={LLM_PROVIDER!r} is not implemented yet")


def tool_results_message(results: list[tuple[str, str, bool]]) -> dict:
    """All tool results for one assistant turn go back in a single user message."""
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": content, "is_error": is_error}
        for call_id, content, is_error in results
    ]}


def model_configured() -> bool:
    if LLM_PROVIDER == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    return False

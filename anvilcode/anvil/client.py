"""HuggingFace router client (OpenAI-compatible) with streaming + graceful param fallback."""
from __future__ import annotations

import time
from collections import namedtuple

BASE_URL = "https://router.huggingface.co/v1"

# GLM reasoning effort: the API understands low/medium/high; "max" = high + a system nudge.
EFFORT_API = {"low": "low", "medium": "medium", "high": "high", "max": "high"}


class ToolsUnsupported(Exception):
    """The chosen provider rejected the tools parameter — caller should fall back to JSON mode."""


StreamResult = namedtuple("StreamResult", "content reasoning tool_calls usage finish_reason")


def consume_stream(chunks, emit) -> StreamResult:
    """Turn an iterable of OpenAI-style chunks into a StreamResult, emitting UI events."""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    usage = None
    finish_reason = None

    for chunk in chunks:
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        if not getattr(chunk, "choices", None):
            continue
        choice = chunk.choices[0]
        if choice.finish_reason:
            finish_reason = choice.finish_reason
        delta = choice.delta
        if delta is None:
            continue
        rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if rc:
            reasoning_parts.append(rc)
            emit(("reason", rc))
        if delta.content:
            content_parts.append(delta.content)
            emit(("content", delta.content))
        for tc in (getattr(delta, "tool_calls", None) or []):
            slot = tool_calls.setdefault(getattr(tc, "index", 0) or 0, {"id": "", "name": "", "args": ""})
            if getattr(tc, "id", None):
                slot["id"] = tc.id
            fn = getattr(tc, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    slot["name"] += fn.name
                if getattr(fn, "arguments", None):
                    slot["args"] += fn.arguments

    calls = [
        (slot["id"] or f"call_{i}", slot["name"], slot["args"])
        for i, slot in sorted(tool_calls.items())
    ]
    return StreamResult("".join(content_parts), "".join(reasoning_parts), calls, usage, finish_reason)


class HFClient:
    def __init__(self, api_key: str, on_status=None):
        from openai import OpenAI
        self.client = OpenAI(base_url=BASE_URL, api_key=api_key, timeout=240.0, max_retries=1)
        self.on_status = on_status or (lambda msg: None)
        self.effort_ok = True
        self.stream_opts_ok = True

    def validate(self) -> list[str]:
        """Cheap GET /v1/models — raises on a bad token."""
        return [m.id for m in self.client.models.list()]

    # ---------------- streaming ----------------

    def stream(self, *, model: str, messages: list[dict], tools=None,
               effort: str | None = None, on_status=None) -> StreamResult:
        emit = on_status or self.on_status
        kwargs = {"model": model, "messages": messages, "stream": True}
        if self.stream_opts_ok:
            kwargs["stream_options"] = {"include_usage": True}
        if effort and self.effort_ok and effort in EFFORT_API:
            kwargs["reasoning_effort"] = EFFORT_API[effort]
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        retries = 0
        while True:
            try:
                return self._do_stream(emit, **kwargs)
            except ToolsUnsupported:
                raise
            except Exception as e:
                kind = type(e).__name__
                msg = str(e)
                lower = msg.lower()
                if kind == "BadRequestError":
                    if "reasoning_effort" in kwargs and (
                        "reasoning_effort" in lower or "effort" in lower or "reasoning" in lower
                    ):
                        self.effort_ok = False
                        kwargs.pop("reasoning_effort", None)
                        emit("provider rejected reasoning_effort — retrying without it")
                        continue
                    if "stream_options" in kwargs and "stream_options" in lower:
                        self.stream_opts_ok = False
                        kwargs.pop("stream_options", None)
                        emit("provider rejected stream_options — retrying without it")
                        continue
                    if ("tools" in kwargs) and ("tools" in lower or "tool_choice" in lower):
                        raise ToolsUnsupported(msg)
                    raise
                if kind == "RateLimitError" or "rate limit" in lower or "429" in lower:
                    retries += 1
                    if retries > 4:
                        raise
                    emit(f"rate limited — waiting 15s (attempt {retries}/4)")
                    time.sleep(15)
                    continue
                if kind in ("InternalServerError", "APIConnectionError", "APITimeoutError") or "502" in lower \
                        or "503" in lower or "524" in lower:
                    emit(f"{kind} — retrying in 5s")
                    time.sleep(5)
                    continue
                raise

    def _do_stream(self, emit, **kwargs) -> StreamResult:
        stream = self.client.chat.completions.create(**kwargs)
        return consume_stream(stream, emit)

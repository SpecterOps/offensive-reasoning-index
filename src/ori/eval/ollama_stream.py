"""Network-free validation of native Ollama chat stream framing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .provider_contract import ProviderGenerationError, ProviderProtocolError

_INVALID_STREAM = "Ollama stream is incomplete or malformed"
_METRICS = (
    "prompt_eval_count",
    "eval_count",
    "total_duration",
    "load_duration",
    "prompt_eval_duration",
    "eval_duration",
)


def _reject_constant(value: str) -> None:
    raise ProviderProtocolError(_INVALID_STREAM)


@dataclass(slots=True)
class OllamaStreamState:
    """Admit a turn only after one terminal marker and a complete HTTP body."""

    terminal_seen: bool = False
    done_reason: str = ""

    def feed_line(self, line: str) -> dict[str, Any] | None:
        if not line.strip():
            return None
        if self.terminal_seen:
            raise ProviderProtocolError(_INVALID_STREAM)
        try:
            data = json.loads(line, parse_constant=_reject_constant)
        except json.JSONDecodeError as exc:
            raise ProviderProtocolError(_INVALID_STREAM) from exc
        if not isinstance(data, dict):
            raise ProviderProtocolError(_INVALID_STREAM)
        if "error" in data:
            raise ProviderGenerationError("Ollama reported a generation error")
        if "done" in data and type(data["done"]) is not bool:
            raise ProviderProtocolError(_INVALID_STREAM)
        for name in ("model", "done_reason"):
            if data.get(name) is not None and not isinstance(data[name], str):
                raise ProviderProtocolError(_INVALID_STREAM)
        message = data.get("message")
        if message is not None:
            if not isinstance(message, dict):
                raise ProviderProtocolError(_INVALID_STREAM)
            for name in ("content", "thinking"):
                if message.get(name) is not None and not isinstance(message[name], str):
                    raise ProviderProtocolError(_INVALID_STREAM)
            calls = message.get("tool_calls")
            if calls is not None:
                if not isinstance(calls, list) or any(
                    not isinstance(call, dict) or not isinstance(call.get("function"), dict)
                    for call in calls
                ):
                    raise ProviderProtocolError(_INVALID_STREAM)
        if data.get("done") is True:
            try:
                for name in _METRICS:
                    data[name] = int(data.get(name) or 0)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ProviderProtocolError(_INVALID_STREAM) from exc
            self.terminal_seen = True
            self.done_reason = data.get("done_reason") or ""
        return data

    def finish(self) -> str:
        if not self.terminal_seen:
            raise ProviderProtocolError(_INVALID_STREAM)
        return self.done_reason

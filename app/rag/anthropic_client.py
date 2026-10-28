"""Claude as the triage LLM (``LLM_PROVIDER=anthropic``).

Structured outputs constrain the reply to the report's JSON schema, so the
grounding checks in ``parse_report`` see valid JSON instead of prose. Server-
side fallbacks reroute a request a safety classifier declines; if the whole
chain still refuses, the call raises and triage writes the rule-based report.
"""

from __future__ import annotations

from typing import Any, Literal

from app.rag.budget import LLMReply

DEFAULT_MODEL = "claude-opus-5-5"

REPORT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "root_cause": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "runbook": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "evidence_event_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": [
        "summary",
        "root_cause",
        "confidence",
        "runbook",
        "next_steps",
        "evidence_event_ids",
    ],
    "additionalProperties": False,
}


class ClaudeRefusedError(RuntimeError):
    """Every model in the fallback chain declined the request."""


class AnthropicClient:
    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        timeout: float = 60.0,
        max_tokens: int = 4000,
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium",
        client: Any = None,  # tests pass a fake
    ) -> None:
        import anthropic

        self.model = model
        self._max_tokens = max_tokens
        self._effort = effort
        # An empty key falls through to the SDK's own credential lookup
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key or None, timeout=timeout, max_retries=2
        )

    async def complete(self, system: str, user: str) -> LLMReply:
        response = await self._client.beta.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": REPORT_SCHEMA},
            },
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise ClaudeRefusedError(str(getattr(response, "stop_details", None)))
        text = "".join(getattr(b, "text", "") for b in response.content if b.type == "text")
        return LLMReply(text, response.usage.input_tokens, response.usage.output_tokens)

    async def complete_json(self, system: str, user: str) -> str:
        return (await self.complete(system, user)).text

"""LLM summarizer – produce incident summaries and root-cause analysis (Phase 4 stub)."""

from __future__ import annotations

from typing import Any


async def summarize_anomaly(
    log_lines: list[str],
    runbook_context: list[str],
    anomaly_info: dict[str, Any],
) -> dict[str, str]:
    """Placeholder – will call OpenAI / local LLM in Phase 4.

    Returns:
        {"summary": "...", "root_cause": "..."}
    """
    raise NotImplementedError("LLM summarizer is not yet implemented (Phase 4).")

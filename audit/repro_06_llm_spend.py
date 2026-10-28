"""Nothing bounds LLM spend: every new incident is one paid call, uncapped.

Error bursts on N different service names → N incidents → N LLM calls. The
settings have no budget, call cap or per-day limit.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

from _harness import api, verdict

# A cap the operator would set; ignored by code that has no budget guard
os.environ["LLM_MAX_CALLS_PER_DAY"] = "10"
os.environ["OPENAI_API_KEY"] = "audit-fake"  # makes the app build its OpenAI client

import app.rag.summarizer as summarizer  # noqa: E402
from app.core.config import Settings  # noqa: E402


class CountingLLM:
    """Stands in for the provider client; the app wires it exactly as the real one."""

    model = "counting-fake"
    calls = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def complete_json(self, system: str, user: str) -> str:
        CountingLLM.calls += 1
        return '{"summary": "s", "root_cause": "r", "confidence": "low"}'


async def main() -> None:
    budget_fields = [f for f in Settings.model_fields if "budget" in f or "max_llm" in f]
    print("budget-like settings:", budget_fields or "none")
    summarizer.OpenAICompatibleClient = CountingLLM
    start = datetime.now(tz=UTC).replace(second=0, microsecond=0) - timedelta(minutes=40)
    n = 25
    async with api() as c:
        await c.post("/api/v1/runbooks/index")
        batch = []
        for i in range(n):
            for m in range(40):
                for s in (0, 20, 40):
                    lvl = "ERROR" if m >= 35 else "INFO"
                    ts = (start + timedelta(minutes=m, seconds=s)).isoformat()
                    batch.append(f"{ts} svc-{i} {lvl} upstream timeout")
        await c.post("/api/v1/ingest", json={"lines": batch})
        r = await c.post("/api/v1/detect", json={"detectors": ["error_burst"]})
        ids = [a["id"] for a in r.json()["anomalies"]]
        for i in ids:
            await c.post(f"/api/v1/anomalies/{i}/triage")
    print(f"{len(ids)} anomalies on {n} services → {CountingLLM.calls} LLM calls")
    verdict(CountingLLM.calls > 10, "LLM calls exceed the configured daily cap of 10")


asyncio.run(main())

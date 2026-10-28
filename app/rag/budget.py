"""Daily LLM spend guard, shared by every API and worker process.

Before each call, the worst case (prompt estimate + the output-token cap) is
reserved against today's row in ``llm_usage`` in a single conditional upsert,
so concurrent workers can't both squeeze past the limit. If the reservation
would cross the call or token limit, the call is not made: ``BudgetExceededError``
is raised and triage writes the rule-based report instead. After a call the
reservation is corrected to the usage the provider reports.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.core.metrics import LLM_CALLS, LLM_TOKENS
from app.models.llm_usage import LLMUsage

log = get_logger(__name__)


class BudgetExceededError(RuntimeError):
    """Today's LLM call or token limit would be crossed."""


@dataclass(frozen=True, slots=True)
class LLMReply:
    text: str
    input_tokens: int
    output_tokens: int


@runtime_checkable
class ReportsUsage(Protocol):
    async def complete(self, system: str, user: str) -> LLMReply: ...


class _LLM(Protocol):
    model: str

    async def complete_json(self, system: str, user: str) -> str: ...


def estimate_tokens(text: str) -> int:
    # ~4 characters per token for English; 3 errs on the side of reserving more
    return len(text) // 3 + 1


def _today() -> date:
    return datetime.now(tz=UTC).date()


async def reserve(
    session: AsyncSession, day: date, tokens: int, max_calls: int, max_tokens: int
) -> bool:
    """Atomically count one call and ``tokens`` against ``day`` if both stay in budget."""
    if tokens > max_tokens or max_calls < 1:
        return False
    insert_stmt = insert(LLMUsage).values(day=day, calls=1, tokens=tokens)
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=[LLMUsage.day],
        set_={"calls": LLMUsage.calls + 1, "tokens": LLMUsage.tokens + tokens},
        # Postgres locks the row for the check, so racing reservations serialise
        where=(LLMUsage.calls + 1 <= max_calls) & (LLMUsage.tokens + tokens <= max_tokens),
    ).returning(LLMUsage.calls)
    granted = (await session.execute(stmt)).first() is not None
    if not granted:
        await session.execute(
            update(LLMUsage).where(LLMUsage.day == day).values(denied=LLMUsage.denied + 1)
        )
    await session.commit()
    return granted


async def settle(
    session: AsyncSession, day: date, reserved: int, input_tokens: int, output_tokens: int
) -> None:
    """Replace a reservation with the usage actually reported."""
    await session.execute(
        update(LLMUsage)
        .where(LLMUsage.day == day)
        .values(
            tokens=LLMUsage.tokens + (input_tokens + output_tokens - reserved),
            input_tokens=LLMUsage.input_tokens + input_tokens,
            output_tokens=LLMUsage.output_tokens + output_tokens,
        )
    )
    await session.commit()


class BudgetedLLM:
    """An LLM client that refuses calls once today's budget is spent."""

    def __init__(
        self,
        inner: _LLM,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_calls_per_day: int,
        max_tokens_per_day: int,
        max_output_tokens: int,
        today: Callable[[], date] = _today,
    ) -> None:
        self.inner = inner
        self.model = inner.model
        self._sessions = session_factory
        self._max_calls = max_calls_per_day
        self._max_tokens = max_tokens_per_day
        self._max_output = max_output_tokens
        self._today = today

    async def complete_json(self, system: str, user: str) -> str:
        day = self._today()
        reserved = estimate_tokens(system) + estimate_tokens(user) + self._max_output
        async with self._sessions() as session:
            if not await reserve(session, day, reserved, self._max_calls, self._max_tokens):
                LLM_CALLS.labels("budget_denied").inc()
                log.warning("llm_budget_exceeded", day=str(day), reserve=reserved)
                raise BudgetExceededError(f"LLM budget for {day} is spent")
        # A failed call keeps its reservation: the provider may have billed it
        try:
            if isinstance(self.inner, ReportsUsage):
                reply = await self.inner.complete(system, user)
            else:
                text = await self.inner.complete_json(system, user)
                reply = LLMReply(text, reserved - self._max_output, self._max_output)
        except Exception:
            LLM_CALLS.labels("error").inc()
            raise
        LLM_CALLS.labels("ok").inc()
        LLM_TOKENS.labels("input").inc(reply.input_tokens)
        LLM_TOKENS.labels("output").inc(reply.output_tokens)
        async with self._sessions() as session:
            await settle(session, day, reserved, reply.input_tokens, reply.output_tokens)
        return reply.text

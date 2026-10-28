"""LLM spend ledger: one row per UTC day, shared by every process."""

from datetime import date

from sqlalchemy import BigInteger, Date, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Reserved before each call (worst case), corrected to actual usage after
    tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    output_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    denied: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

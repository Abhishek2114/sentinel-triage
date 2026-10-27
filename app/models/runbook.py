"""Runbook chunk ORM model – the retrieval corpus for triage."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.rag.embeddings import EMBEDDING_DIM


class RunbookChunkRow(Base):
    """One embedded runbook section."""

    __tablename__ = "runbook_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_doc: Mapped[str] = mapped_column(String(256), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str] = mapped_column(String(256), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="vectors are only compared within one model"
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "embedding_model", "source_doc", "chunk_index", name="uq_runbook_chunks_model_doc_chunk"
        ),
    )

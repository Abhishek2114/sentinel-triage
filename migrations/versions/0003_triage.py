"""Triage: pgvector runbook chunks and structured incident fields.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Matches app.rag.embeddings.EMBEDDING_DIM (all-MiniLM-L6-v2)
EMBEDDING_DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "runbook_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_doc", sa.String(256), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(256), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "embedding_model",
            sa.String(128),
            nullable=False,
            comment="vectors are only compared within one model",
        ),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "embedding_model", "source_doc", "chunk_index", name="uq_runbook_chunks_model_doc_chunk"
        ),
    )
    # No ANN index: a few hundred chunks are faster to scan exactly. Add HNSW
    # (vector_cosine_ops) if the corpus grows to tens of thousands.

    op.add_column("incidents", sa.Column("confidence", sa.String(16), nullable=True))
    op.add_column("incidents", sa.Column("runbook", sa.String(256), nullable=True))
    op.add_column("incidents", sa.Column("next_steps", postgresql.ARRAY(sa.Text()), nullable=True))
    op.add_column("incidents", sa.Column("evidence", postgresql.JSONB(), nullable=True))
    op.add_column(
        "incidents",
        sa.Column(
            "triage_method",
            sa.String(16),
            server_default="fallback",
            nullable=False,
            comment="llm | fallback",
        ),
    )
    op.add_column("incidents", sa.Column("llm_model", sa.String(128), nullable=True))


def downgrade() -> None:
    for column in ("llm_model", "triage_method", "evidence", "next_steps", "runbook", "confidence"):
        op.drop_column("incidents", column)
    op.drop_table("runbook_chunks")
    # The vector extension is left installed: other objects may depend on it

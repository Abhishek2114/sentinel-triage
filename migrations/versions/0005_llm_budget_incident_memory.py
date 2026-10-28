"""LLM spend ledger, and resolution notes for incident memory.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("denied", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("incidents", sa.Column("resolution", sa.Text(), nullable=True))
    op.add_column(
        "incidents", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_incidents_status_resolved_at", "incidents", ["status", "resolved_at"])


def downgrade() -> None:
    op.drop_index("ix_incidents_status_resolved_at", table_name="incidents")
    op.drop_column("incidents", "resolved_at")
    op.drop_column("incidents", "resolution")
    op.drop_table("llm_usage")

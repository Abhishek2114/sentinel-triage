"""Initial schema: log_events, anomalies, incidents.

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "log_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("template_id", sa.String(64), nullable=True),
        sa.Column("raw_line", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_log_events_timestamp", "log_events", ["timestamp"])
    op.create_index("ix_log_events_service", "log_events", ["service"])
    op.create_index("ix_log_events_level", "log_events", ["level"])
    op.create_index("ix_log_events_service_level", "log_events", ["service", "level"])
    op.create_index("ix_log_events_timestamp_service", "log_events", ["timestamp", "service"])

    op.create_table(
        "anomalies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("detector", sa.String(64), nullable=False, comment="iforest | lstm_ae"),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service", sa.String(128), nullable=True),
        sa.Column(
            "is_true_positive", sa.Boolean(), nullable=True, comment="filled during evaluation"
        ),
    )
    op.create_index("ix_anomalies_detected_at", "anomalies", ["detected_at"])
    op.create_index("ix_anomalies_service", "anomalies", ["service"])

    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "anomaly_id", sa.Integer(), sa.ForeignKey("anomalies.id"), nullable=False, unique=True
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("related_event_ids", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("status", sa.String(32), server_default="open", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("incidents")
    op.drop_table("anomalies")
    op.drop_table("log_events")

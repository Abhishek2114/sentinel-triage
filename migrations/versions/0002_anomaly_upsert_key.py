"""One anomaly per detector, service and window start, so detection can upsert.

Also documents the new error_burst detector in the column comment.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "anomalies",
        "detector",
        comment="error_burst | iforest | lstm_ae",
        existing_comment="iforest | lstm_ae",
        existing_type=sa.String(64),
        existing_nullable=False,
    )
    op.create_unique_constraint(
        "uq_anomalies_detector_service_window",
        "anomalies",
        ["detector", "service", "window_start"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_constraint("uq_anomalies_detector_service_window", "anomalies", type_="unique")
    op.alter_column(
        "anomalies",
        "detector",
        comment="iforest | lstm_ae",
        existing_comment="error_burst | iforest | lstm_ae",
        existing_type=sa.String(64),
        existing_nullable=False,
    )

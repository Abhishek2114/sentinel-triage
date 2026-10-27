"""Group anomalies into incidents: anomalies.incident_id and incident time span.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("anomalies", sa.Column("incident_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_anomalies_incident_id",
        "anomalies",
        "incidents",
        ["incident_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_anomalies_incident_id", "anomalies", ["incident_id"])

    op.add_column("incidents", sa.Column("service", sa.String(128), nullable=True))
    op.add_column("incidents", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("incidents", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "incidents",
        sa.Column("anomaly_count", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "incidents",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # Existing incidents each have exactly one anomaly: copy its service and window
    op.execute(
        """
        UPDATE incidents AS i
        SET service = a.service, started_at = a.window_start, last_seen_at = a.window_end
        FROM anomalies AS a
        WHERE a.id = i.anomaly_id
        """
    )
    op.execute(
        """
        UPDATE anomalies AS a SET incident_id = i.id
        FROM incidents AS i
        WHERE i.anomaly_id = a.id
        """
    )
    op.alter_column("incidents", "started_at", nullable=False)
    op.alter_column("incidents", "last_seen_at", nullable=False)
    op.create_index("ix_incidents_service_status", "incidents", ["service", "status"])


def downgrade() -> None:
    op.drop_index("ix_incidents_service_status", table_name="incidents")
    for column in ("updated_at", "anomaly_count", "last_seen_at", "started_at", "service"):
        op.drop_column("incidents", column)
    op.drop_index("ix_anomalies_incident_id", table_name="anomalies")
    op.drop_constraint("fk_anomalies_incident_id", "anomalies", type_="foreignkey")
    op.drop_column("anomalies", "incident_id")

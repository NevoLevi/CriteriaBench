"""Create extraction and evaluation run tables.

Revision ID: 20260831_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("trial_id", sa.String(length=100), nullable=False),
        sa.Column("trial_title", sa.String(length=2000), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_extraction_runs_provider", "extraction_runs", ["provider"])
    op.create_index("ix_extraction_runs_status", "extraction_runs", ["status"])
    op.create_index("ix_extraction_runs_trial_id", "extraction_runs", ["trial_id"])
    op.create_index(
        "ix_extraction_runs_trial_created",
        "extraction_runs",
        ["trial_id", "created_at"],
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("extraction_run_id", sa.String(length=36), nullable=True),
        sa.Column("trial_id", sa.String(length=100), nullable=False),
        sa.Column("prediction_json", sa.JSON(), nullable=False),
        sa.Column("reference_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["extraction_runs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluation_runs_extraction_run_id",
        "evaluation_runs",
        ["extraction_run_id"],
    )
    op.create_index("ix_evaluation_runs_trial_id", "evaluation_runs", ["trial_id"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_runs_trial_id", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_extraction_run_id", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_index("ix_extraction_runs_trial_created", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_trial_id", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_status", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_provider", table_name="extraction_runs")
    op.drop_table("extraction_runs")

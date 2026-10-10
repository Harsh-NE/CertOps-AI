"""validation results, job runs, scan targets, timestamptz

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TIMESTAMP_COLUMNS = {
    "certificates": ["not_before", "not_after", "created_at", "updated_at", "last_scanned_at"],
    "renewal_tasks": ["created_at", "completed_at"],
    "scan_logs": ["created_at"],
    "users": ["created_at"],
}


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    if is_pg:
        # New enum labels must be added outside the DDL transaction.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE certstatus ADD VALUE IF NOT EXISTS 'HOSTNAME_MISMATCH'")
            op.execute("ALTER TYPE certstatus ADD VALUE IF NOT EXISTS 'WEAK_CRYPTO'")

    op.create_table(
        "job_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("status", sa.Enum("RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "SKIPPED", name="jobrunstatus")),
        sa.Column("attempts", sa.Integer()),
        sa.Column("discovered", sa.Integer()),
        sa.Column("errors", sa.Text()),
        sa.Column("triggered_by", sa.String()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_job_runs_provider", "job_runs", ["provider"])

    op.create_table(
        "scan_targets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("host", sa.String(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("server_name", sa.String()),
        sa.Column("environment", sa.String()),
        sa.Column("enabled", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
    )

    op.create_table(
        "validation_results",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("certificate_id", sa.String(), sa.ForeignKey("certificates.id"), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("overall", sa.String(), nullable=False),
        sa.Column("chain_valid", sa.Boolean()),
        sa.Column("revoked", sa.Boolean()),
        sa.Column("findings", sa.Text(), nullable=False),
    )
    op.create_index("ix_validation_results_certificate_id", "validation_results", ["certificate_id"])
    op.create_index("ix_validation_results_checked_at", "validation_results", ["checked_at"])

    with op.batch_alter_table("certificates") as batch:
        batch.add_column(sa.Column("expected_hostname", sa.String()))
        batch.add_column(sa.Column("chain_pem", sa.Text()))
        batch.add_column(sa.Column("last_validated_at", sa.DateTime(timezone=True)))

    if not is_pg:  # SQLite stores enums as VARCHAR(longest label); widen for the new labels
        with op.batch_alter_table("certificates") as batch:
            batch.alter_column(
                "status", existing_type=sa.VARCHAR(length=13),
                type_=sa.Enum("VALID", "EXPIRING_SOON", "EXPIRED", "REVOKED", "INVALID_CHAIN", "HOSTNAME_MISMATCH",
                              "WEAK_CRYPTO", "UNKNOWN", name="certstatus"), existing_nullable=True)

    if is_pg:  # existing values were naive UTC
        for table, cols in TIMESTAMP_COLUMNS.items():
            for col in cols:
                op.execute(f'ALTER TABLE {table} ALTER COLUMN {col} TYPE TIMESTAMP WITH TIME ZONE '
                           f"USING {col} AT TIME ZONE 'UTC'")


def downgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    if is_pg:
        for table, cols in TIMESTAMP_COLUMNS.items():
            for col in cols:
                op.execute(f'ALTER TABLE {table} ALTER COLUMN {col} TYPE TIMESTAMP WITHOUT TIME ZONE '
                           f"USING {col} AT TIME ZONE 'UTC'")
    with op.batch_alter_table("certificates") as batch:
        batch.drop_column("last_validated_at")
        batch.drop_column("chain_pem")
        batch.drop_column("expected_hostname")
    op.drop_table("validation_results")
    op.drop_table("scan_targets")
    op.drop_table("job_runs")
    if is_pg:
        op.execute("DROP TYPE IF EXISTS jobrunstatus")
    # PostgreSQL cannot remove labels from an enum; HOSTNAME_MISMATCH / WEAK_CRYPTO stay in certstatus.

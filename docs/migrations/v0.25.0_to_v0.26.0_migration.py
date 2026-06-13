"""Alembic migration template for upgrading agent-control-plane from v0.24.0 to v0.26.0.

This template contains the schema upgrades required for:
- v0.25.0: approval ticket revocation columns
- v0.26.0: steering history, started_at, and max_steering_retries columns

Copy/paste these operations into your own Alembic migration scripts.
"""

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    # 1. Upgrade from v0.24.0 to v0.25.0 (Approval ticket revocation)
    op.add_column('approval_tickets', sa.Column('revoked_by', sa.VARCHAR(length=100), nullable=True))
    op.add_column('approval_tickets', sa.Column('revocation_reason', sa.Text(), nullable=True))
    op.add_column('approval_tickets', sa.Column('revoked_at', sa.TIMESTAMP(timezone=True), nullable=True))

    # 2. Upgrade from v0.25.0 to v0.26.0 (Steering history and session started_at)
    op.add_column('policy_snapshots', sa.Column('max_steering_retries', sa.Integer(), server_default='3', nullable=False))
    op.add_column('agent_runs', sa.Column('steering_history', sa.JSON(), server_default='{}', nullable=False))
    op.add_column('agent_runs', sa.Column('killed_at', sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column('agent_runs', sa.Column('started_at', sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    # Downgrade from v0.26.0 to v0.25.0
    op.drop_column('agent_runs', 'started_at')
    op.drop_column('agent_runs', 'killed_at')
    op.drop_column('agent_runs', 'steering_history')
    op.drop_column('policy_snapshots', 'max_steering_retries')

    # Downgrade from v0.25.0 to v0.24.0
    op.drop_column('approval_tickets', 'revoked_at')
    op.drop_column('approval_tickets', 'revocation_reason')
    op.drop_column('approval_tickets', 'revoked_by')

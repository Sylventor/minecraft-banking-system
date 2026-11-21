"""Verified rights requests, age self-attestation and controlled retention; no data purge on upgrade."""
from pathlib import Path

from alembic import op

revision = "auth_0013"
down_revision = "auth_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    raise RuntimeError("Privacy decisions, restrictions and erasure records must not be discarded")

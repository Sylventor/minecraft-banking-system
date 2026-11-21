"""Controlled privacy minimization preserves journal amounts, reserves and asset ownership."""
from pathlib import Path

from alembic import op

revision = "finance_0011"
down_revision = "finance_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    raise RuntimeError("Do not restore erased identifiers or discard privacy decisions")

"""Episodios: variable, umbral y horas de calma asociadas.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18
"""

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE episode
            ADD COLUMN variable_code text REFERENCES variable (code),
            ADD COLUMN threshold real,      -- umbral superado (en la unidad de la variable)
            ADD COLUMN calm_hours smallint; -- horas con viento medio < 1 m/s durante el episodio
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE episode DROP COLUMN variable_code, DROP COLUMN threshold, "
        "DROP COLUMN calm_hours"
    )

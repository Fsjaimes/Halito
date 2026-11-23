"""Calor de superficie: valores por escena y celda, de los que se derivan las medianas.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-16
"""

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


UPGRADE = """
ALTER TABLE lst_scene
    ADD COLUMN clear_pct real,          -- porción del área de estudio sin nubes
    ADD COLUMN season text CHECK (season IN ('DEF', 'MAM', 'JJA', 'SON'));

-- Lo que vio cada escena en cada celda. Guardarlo permite sumar escenas nuevas sin volver
-- a descargar las anteriores y recalcular las medianas en SQL.
CREATE TABLE lst_scene_cell (
    scene_id  integer NOT NULL REFERENCES lst_scene (id) ON DELETE CASCADE,
    h3        text NOT NULL,
    lst_c     real NOT NULL,            -- temperatura de superficie media de la celda, °C
    anomaly_c real NOT NULL,            -- diferencia con la mediana de la escena
    PRIMARY KEY (scene_id, h3)
);
CREATE INDEX lst_scene_cell_h3_idx ON lst_scene_cell (h3);
"""

DOWNGRADE = """
DROP TABLE lst_scene_cell;
ALTER TABLE lst_scene DROP COLUMN clear_pct, DROP COLUMN season;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)

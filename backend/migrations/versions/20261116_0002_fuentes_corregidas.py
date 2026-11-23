"""Fuentes corregidas: alias de estaciones, presión del IDEAM y datasets locales de la CDMB.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-16
"""

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


UPGRADE = """
-- El IDEAM cambia el código de una estación sin avisar (Piedecuesta Granja pasó de
-- 0023190700 a 2319500065 en agosto de 2026). Los códigos anteriores viven aquí.
CREATE TABLE station_alias (
    source      text NOT NULL CHECK (source IN ('ideam', 'cdmb')),
    source_code text NOT NULL,
    station_id  integer NOT NULL REFERENCES station (id) ON DELETE CASCADE,
    PRIMARY KEY (source, source_code)
);

-- La ingesta avanza por dataset y por estación.
ALTER TABLE ingestion_run ADD COLUMN station_code text;
DROP INDEX ingestion_run_dataset_idx;
CREATE INDEX ingestion_run_lookup_idx
    ON ingestion_run (dataset_id, station_code, started_at DESC);

-- Unidad común de presión: hPa (el IDEAM la publica así; la CDMB, en mmHg).
UPDATE variable SET unit = 'hPa', valid_min = 600, valid_max = 1100 WHERE code = 'pressure';
-- La radiación horaria se guarda como potencia media.
UPDATE variable SET unit = 'W/m²' WHERE code = 'radiation';

INSERT INTO source_variable_map (source, source_code, variable_code) VALUES
    ('ideam', '62tk-nxj5', 'pressure'),
    -- Columnas de los datasets por estación de la CDMB (2024 en adelante, sensores a 2 m).
    ('cdmb', 'temperatura_ambiente_celsius', 'temp_2m'),
    ('cdmb', 'humedad_relativa',             'rh_2m'),
    ('cdmb', 'presi_n_atmosf_rica_mmhg',     'pressure'),
    ('cdmb', 'velocidad_del_viento_m_s',     'wind_speed'),
    ('cdmb', 'direcci_n_del_viento',         'wind_dir'),
    ('cdmb', 'precipitaci_n_mm',             'precip'),
    ('cdmb', 'radiaci_n_solar_w_m',          'radiation'),
    ('cdmb', 'pm2_5_g_m',                    'pm25'),
    ('cdmb', 'pm10_g_m',                     'pm10'),
    ('cdmb', 'o3_g_m',                       'o3'),
    ('cdmb', 'no_g_m',                       'no'),
    ('cdmb', 'no2_g_m',                      'no2'),
    ('cdmb', 'so2_g_m',                      'so2'),
    ('cdmb', 'co_g_m',                       'co');
"""

DOWNGRADE = """
DELETE FROM source_variable_map
WHERE (source = 'ideam' AND source_code = '62tk-nxj5')
   OR (source = 'cdmb' AND source_code LIKE '%\\_%');
UPDATE variable SET unit = 'Wh/m²' WHERE code = 'radiation';
UPDATE variable SET unit = 'mmHg', valid_min = 550, valid_max = 800 WHERE code = 'pressure';
DROP INDEX ingestion_run_lookup_idx;
CREATE INDEX ingestion_run_dataset_idx ON ingestion_run (dataset_id, started_at DESC);
ALTER TABLE ingestion_run DROP COLUMN station_code;
DROP TABLE station_alias;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)

"""Esquema inicial: catálogo, observaciones, satélite, episodios y operación.

Revision ID: 0001
Revises:
Create Date: 2026-11-16
"""

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ── Catálogo ────────────────────────────────────────────────────────────────

CREATE TABLE station (
    id           integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source       text NOT NULL CHECK (source IN ('ideam', 'cdmb')),
    source_code  text NOT NULL,
    name         text NOT NULL,
    municipality text NOT NULL,
    geom         geography(Point, 4326) NOT NULL,
    elevation_m  integer,
    active_from  date,
    active_to    date,
    UNIQUE (source, source_code)
);
CREATE INDEX station_geom_idx ON station USING gist (geom);

CREATE TABLE variable (
    code      text PRIMARY KEY,
    name      text NOT NULL,
    unit      text NOT NULL,
    kind      text NOT NULL CHECK (kind IN ('meteo', 'pollutant')),
    valid_min double precision NOT NULL,
    valid_max double precision NOT NULL,
    CHECK (valid_min < valid_max)
);

-- Cómo llama cada fuente a cada variable. En la CDMB es el código de la fila
-- (msfl_code); en el IDEAM cada variable es un dataset distinto de datos.gov.co.
CREATE TABLE source_variable_map (
    source        text NOT NULL CHECK (source IN ('ideam', 'cdmb')),
    source_code   text NOT NULL,
    variable_code text NOT NULL REFERENCES variable (code),
    PRIMARY KEY (source, source_code)
);

-- ── Operación ───────────────────────────────────────────────────────────────

CREATE TABLE ingestion_run (
    id            integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source        text NOT NULL,
    dataset_id    text NOT NULL,
    watermark     timestamptz,
    rows_in       integer NOT NULL DEFAULT 0,
    rows_ok       integer NOT NULL DEFAULT 0,
    rows_repaired integer NOT NULL DEFAULT 0,
    rows_rejected integer NOT NULL DEFAULT 0,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'ok', 'failed'))
);
CREATE INDEX ingestion_run_dataset_idx ON ingestion_run (dataset_id, started_at DESC);

-- ── Hechos ──────────────────────────────────────────────────────────────────

CREATE TABLE observation (
    station_id       integer NOT NULL REFERENCES station (id),
    variable_code    text NOT NULL REFERENCES variable (code),
    ts               timestamptz NOT NULL,
    value            double precision,           -- NULL si quality_flag = 'rejected'
    raw_value        text,                       -- tal como llegó de la fuente
    quality_flag     text NOT NULL DEFAULT 'ok'
                     CHECK (quality_flag IN
                            ('ok', 'repaired_unique', 'repaired_inferred', 'rejected')),
    n_samples        smallint NOT NULL DEFAULT 1, -- lecturas agregadas en la hora
    ingestion_run_id integer REFERENCES ingestion_run (id),
    PRIMARY KEY (station_id, variable_code, ts),
    CHECK ((quality_flag = 'rejected') = (value IS NULL))
);
SELECT create_hypertable('observation', by_range('ts', INTERVAL '1 month'));
CREATE INDEX observation_variable_ts_idx ON observation (variable_code, ts DESC);

-- ── Satélite ────────────────────────────────────────────────────────────────

CREATE TABLE lst_scene (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    landsat_id  text NOT NULL UNIQUE,
    acquired_at timestamptz NOT NULL,
    cloud_pct   real NOT NULL,
    used        boolean NOT NULL DEFAULT false
);

CREATE TABLE lst_cell (
    h3           text NOT NULL,       -- índice H3, resolución 9
    period       text NOT NULL,       -- '2024-DEF', '2024-JJA', 'all'
    lst_c_median real NOT NULL,       -- temperatura de superficie, °C
    anomaly_c    real NOT NULL,       -- diferencia con la mediana urbana de la escena
    n_scenes     smallint NOT NULL,
    PRIMARY KEY (h3, period)
);
CREATE INDEX lst_cell_period_idx ON lst_cell (period);

-- ── Eventos ─────────────────────────────────────────────────────────────────

CREATE TABLE episode (
    id              integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind            text NOT NULL
                    CHECK (kind IN ('heat', 'pm25', 'o3', 'calm', 'heavy_rain')),
    starts_at       timestamptz NOT NULL,
    ends_at         timestamptz NOT NULL,
    peak_value      real,
    peak_station_id integer REFERENCES station (id),
    peak_at         timestamptz,
    stations        integer[] NOT NULL DEFAULT '{}',
    severity        text,
    summary         text,
    CHECK (starts_at <= ends_at)
);
CREATE INDEX episode_range_idx ON episode (starts_at, ends_at);
"""

SEED = """
INSERT INTO variable (code, name, unit, kind, valid_min, valid_max) VALUES
    ('temp_2m',    'Temperatura del aire a 2 m',  '°C',    'meteo',     5,    45),
    ('temp_10m',   'Temperatura del aire a 10 m', '°C',    'meteo',     5,    45),
    ('rh_2m',      'Humedad relativa a 2 m',      '%',     'meteo',     0,   100),
    ('rh_10m',     'Humedad relativa a 10 m',     '%',     'meteo',     0,   100),
    ('pressure',   'Presión atmosférica',         'mmHg',  'meteo',   550,   800),
    ('wind_speed', 'Velocidad del viento',        'm/s',   'meteo',     0,    40),
    ('wind_dir',   'Dirección del viento',        '°',     'meteo',     0,   360),
    ('precip',     'Precipitación',               'mm',    'meteo',     0,   150),
    ('radiation',  'Radiación solar global',      'Wh/m²', 'meteo',     0,  1400),
    ('uvb',        'Radiación UVB',               'W/m²',  'meteo',     0,    10),
    ('pm25',       'Material particulado PM2.5',  'µg/m³', 'pollutant', 0,   500),
    ('pm10',       'Material particulado PM10',   'µg/m³', 'pollutant', 0,  1000),
    ('o3',         'Ozono',                       'µg/m³', 'pollutant', 0,   600),
    ('no',         'Monóxido de nitrógeno',       'µg/m³', 'pollutant', 0,  1000),
    ('no2',        'Dióxido de nitrógeno',        'µg/m³', 'pollutant', 0,  1000),
    ('so2',        'Dióxido de azufre',           'µg/m³', 'pollutant', 0,  1000),
    ('co',         'Monóxido de carbono',         'µg/m³', 'pollutant', 0, 50000);

INSERT INTO source_variable_map (source, source_code, variable_code) VALUES
    ('cdmb', 'TAire2',    'temp_2m'),
    ('cdmb', 'TAire10',   'temp_10m'),
    ('cdmb', 'HAire2',    'rh_2m'),
    ('cdmb', 'HAire10',   'rh_10m'),
    ('cdmb', 'P',         'pressure'),
    ('cdmb', 'VViento',   'wind_speed'),
    ('cdmb', 'DViento',   'wind_dir'),
    ('cdmb', 'PLiquida',  'precip'),
    ('cdmb', 'RGlobal',   'radiation'),
    ('cdmb', 'RUVb',      'uvb'),
    ('cdmb', 'PM2.5',     'pm25'),
    ('cdmb', 'PM10',      'pm10'),
    ('cdmb', 'O3',        'o3'),
    ('cdmb', 'NO',        'no'),
    ('cdmb', 'NO2',       'no2'),
    ('cdmb', 'SO2',       'so2'),
    ('cdmb', 'CO',        'co'),
    ('ideam', 'sbwg-7ju4', 'temp_2m'),
    ('ideam', 'uext-mhny', 'rh_2m'),
    ('ideam', 'sgfv-3yp8', 'wind_speed'),
    ('ideam', 'kiw7-v9ta', 'wind_dir'),
    ('ideam', 's54a-sgyg', 'precip');
"""

# Agregado continuo: resumen diario en hora de Colombia, sin los valores rechazados.
# Los percentiles no se pueden materializar aquí; se calculan al detectar episodios.
DAILY_AGGREGATE = """
CREATE MATERIALIZED VIEW observation_daily
WITH (timescaledb.continuous) AS
SELECT
    station_id,
    variable_code,
    time_bucket(INTERVAL '1 day', ts, 'America/Bogota') AS day,
    avg(value)   AS mean,
    min(value)   AS min,
    max(value)   AS max,
    count(value) AS n
FROM observation
WHERE quality_flag <> 'rejected'
GROUP BY station_id, variable_code, day
WITH NO DATA;
"""

DAILY_POLICY = """
SELECT add_continuous_aggregate_policy(
    'observation_daily',
    start_offset      => NULL,
    end_offset        => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day'
);
"""


def upgrade() -> None:
    op.execute(SCHEMA)
    op.execute(SEED)
    # Un agregado continuo no se puede crear dentro de una transacción.
    with op.get_context().autocommit_block():
        op.execute(DAILY_AGGREGATE)
        op.execute(DAILY_POLICY)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS observation_daily")
    op.execute(
        "DROP TABLE IF EXISTS episode, lst_cell, lst_scene, observation, "
        "ingestion_run, source_variable_map, variable, station"
    )

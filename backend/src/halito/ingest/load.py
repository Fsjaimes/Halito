"""Escritura en la base de datos: estaciones, observaciones y registro de cada corrida."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import polars as pl
from sqlalchemy import Connection, text

from halito.ingest.catalog import STATIONS
from halito.quality.clean import Ranges

StationIds = dict[tuple[str, str], int]


def sync_stations(conn: Connection) -> StationIds:
    """Crea o actualiza las estaciones del catálogo y devuelve sus ids por (fuente, código)."""
    ids: StationIds = {}
    for station in STATIONS:
        station_id = conn.execute(
            text(
                "INSERT INTO station (source, source_code, name, municipality, geom, elevation_m) "
                "VALUES (:source, :code, :name, :municipality, "
                "        ST_MakePoint(:lon, :lat)::geography, :elevation) "
                "ON CONFLICT (source, source_code) DO UPDATE SET "
                "    name = EXCLUDED.name, municipality = EXCLUDED.municipality, "
                "    geom = EXCLUDED.geom, elevation_m = EXCLUDED.elevation_m "
                "RETURNING id"
            ),
            {
                "source": station.source,
                "code": station.code,
                "name": station.name,
                "municipality": station.municipality,
                "lon": station.lon,
                "lat": station.lat,
                "elevation": station.elevation_m,
            },
        ).scalar_one()
        ids[(station.source, station.code)] = station_id
        for alias in station.aliases:
            conn.execute(
                text(
                    "INSERT INTO station_alias (source, source_code, station_id) "
                    "VALUES (:source, :code, :id) "
                    "ON CONFLICT (source, source_code) "
                    "DO UPDATE SET station_id = EXCLUDED.station_id"
                ),
                {"source": station.source, "code": alias, "id": station_id},
            )
    return ids


def load_ranges(conn: Connection) -> Ranges:
    rows = conn.execute(text("SELECT code, valid_min, valid_max FROM variable"))
    return {code: (low, high) for code, low, high in rows}


def last_watermark(conn: Connection, dataset: str, station_code: str) -> datetime | None:
    """Última hora cargada con éxito para ese dataset y estación."""
    watermark: datetime | None = conn.execute(
        text(
            "SELECT max(watermark) FROM ingestion_run "
            "WHERE dataset_id = :dataset AND station_code = :station AND status = 'ok'"
        ),
        {"dataset": dataset, "station": station_code},
    ).scalar()
    return watermark


def start_run(conn: Connection, source: str, dataset: str, station_code: str) -> int:
    run_id: int = conn.execute(
        text(
            "INSERT INTO ingestion_run (source, dataset_id, station_code) "
            "VALUES (:source, :dataset, :station) RETURNING id"
        ),
        {"source": source, "dataset": dataset, "station": station_code},
    ).scalar_one()
    return run_id


@dataclass(frozen=True)
class RunCounts:
    rows_in: int
    rows_ok: int
    rows_rejected: int
    watermark: datetime | None


def finish_run(conn: Connection, run_id: int, counts: RunCounts) -> None:
    conn.execute(
        text(
            "UPDATE ingestion_run SET status = 'ok', finished_at = now(), rows_in = :rows_in, "
            "    rows_ok = :rows_ok, rows_rejected = :rows_rejected, "
            "    watermark = :watermark WHERE id = :id"
        ),
        {
            "id": run_id,
            "rows_in": counts.rows_in,
            "rows_ok": counts.rows_ok,
            "rows_rejected": counts.rows_rejected,
            "watermark": counts.watermark,
        },
    )


def fail_run(conn: Connection, run_id: int) -> None:
    conn.execute(
        text("UPDATE ingestion_run SET status = 'failed', finished_at = now() WHERE id = :id"),
        {"id": run_id},
    )


def upsert_observations(
    conn: Connection, hourly: pl.DataFrame, station_id: int, run_id: int
) -> None:
    """Carga idempotente: volver a ingerir las mismas horas reemplaza, no duplica."""
    if hourly.is_empty():
        return
    conn.execute(
        text(
            "CREATE TEMP TABLE IF NOT EXISTS observation_stage ("
            "    variable_code text, ts timestamptz, value double precision, raw_value text, "
            "    quality_flag text, n_samples smallint) ON COMMIT DROP"
        )
    )
    conn.execute(text("TRUNCATE observation_stage"))
    driver: Any = conn.connection.driver_connection
    with (
        driver.cursor() as cursor,
        cursor.copy(
            "COPY observation_stage "
            "(variable_code, ts, value, raw_value, quality_flag, n_samples) FROM STDIN"
        ) as copy,
    ):
        for row in hourly.select(
            "variable", "ts", "value", "raw_value", "quality_flag", "n_samples"
        ).iter_rows():
            copy.write_row(row)
    conn.execute(
        text(
            "INSERT INTO observation (station_id, variable_code, ts, value, raw_value, "
            "                         quality_flag, n_samples, ingestion_run_id) "
            "SELECT :station, variable_code, ts, value, raw_value, quality_flag, n_samples, :run "
            "FROM observation_stage "
            "ON CONFLICT (station_id, variable_code, ts) DO UPDATE SET "
            "    value = EXCLUDED.value, raw_value = EXCLUDED.raw_value, "
            "    quality_flag = EXCLUDED.quality_flag, n_samples = EXCLUDED.n_samples, "
            "    ingestion_run_id = EXCLUDED.ingestion_run_id"
        ),
        {"station": station_id, "run": run_id},
    )


def known_scenes(conn: Connection) -> set[str]:
    return set(conn.execute(text("SELECT landsat_id FROM lst_scene")).scalars())


def save_scene(
    conn: Connection,
    *,
    landsat_id: str,
    acquired_at: datetime,
    cloud_pct: float,
    season: str,
    clear_pct: float,
    used: bool,
    cells: list[tuple[str, float, float]],
) -> None:
    """Registra una escena y, si sirve, lo que midió en cada celda (h3, °C, anomalía)."""
    scene_id = conn.execute(
        text(
            "INSERT INTO lst_scene (landsat_id, acquired_at, cloud_pct, season, clear_pct, used) "
            "VALUES (:landsat_id, :acquired_at, :cloud_pct, :season, :clear_pct, :used) "
            "RETURNING id"
        ),
        {
            "landsat_id": landsat_id,
            "acquired_at": acquired_at,
            "cloud_pct": cloud_pct,
            "season": season,
            "clear_pct": clear_pct,
            "used": used,
        },
    ).scalar_one()
    if not used or not cells:
        return
    driver: Any = conn.connection.driver_connection
    with (
        driver.cursor() as cursor,
        cursor.copy("COPY lst_scene_cell (scene_id, h3, lst_c, anomaly_c) FROM STDIN") as copy,
    ):
        for h3_id, lst_c, anomaly_c in cells:
            copy.write_row((scene_id, h3_id, lst_c, anomaly_c))


# Medianas por celda: una para todo el periodo y una por trimestre climático.
REBUILD_HEAT = """
INSERT INTO lst_cell (h3, period, lst_c_median, anomaly_c, n_scenes)
SELECT c.h3, p.period,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY c.lst_c),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY c.anomaly_c),
       count(*)
FROM lst_scene_cell c
JOIN lst_scene s ON s.id = c.scene_id AND s.used
CROSS JOIN LATERAL (VALUES ('all'), (s.season)) AS p (period)
GROUP BY c.h3, p.period
HAVING count(*) >= :min_scenes
"""


def rebuild_heat_cells(conn: Connection, min_scenes: int = 3) -> int:
    """Recalcula las medianas por celda. Con menos de ``min_scenes`` la celda no se publica."""
    conn.execute(text("DELETE FROM lst_cell"))
    result = conn.execute(text(REBUILD_HEAT), {"min_scenes": min_scenes})
    return result.rowcount

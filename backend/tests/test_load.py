from datetime import datetime
from zoneinfo import ZoneInfo

import polars as pl
import pytest
from sqlalchemy import Connection, text

from halito.ingest import load
from halito.ingest.catalog import CDMB_COLUMNS, DEFAULT_SINCE, IDEAM_DATASETS, STATIONS
from halito.ingest.pipeline import resume_point
from halito.quality.clean import RAW_SCHEMA, to_hourly

BOGOTA = ZoneInfo("America/Bogota")


def hourly_temperatures(*values: float) -> pl.DataFrame:
    readings = pl.DataFrame(
        [
            ("X", "temp_2m", datetime(2025, 3, 1, hour), value, str(value))
            for hour, value in enumerate(values)
        ],
        schema=RAW_SCHEMA,
        orient="row",
    )
    return to_hourly(readings, {"temp_2m": (5.0, 45.0)})


def stored(conn: Connection, station_id: int) -> list[tuple[float | None, str]]:
    rows = conn.execute(
        text("SELECT value, quality_flag FROM observation WHERE station_id = :id ORDER BY ts"),
        {"id": station_id},
    )
    return [(value, flag) for value, flag in rows]


def test_el_catalogo_crea_estaciones_y_registra_los_codigos_antiguos(conn: Connection) -> None:
    ids = load.sync_stations(conn)

    assert len(ids) == len(STATIONS)
    # Piedecuesta Granja cambió de código en agosto de 2026: el antiguo apunta a la misma.
    alias_target = conn.execute(
        text("SELECT station_id FROM station_alias WHERE source_code = '0023190700'")
    ).scalar_one()
    assert alias_target == ids[("ideam", "2319500065")]
    # Sincronizar dos veces no duplica.
    assert load.sync_stations(conn) == ids


def test_volver_a_cargar_las_mismas_horas_reemplaza_sin_duplicar(conn: Connection) -> None:
    station_id = load.sync_stations(conn)[("cdmb", "31916")]
    conn.execute(text("DELETE FROM observation WHERE station_id = :id"), {"id": station_id})
    run_id = load.start_run(conn, "cdmb", "prueba", "31916")

    load.upsert_observations(conn, hourly_temperatures(20.0, 21.0, -999.0), station_id, run_id)
    load.upsert_observations(conn, hourly_temperatures(20.5, 21.0, 23.0), station_id, run_id)

    assert stored(conn, station_id) == [(20.5, "ok"), (21.0, "ok"), (23.0, "ok")]


def test_una_hora_rechazada_se_guarda_sin_valor(conn: Connection) -> None:
    station_id = load.sync_stations(conn)[("cdmb", "31916")]
    conn.execute(text("DELETE FROM observation WHERE station_id = :id"), {"id": station_id})
    run_id = load.start_run(conn, "cdmb", "prueba", "31916")

    load.upsert_observations(conn, hourly_temperatures(20.0, -999.0), station_id, run_id)

    assert stored(conn, station_id) == [(20.0, "ok"), (None, "rejected")]


def test_la_ultima_corrida_exitosa_marca_donde_reanudar(conn: Connection) -> None:
    assert load.last_watermark(conn, "prueba", "31916") is None
    watermark = datetime(2025, 12, 31, 23, tzinfo=BOGOTA)

    ok_run = load.start_run(conn, "cdmb", "prueba", "31916")
    load.finish_run(conn, ok_run, load.RunCounts(10, 9, 1, watermark))
    failed_run = load.start_run(conn, "cdmb", "prueba", "31916")
    load.fail_run(conn, failed_run)

    assert load.last_watermark(conn, "prueba", "31916") == watermark


def test_reanudar_retrocede_dos_dias_para_recoger_lecturas_tardias() -> None:
    watermark = datetime(2026, 11, 14, 23, 30, tzinfo=BOGOTA)

    assert resume_point(watermark, None) == datetime(2026, 11, 12, 23)
    assert resume_point(None, None) == DEFAULT_SINCE
    assert resume_point(watermark, datetime(2025, 1, 1)) == datetime(2025, 1, 1)


def test_los_rangos_validos_salen_del_catalogo_de_variables(conn: Connection) -> None:
    ranges = load.load_ranges(conn)

    assert ranges["rh_2m"] == (0, 100)
    assert ranges["pressure"] == pytest.approx((600, 1100))


def test_el_catalogo_del_codigo_coincide_con_el_de_la_base(conn: Connection) -> None:
    rows = conn.execute(text("SELECT source, source_code, variable_code FROM source_variable_map"))
    in_db = {(source, code): variable for source, code, variable in rows}

    for dataset, variable in IDEAM_DATASETS.items():
        assert in_db[("ideam", dataset)] == variable
    for column, (variable, _) in CDMB_COLUMNS.items():
        assert in_db[("cdmb", column)] == variable

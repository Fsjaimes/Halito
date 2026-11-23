"""Comprueban que la migración deja el esquema que describe el diseño técnico."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

TABLES = {
    "station",
    "variable",
    "source_variable_map",
    "ingestion_run",
    "observation",
    "lst_scene",
    "lst_cell",
    "episode",
}


async def _insert_station(db: AsyncConnection) -> int:
    station_id = await db.scalar(
        text(
            "INSERT INTO station (source, source_code, name, municipality, geom, elevation_m) "
            "VALUES ('cdmb', 'test', 'Estación de prueba', 'Bucaramanga', "
            "        ST_MakePoint(-73.1198, 7.1193)::geography, 959) RETURNING id"
        )
    )
    assert isinstance(station_id, int)
    return station_id


async def test_existen_las_tablas_del_diseno(db: AsyncConnection) -> None:
    rows = await db.scalars(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    )
    assert set(rows.all()) >= TABLES


async def test_observation_es_hypertable_y_tiene_agregado_diario(db: AsyncConnection) -> None:
    hypertables = await db.scalars(
        text("SELECT hypertable_name FROM timescaledb_information.hypertables")
    )
    aggregates = await db.scalars(
        text("SELECT view_name FROM timescaledb_information.continuous_aggregates")
    )
    assert "observation" in hypertables.all()
    assert "observation_daily" in aggregates.all()


async def test_cada_codigo_de_las_fuentes_apunta_a_una_variable(db: AsyncConnection) -> None:
    mapping = await db.execute(
        text("SELECT source, source_code, variable_code FROM source_variable_map")
    )
    rows = {(source, code): variable for source, code, variable in mapping.all()}

    assert rows[("cdmb", "PM2.5")] == "pm25"
    # La CDMB cambió la altura del sensor en 2024: son variables distintas.
    assert rows[("cdmb", "TAire10")] == "temp_10m"
    assert rows[("cdmb", "TAire2")] == "temp_2m"
    assert rows[("ideam", "sbwg-7ju4")] == "temp_2m"


async def test_se_guarda_una_observacion_reparada_con_su_valor_crudo(db: AsyncConnection) -> None:
    station_id = await _insert_station(db)
    await db.execute(
        text(
            "INSERT INTO observation (station_id, variable_code, ts, value, raw_value, "
            "                         quality_flag) "
            "VALUES (:station, 'temp_10m', :ts, 19.32, '1932', 'repaired_unique')"
        ),
        {"station": station_id, "ts": datetime(2023, 3, 1, 7, tzinfo=UTC)},
    )
    value = await db.scalar(
        text("SELECT value FROM observation WHERE station_id = :station"), {"station": station_id}
    )
    assert value == pytest.approx(19.32)


async def test_un_valor_rechazado_no_puede_conservar_valor(db: AsyncConnection) -> None:
    station_id = await _insert_station(db)
    with pytest.raises(IntegrityError):
        await db.execute(
            text(
                "INSERT INTO observation (station_id, variable_code, ts, value, quality_flag) "
                "VALUES (:station, 'pm25', now(), 2413, 'rejected')"
            ),
            {"station": station_id},
        )


async def test_la_distancia_entre_estaciones_se_calcula_en_metros(db: AsyncConnection) -> None:
    # UIS → Piedecuesta Granja: unos 18 km en línea recta.
    meters = await db.scalar(
        text(
            "SELECT ST_Distance(ST_MakePoint(-73.1222, 7.1447)::geography, "
            "                   ST_MakePoint(-73.0678, 6.9933)::geography)"
        )
    )
    assert 17_000 < meters < 19_000

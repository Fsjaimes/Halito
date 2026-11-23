from collections.abc import AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from halito.api.main import app
from halito.db.session import get_session

BOGOTA = ZoneInfo("America/Bogota")


@pytest.fixture
async def api(db: AsyncConnection) -> AsyncIterator[AsyncClient]:
    """Cliente cuya API lee dentro de la transacción del test (que se deshace al final)."""

    async def session_in_test_transaction() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(bind=db) as session:
            yield session

    app.dependency_overrides[get_session] = session_in_test_transaction
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def add_station(db: AsyncConnection, code: str) -> int:
    station_id = await db.scalar(
        text(
            "INSERT INTO station (source, source_code, name, municipality, geom, elevation_m) "
            "VALUES ('cdmb', :code, :name, 'Bucaramanga', "
            "        ST_MakePoint(-73.12, 7.13)::geography, 950) RETURNING id"
        ),
        {"code": code, "name": f"Estación {code}"},
    )
    assert isinstance(station_id, int)
    return station_id


async def add_reading(
    db: AsyncConnection, station_id: int, hour: int, value: float | None, flag: str = "ok"
) -> None:
    await db.execute(
        text(
            "INSERT INTO observation (station_id, variable_code, ts, value, quality_flag) "
            "VALUES (:station, 'prueba', :ts, :value, :flag)"
        ),
        {
            "station": station_id,
            "ts": datetime(2025, 3, 1, hour, tzinfo=BOGOTA),
            "value": value,
            "flag": flag,
        },
    )


@pytest.fixture
async def two_stations(db: AsyncConnection) -> tuple[int, int]:
    """Una variable de prueba medida por dos estaciones el 1 de marzo de 2025."""
    await db.execute(
        text(
            "INSERT INTO variable (code, name, unit, kind, valid_min, valid_max) "
            "VALUES ('prueba', 'Variable de prueba', 'u', 'meteo', 0, 100)"
        )
    )
    first, second = await add_station(db, "t1"), await add_station(db, "t2")
    await add_reading(db, first, 0, 20.0)
    await add_reading(db, first, 1, 21.456)
    await add_reading(db, first, 2, None, "rejected")
    await add_reading(db, second, 1, 30.0)
    return first, second


async def test_health_informa_migracion_y_extensiones(api: AsyncClient) -> None:
    response = await api.get("/health")

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    # La base de pruebas debe estar en la última migración del repositorio.
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert body["schema_revision"] == head
    assert set(body["extensions"]) == {"postgis", "timescaledb"}


async def test_frames_devuelve_una_matriz_hora_por_estacion(
    api: AsyncClient, two_stations: tuple[int, int]
) -> None:
    response = await api.get(
        "/frames", params={"variable": "prueba", "start": "2025-03-01", "days": 1}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["unit"] == "u"
    assert body["stations"] == list(two_stations)
    assert len(body["timestamps"]) == 24
    assert body["timestamps"][0] == "2025-03-01T00:00:00-05:00"
    # Redondea, alinea cada estación en su columna y deja vacío lo rechazado o ausente.
    assert body["values"][:3] == [[20.0, None], [21.46, 30.0], [None, None]]


async def test_frames_de_un_rango_sin_datos_conserva_las_estaciones(
    api: AsyncClient, two_stations: tuple[int, int]
) -> None:
    response = await api.get(
        "/frames", params={"variable": "prueba", "start": "2024-01-01", "days": 2}
    )

    body = response.json()
    assert body["stations"] == list(two_stations)
    assert len(body["values"]) == 48
    assert all(value is None for row in body["values"] for value in row)


async def test_frames_rechaza_variables_desconocidas_y_rangos_excesivos(api: AsyncClient) -> None:
    unknown = await api.get("/frames", params={"variable": "no_existe", "start": "2025-03-01"})
    too_long = await api.get(
        "/frames", params={"variable": "temp_2m", "start": "2025-03-01", "days": 90}
    )

    assert unknown.status_code == 404
    assert "no_existe" in unknown.json()["detail"]
    assert too_long.status_code == 422


async def test_stations_lista_ubicacion_y_variables_medidas(
    api: AsyncClient, two_stations: tuple[int, int]
) -> None:
    response = await api.get("/stations")

    by_id = {station["id"]: station for station in response.json()}
    station = by_id[two_stations[0]]
    assert (station["lat"], station["lon"]) == (pytest.approx(7.13), pytest.approx(-73.12))
    assert station["elevation_m"] == 950
    assert station["variables"] == ["prueba"]


async def test_variables_informa_el_rango_de_dias_con_datos(
    api: AsyncClient, two_stations: tuple[int, int]
) -> None:
    response = await api.get("/variables")

    by_code = {variable["code"]: variable for variable in response.json()}
    assert by_code["prueba"]["first_day"] == by_code["prueba"]["last_day"] == "2025-03-01"


async def test_heat_devuelve_celdas_en_columnas_y_el_resumen_de_escenas(
    api: AsyncClient, db: AsyncConnection
) -> None:
    await db.execute(text("DELETE FROM lst_scene"))
    await db.execute(text("DELETE FROM lst_cell"))
    await db.execute(
        text(
            "INSERT INTO lst_scene (landsat_id, acquired_at, cloud_pct, season, used) VALUES "
            "('a', '2025-01-10 15:00+00', 5, 'DEF', true), "
            "('b', '2025-07-10 15:00+00', 9, 'JJA', true), "
            "('c', '2025-08-10 15:00+00', 70, 'JJA', false)"
        )
    )
    await db.execute(
        text(
            "INSERT INTO lst_cell (h3, period, lst_c_median, anomaly_c, n_scenes) VALUES "
            "('89a', 'all', 33.456, 2.5, 2), ('89b', 'all', 28.0, -3.0, 2), "
            "('89a', 'DEF', 31.0, 1.0, 1)"
        )
    )

    summary = (await api.get("/heat")).json()
    cells = (await api.get("/heat/cells", params={"period": "all"})).json()
    unknown = await api.get("/heat/cells", params={"period": "verano"})

    assert (summary["scenes_used"], summary["scenes_discarded"]) == (2, 1)
    assert (summary["first_scene"], summary["last_scene"]) == ("2025-01-10", "2025-07-10")
    assert summary["periods"] == [
        {"period": "all", "scenes": 2, "cells": 2},
        {"period": "DEF", "scenes": 1, "cells": 1},
    ]
    assert cells["cells"] == ["89a", "89b"]
    assert cells["lst_c"] == [33.46, 28.0]
    assert cells["anomaly_c"] == [2.5, -3.0]
    assert unknown.status_code == 422


async def test_episodes_filtra_por_tipo_y_por_fechas(api: AsyncClient, db: AsyncConnection) -> None:
    await db.execute(text("DELETE FROM episode"))
    station = await add_station(db, "ep")
    await db.execute(
        text(
            "INSERT INTO episode (kind, variable_code, starts_at, ends_at, peak_value, "
            "    peak_station_id, peak_at, stations, threshold, severity, summary, calm_hours) "
            "VALUES "
            "('pm25', 'pm25', '2025-02-10 05:00+00', '2025-02-11 11:00+00', 58.2, :s, "
            "    '2025-02-10 13:00+00', ARRAY[:s], 37, 'Dañina', 'Resumen PM', 9), "
            "('heavy_rain', 'precip', '2025-10-03 20:00+00', '2025-10-03 21:00+00', 44.1, :s, "
            "    '2025-10-03 20:00+00', ARRAY[:s], 20, 'Muy fuerte', 'Resumen lluvia', 0)"
        ),
        {"s": station},
    )

    every = (await api.get("/episodes")).json()
    pm25 = (await api.get("/episodes", params={"kind": "pm25"})).json()
    # El episodio de PM2.5 termina el 11 de febrero a las 06:00 hora de Colombia.
    from_11 = (await api.get("/episodes", params={"start": "2025-02-11"})).json()
    until_9 = (await api.get("/episodes", params={"end": "2025-02-09"})).json()
    wrong = await api.get("/episodes", params={"kind": "granizo"})

    assert [e["kind"] for e in every] == ["heavy_rain", "pm25"]
    assert pm25[0]["summary"] == "Resumen PM"
    assert pm25[0]["stations"] == [station]
    assert pm25[0]["calm_hours"] == 9
    assert [e["kind"] for e in from_11] == ["heavy_rain", "pm25"]
    assert until_9 == []
    assert wrong.status_code == 422

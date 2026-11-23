from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, text

from halito.analysis import episodes as ep

HOUR = timedelta(hours=1)
T0 = datetime(2025, 2, 10, 0, tzinfo=UTC)


def reading(station: int, hour: int, value: float, threshold: float = 37.0) -> ep.Reading:
    return ep.Reading(station, T0 + hour * HOUR, value, threshold)


def test_lecturas_seguidas_forman_un_tramo_con_su_pico() -> None:
    readings = [reading(1, 0, 40), reading(1, 1, 52), reading(1, 2, 45)]

    [run] = ep.runs_from_readings(readings, step=HOUR, max_gap=timedelta(), min_steps=1)

    assert (run.start, run.end) == (T0, T0 + 3 * HOUR)
    assert (run.peak, run.peak_at, run.steps) == (52, T0 + HOUR, 3)


def test_un_hueco_corto_no_corta_el_tramo_y_uno_largo_si() -> None:
    readings = [reading(1, 0, 40), reading(1, 3, 41), reading(1, 9, 42)]

    runs = ep.runs_from_readings(readings, step=HOUR, max_gap=2 * HOUR, min_steps=1)

    assert [(r.start, r.end) for r in runs] == [
        (T0, T0 + 4 * HOUR),
        (T0 + 9 * HOUR, T0 + 10 * HOUR),
    ]


def test_los_tramos_demasiado_cortos_se_descartan() -> None:
    day = timedelta(days=1)
    readings = [ep.Reading(1, T0 + n * day, 30.0, 29.0) for n in (0, 1, 5, 6, 7)]

    runs = ep.runs_from_readings(readings, step=day, max_gap=timedelta(), min_steps=3)

    assert [(r.start, r.steps) for r in runs] == [(T0 + 5 * day, 3)]


def test_cada_estacion_tiene_sus_propios_tramos() -> None:
    readings = [reading(2, 0, 40), reading(1, 1, 44), reading(2, 1, 39)]

    runs = ep.runs_from_readings(readings, step=HOUR, max_gap=timedelta(), min_steps=1)

    assert [(r.station_id, r.steps) for r in runs] == [(1, 1), (2, 2)]


def test_los_tramos_que_se_solapan_forman_un_episodio_con_el_pico_mayor() -> None:
    runs = ep.runs_from_readings(
        [reading(1, 0, 40), reading(1, 1, 41), reading(2, 1, 60), reading(2, 2, 50),
         reading(3, 20, 45)],
        step=HOUR,
        max_gap=timedelta(),
        min_steps=1,
    )  # fmt: skip

    first, second = ep.merge_runs("pm25", "pm25", runs, max_gap=6 * HOUR)

    assert (first.start, first.end) == (T0, T0 + 3 * HOUR)
    assert (first.peak, first.peak_station_id, first.stations) == (60, 2, {1, 2})
    assert second.stations == {3}


def episode(kind: str, peak: float, **changes: object) -> ep.Episode:
    values: dict[str, object] = {
        "kind": kind,
        "variable": "pm25",
        "start": T0,
        "end": T0 + 30 * HOUR,
        "peak": peak,
        "peak_at": T0 + 5 * HOUR,
        "peak_station_id": 7,
        "threshold": 37.0,
        "stations": {7, 8},
        "calm_hours": 9,
    }
    values.update(changes)
    return ep.Episode(**values)  # type: ignore[arg-type]


def test_el_resumen_cuenta_el_episodio_con_sus_numeros() -> None:
    text_pm25 = ep.summary(episode("pm25", 58.24), {7: "Club Unión"})
    text_heat = ep.summary(
        episode("heat", 33.06, end=T0 + timedelta(days=4), stations={7}), {7: "Floridablanca"}
    )

    text_o3 = ep.summary(episode("o3", 100.5, end=T0 + HOUR, stations={7}, calm_hours=0), {})
    assert text_o3 == (
        "El ozono superó la norma de 8 horas (100 µg/m³) durante 1 hora, "
        "con un máximo de 100,5 µg/m³ en una estación."
    )
    assert text_pm25 == (
        "El PM2.5 superó la norma de 24 horas (37 µg/m³) durante 30 horas. La media de 24 horas "
        "llegó a 58,2 µg/m³ en Club Unión. Lo registraron 2 estaciones. Coincidió con 9 horas "
        "de calma (viento de menos de 1 m/s)."
    )
    # El pico fue a las 05:00 UTC del 10 de febrero: las 00:00 del 10 en Colombia.
    assert text_heat == (
        "4 días seguidos con máximas por encima de lo habitual para el mes. "
        "Pico de 33,1 °C en Floridablanca el 10 de febrero de 2025."
    )


@pytest.mark.parametrize(
    ("kind", "peak", "expected"),
    [
        ("pm25", 40.0, "Dañina para grupos sensibles"),
        ("pm25", 60.0, "Dañina"),
        ("heavy_rain", 25.0, "Fuerte"),
        ("heavy_rain", 45.0, "Muy fuerte"),
    ],
)
def test_la_severidad_sigue_las_categorias_publicadas(
    kind: str, peak: float, expected: str
) -> None:
    assert ep.severity(episode(kind, peak)) == expected


def add_station(conn: Connection, code: str) -> int:
    station_id = conn.execute(
        text(
            "INSERT INTO station (source, source_code, name, municipality, geom) "
            "VALUES ('cdmb', :code, :code, 'Bucaramanga', ST_MakePoint(-73.1, 7.1)::geography) "
            "RETURNING id"
        ),
        {"code": code},
    ).scalar_one()
    assert isinstance(station_id, int)
    return station_id


def add_hours(conn: Connection, station: int, variable: str, values: list[float]) -> None:
    for hour, value in enumerate(values):
        conn.execute(
            text(
                "INSERT INTO observation (station_id, variable_code, ts, value) "
                "VALUES (:station, :variable, :ts, :value)"
            ),
            {"station": station, "variable": variable, "ts": T0 + hour * HOUR, "value": value},
        )


def test_la_media_de_24_horas_se_calcula_en_la_base_con_horas_suficientes(conn: Connection) -> None:
    station = add_station(conn, "prueba-pm")
    # 24 horas a 30 y luego 24 horas a 50.
    add_hours(conn, station, "pm25", [30.0] * 24 + [50.0] * 24)

    readings = ep.rolling_readings(
        conn, "pm25", hours=24, min_hours=18, limit=37.0, station_ids=[station]
    )

    first = min(readings, key=lambda r: r.at)
    # Hora 31: (16·30 + 8·50) / 24 = 36,7. Hora 32: (15·30 + 9·50) / 24 = 37,5, primera sobre 37.
    assert first.at == T0 + 32 * HOUR
    assert first.value == pytest.approx(37.5)
    assert max(r.value for r in readings) == pytest.approx(50.0)


def test_las_horas_de_calma_promedian_el_viento_de_todas_las_estaciones(conn: Connection) -> None:
    first, second = add_station(conn, "prueba-v1"), add_station(conn, "prueba-v2")
    add_hours(conn, first, "wind_speed", [0.4, 0.6, 2.0])
    add_hours(conn, second, "wind_speed", [0.8, 1.6, 2.0])
    conn.execute(text("DELETE FROM observation WHERE variable_code = 'wind_speed' AND ts >= :t0 "
                      "AND ts < :t1 AND station_id NOT IN (:a, :b)"),
                 {"t0": T0, "t1": T0 + 3 * HOUR, "a": first, "b": second})  # fmt: skip

    # Hora 0: media 0,6 (calma). Hora 1: media 1,1. Hora 2: media 2,0.
    assert ep.calm_hours(conn, T0, T0 + 3 * HOUR) == 1

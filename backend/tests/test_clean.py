from datetime import datetime
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from halito.quality.clean import RAW_SCHEMA, to_hourly

RANGES = {"temp_2m": (5.0, 45.0), "precip": (0.0, 150.0), "wind_dir": (0.0, 360.0)}


def readings(variable: str, *samples: tuple[str, float | None]) -> pl.DataFrame:
    """Lecturas de una estación: (hora «HH:MM» del 1 de marzo de 2025, valor)."""
    return pl.DataFrame(
        [
            ("E1", variable, datetime.fromisoformat(f"2025-03-01T{time}"), value, str(value))
            for time, value in samples
        ],
        schema=RAW_SCHEMA,
        orient="row",
    )


def only_row(frame: pl.DataFrame) -> dict[str, object]:
    assert frame.height == 1
    return frame.row(0, named=True)


def test_promedia_la_hora_sin_las_lecturas_fuera_de_rango() -> None:
    hourly = to_hourly(
        readings("temp_2m", ("07:00", 20.0), ("07:20", -999.0), ("07:40", 22.0)), RANGES
    )

    row = only_row(hourly)
    assert row["value"] == pytest.approx(21.0)
    assert (row["quality_flag"], row["n_samples"], row["raw_value"]) == ("ok", 2, None)


def test_la_hora_queda_en_hora_de_colombia() -> None:
    hourly = to_hourly(readings("temp_2m", ("07:40", 22.0)), RANGES)

    expected = datetime(2025, 3, 1, 7, tzinfo=ZoneInfo("America/Bogota"))
    assert only_row(hourly)["ts"] == expected


def test_la_lluvia_se_suma() -> None:
    hourly = to_hourly(readings("precip", ("15:00", 1.5), ("15:10", 0.0), ("15:20", 2.5)), RANGES)

    assert only_row(hourly)["value"] == pytest.approx(4.0)


def test_la_lluvia_con_acumulados_que_se_solapan_se_rechaza() -> None:
    # Un sensor que reporta cada 5 minutos el acumulado de los últimos 10: no se puede sumar.
    samples = [(f"16:{minute:02d}", 4.0) for minute in range(0, 60, 5)]

    row = only_row(to_hourly(readings("precip", *samples), RANGES))

    assert (row["value"], row["quality_flag"], row["n_samples"]) == (None, "rejected", 12)


def test_la_direccion_del_viento_se_promedia_como_angulo() -> None:
    hourly = to_hourly(readings("wind_dir", ("09:00", 350.0), ("09:10", 10.0)), RANGES)

    value = only_row(hourly)["value"]
    assert isinstance(value, float)
    # Norte, no sur: la media aritmética daría 180°.
    assert min(value, 360 - value) == pytest.approx(0.0, abs=1e-6)


def test_una_hora_sin_lecturas_validas_se_rechaza_y_conserva_el_crudo() -> None:
    hourly = to_hourly(readings("temp_2m", ("03:00", -999.0), ("03:30", None)), RANGES)

    row = only_row(hourly)
    assert (row["value"], row["quality_flag"], row["raw_value"]) == (None, "rejected", "-999.0")
    assert row["n_samples"] == 2


def test_cada_hora_y_cada_estacion_van_por_separado() -> None:
    frame = pl.concat(
        [
            readings("temp_2m", ("07:10", 20.0), ("08:10", 24.0)),
            readings("temp_2m", ("07:30", 30.0)).with_columns(station_code=pl.lit("E2")),
        ]
    )

    hourly = to_hourly(frame, RANGES)

    assert hourly.select("station_code", "value").rows() == [
        ("E1", 20.0),
        ("E1", 24.0),
        ("E2", 30.0),
    ]


def test_sin_lecturas_no_hay_horas() -> None:
    assert to_hourly(pl.DataFrame(schema=RAW_SCHEMA), RANGES).is_empty()

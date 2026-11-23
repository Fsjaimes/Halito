"""Limpieza y agregación horaria de lecturas crudas.

Entrada (una fila por lectura):
    station_code · variable · ts (hora local, sin zona) · value · raw_value

Salida (una fila por estación, variable y hora):
    station_code · variable · ts (con zona) · value · raw_value · quality_flag · n_samples
"""

import math

import polars as pl

LOCAL_TZ = "America/Bogota"

RAW_SCHEMA: dict[str, pl.DataType] = {
    "station_code": pl.String(),
    "variable": pl.String(),
    "ts": pl.Datetime("us"),
    "value": pl.Float64(),
    "raw_value": pl.String(),
}

Ranges = dict[str, tuple[float, float]]

MAX_RAIN_READINGS = 6


def to_hourly(readings: pl.DataFrame, ranges: Ranges) -> pl.DataFrame:
    """Descarta lecturas fuera del rango físico y resume cada hora.

    - La lluvia se suma; la dirección del viento se promedia como ángulo (350° y 10° dan 0°,
      no 180°); el resto se promedia.
    - Una hora sin ninguna lectura válida queda como ``rejected``, sin valor y con la
      primera lectura cruda, para que el reporte de calidad pueda contarla.
    """
    limits = pl.DataFrame(
        {
            "variable": list(ranges),
            "valid_min": [low for low, _ in ranges.values()],
            "valid_max": [high for _, high in ranges.values()],
        },
        schema={"variable": pl.String, "valid_min": pl.Float64, "valid_max": pl.Float64},
    )
    valid = pl.col("value").is_between(pl.col("valid_min"), pl.col("valid_max")).fill_null(False)
    angle = pl.col("value").radians()

    hourly = (
        readings.join(limits, on="variable", how="inner")
        .with_columns(valid=valid, hour=pl.col("ts").dt.truncate("1h"))
        .group_by("station_code", "variable", "hour")
        .agg(
            n_valid=pl.col("valid").sum(),
            n_total=pl.len(),
            mean=pl.col("value").filter(pl.col("valid")).mean(),
            total=pl.col("value").filter(pl.col("valid")).sum(),
            sin=angle.sin().filter(pl.col("valid")).mean(),
            cos=angle.cos().filter(pl.col("valid")).mean(),
            first_raw=pl.col("raw_value").sort_by("ts").first(),
        )
    )

    # La lluvia es un acumulado por intervalo y solo se puede sumar si los intervalos no se
    # solapan. Más de seis lecturas en una hora (cada 10 minutos) indica acumulados que se
    # pisan, como los del sensor GPRS de Palonegro: sumarlos multiplica la lluvia. Se rechazan.
    overlapping_rain = (pl.col("variable") == "precip") & (pl.col("n_valid") > MAX_RAIN_READINGS)
    has_valid = (pl.col("n_valid") > 0) & ~overlapping_rain
    circular = (pl.arctan2("sin", "cos") * (180 / math.pi)).mod(360)
    value = (
        pl.when((pl.col("n_valid") == 0) | overlapping_rain)
        .then(None)
        .when(pl.col("variable") == "precip")
        .then(pl.col("total"))
        .when(pl.col("variable") == "wind_dir")
        .then(circular)
        .otherwise(pl.col("mean"))
    )

    return hourly.select(
        "station_code",
        "variable",
        ts=pl.col("hour").dt.replace_time_zone(LOCAL_TZ),
        value=value,
        raw_value=pl.when(has_valid).then(None).otherwise(pl.col("first_raw")),
        quality_flag=pl.when(has_valid).then(pl.lit("ok")).otherwise(pl.lit("rejected")),
        n_samples=pl.when(has_valid)
        .then(pl.col("n_valid"))
        .otherwise(pl.col("n_total"))
        .clip(upper_bound=32767)
        .cast(pl.Int16),
    ).sort("station_code", "variable", "ts")

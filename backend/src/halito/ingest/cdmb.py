"""Lecturas de la CDMB: un dataset por estación, con una columna por variable."""

from datetime import datetime

import polars as pl

from halito.ingest.catalog import CDMB_COLUMNS, Station
from halito.ingest.socrata import Row, SocrataClient
from halito.quality.clean import RAW_SCHEMA

# «NA» significa que la estación no mide esa variable: no genera fila.
# «-999» es una lectura inválida: se conserva y la limpieza la rechaza por rango.
NOT_MEASURED = {"NA", ""}


def fetch_readings(client: SocrataClient, station: Station, since: datetime) -> pl.DataFrame:
    assert station.dataset_id is not None, f"{station.name} no tiene dataset propio"
    rows = client.rows(
        station.dataset_id,
        select="fecha_y_hora, " + ", ".join(CDMB_COLUMNS),
        where=f"fecha_y_hora >= '{since:%Y-%m-%dT%H:%M:%S}'",
        order="fecha_y_hora",
    )
    return parse_rows(rows, station.code)


def parse_rows(rows: list[Row], station_code: str) -> pl.DataFrame:
    """Pasa de formato ancho a largo y convierte cada columna a la unidad de Hálito."""
    records = [
        (row["fecha_y_hora"], variable, raw, factor)
        for row in rows
        if row.get("fecha_y_hora")
        for column, (variable, factor) in CDMB_COLUMNS.items()
        if (raw := row.get(column)) is not None and raw.strip() not in NOT_MEASURED
    ]
    if not records:
        return pl.DataFrame(schema=RAW_SCHEMA)
    long = pl.DataFrame(
        records,
        schema={
            "ts": pl.String,
            "variable": pl.String,
            "raw_value": pl.String,
            "factor": pl.Float64,
        },
        orient="row",
    )
    return long.select(
        station_code=pl.lit(station_code),
        variable=pl.col("variable"),
        ts=pl.col("ts").str.to_datetime("%Y-%m-%dT%H:%M:%S%.f", time_unit="us"),
        value=pl.col("raw_value").cast(pl.Float64, strict=False) * pl.col("factor"),
        raw_value=pl.col("raw_value"),
    )

"""Lecturas del IDEAM: un dataset nacional por variable, en formato largo."""

from datetime import datetime, timedelta

import polars as pl

from halito.ingest.catalog import Station
from halito.ingest.socrata import Row, SocrataClient
from halito.quality.clean import RAW_SCHEMA

# El IDEAM publicó en UTC hasta julio de 2026 y, sin anunciarlo, pasó a hora de Colombia al
# reanudar el 12 de agosto de 2026 (se ve en la hora del máximo diario de temperatura, que
# salta de las 17–18 h a las 12–13 h). Entre ambas fechas no hay datos.
LOCAL_TIME_SINCE = datetime(2026, 8, 1)
UTC_OFFSET = timedelta(hours=5)

# Algunas estaciones reportan la misma variable con dos sensores a la vez: Palonegro tiene el
# convencional (p. ej. 0240, lluvia cada 10 min) y uno GPRS (0257, cada minuto). Sumar ambos
# duplica la lluvia. En cada hora se usa un solo sensor: el de código menor, que en el IDEAM es
# el convencional. Si una estación solo tiene el GPRS, se usa ese.


def fetch_readings(
    client: SocrataClient, dataset: str, variable: str, station: Station, since: datetime
) -> pl.DataFrame:
    """Descarga las lecturas crudas de una estación desde ``since``, con todos sus códigos."""
    # ``since`` es hora local; lo publicado en UTC va cinco horas por delante.
    start = since if since >= LOCAL_TIME_SINCE else since + UTC_OFFSET
    rows: list[Row] = []
    # Una consulta por código (la igualdad usa el índice del portal; ``in (...)`` no) y por
    # tramo de fechas: el portal responde mucho antes a varios tramos que a dos años de golpe.
    for code in station.codes:
        for first, last in windows(start, datetime.now() + WINDOW):
            rows += client.rows(
                dataset,
                select="fechaobservacion, valorobservado, codigosensor",
                where=(
                    f"codigoestacion = '{code}' "
                    f"AND fechaobservacion >= '{first:%Y-%m-%dT%H:%M:%S}' "
                    f"AND fechaobservacion < '{last:%Y-%m-%dT%H:%M:%S}'"
                ),
                order="fechaobservacion",
            )
    return parse_rows(rows, station.code, variable)


WINDOW = timedelta(days=30)


def windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Parte ``[start, end)`` en tramos consecutivos de ``WINDOW`` como máximo."""
    spans = []
    while start < end:
        spans.append((start, min(start + WINDOW, end)))
        start += WINDOW
    return spans


def parse_rows(rows: list[Row], station_code: str, variable: str) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema=RAW_SCHEMA)
    raw = pl.DataFrame(
        {
            "ts": [row.get("fechaobservacion") for row in rows],
            "raw_value": [row.get("valorobservado") for row in rows],
            "sensor": [row.get("codigosensor") or "" for row in rows],
        },
        schema={"ts": pl.String, "raw_value": pl.String, "sensor": pl.String},
    )
    published = pl.col("ts").str.to_datetime("%Y-%m-%dT%H:%M:%S%.f", time_unit="us")
    hour = published.dt.truncate("1h")
    raw = raw.filter(pl.col("sensor") == pl.col("sensor").min().over(hour))
    # En algunos periodos el portal repite cada lectura (doce de 10 minutos en una hora): se
    # deja una por instante y sensor, o la lluvia se sumaría dos veces.
    raw = raw.unique(subset=["ts", "sensor"], keep="first", maintain_order=True)
    return raw.select(
        station_code=pl.lit(station_code),
        variable=pl.lit(variable),
        ts=pl.when(published < LOCAL_TIME_SINCE).then(published - UTC_OFFSET).otherwise(published),
        value=pl.col("raw_value").cast(pl.Float64, strict=False),
        raw_value=pl.col("raw_value"),
    ).drop_nulls("ts")

"""Comandos de Hálito: ``uv run halito --help``."""

from collections import Counter
from datetime import datetime
from typing import Annotated

import typer
from sqlalchemy import text

from halito.analysis import episodes
from halito.config import get_settings
from halito.db.session import get_sync_engine
from halito.ingest import load, pipeline
from halito.ingest.socrata import SocrataClient

app = typer.Typer(help="Hálito: ingesta y mantenimiento de datos.", no_args_is_help=True)

LANDSAT_SINCE = datetime(2023, 1, 1)

Since = Annotated[
    datetime | None,
    typer.Option(
        help="Hora local desde la que pedir datos. Por defecto, donde quedó la última corrida."
    ),
]


def _client() -> SocrataClient:
    return SocrataClient(app_token=get_settings().socrata_app_token)


@app.command()
def sync_stations() -> None:
    """Crea o actualiza las estaciones del catálogo."""
    with get_sync_engine().begin() as conn:
        ids = load.sync_stations(conn)
    typer.echo(f"{len(ids)} estaciones sincronizadas.")


@app.command()
def ingest_ideam(
    since: Since = None,
    dataset: Annotated[
        list[str] | None, typer.Option(help="Limitar a estos datasets (se puede repetir).")
    ] = None,
    workers: Annotated[int, typer.Option(help="Consultas en paralelo al portal.")] = 4,
) -> None:
    """Descarga, limpia y carga las lecturas del IDEAM."""
    client = _client()
    try:
        pipeline.ingest_ideam(
            get_sync_engine(),
            client,
            since=since,
            datasets=dataset,
            workers=workers,
            report=typer.echo,
        )
    finally:
        client.close()


@app.command()
def ingest_cdmb(since: Since = None) -> None:
    """Descarga, limpia y carga las lecturas de la CDMB."""
    client = _client()
    try:
        pipeline.ingest_cdmb(get_sync_engine(), client, since=since, report=typer.echo)
    finally:
        client.close()


@app.command()
def ingest_landsat(
    since: Annotated[datetime, typer.Option(help="Primera fecha de escenas.")] = datetime(
        2023, 1, 1
    ),
    max_cloud: Annotated[float, typer.Option(help="Nubosidad máxima de la escena, en %.")] = 80.0,
    workers: Annotated[int, typer.Option(help="Descargas en paralelo.")] = 6,
) -> None:
    """Calcula el calor de superficie por celda a partir de escenas de Landsat 8 y 9."""
    pipeline.ingest_landsat(
        get_sync_engine(),
        since=since,
        until=datetime.now(),
        max_cloud=max_cloud,
        workers=workers,
        report=typer.echo,
    )


@app.command()
def detect_episodes() -> None:
    """Recalcula los episodios de calor, PM2.5, ozono y lluvia fuerte."""
    with get_sync_engine().begin() as conn:
        found = episodes.detect(conn)
        episodes.save(conn, found)
    counts = Counter(episode.kind for episode in found)
    typer.echo(f"{len(found)} episodios: " + ", ".join(f"{k} {n}" for k, n in counts.items()))


QUALITY_SQL = """
SELECT s.source, s.name, o.variable_code,
       count(*) FILTER (WHERE o.quality_flag = 'ok')       AS ok,
       count(*) FILTER (WHERE o.quality_flag = 'rejected') AS rejected,
       min(o.ts) AT TIME ZONE 'America/Bogota' AS first_ts,
       max(o.ts) AT TIME ZONE 'America/Bogota' AS last_ts,
       round(100.0 * count(*) FILTER (WHERE o.quality_flag = 'ok')
             / (extract(epoch FROM max(o.ts) - min(o.ts)) / 3600 + 1), 1) AS coverage_pct
FROM observation o JOIN station s ON s.id = o.station_id
GROUP BY s.source, s.name, o.variable_code
ORDER BY s.source, s.name, o.variable_code
"""


@app.command()
def quality_report() -> None:
    """Resume, por estación y variable, cuántas horas hay y qué tan completa es la serie."""
    with get_sync_engine().connect() as conn:
        rows = conn.execute(text(QUALITY_SQL)).all()
    typer.echo(
        f"{'fuente':<6} {'estación':<36} {'variable':<11} {'ok':>7} {'rech.':>6} "
        f"{'desde':<10} {'hasta':<16} {'cobertura':>9}"
    )
    for source, name, variable, ok, rejected, first_ts, last_ts, coverage in rows:
        typer.echo(
            f"{source:<6} {name:<36} {variable:<11} {ok:>7} {rejected:>6} "
            f"{first_ts:%Y-%m-%d} {last_ts:%Y-%m-%d %H:%M} {coverage:>8}%"
        )


def main() -> None:
    app()

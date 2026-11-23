"""Une los pasos: descargar → limpiar → cargar, con una corrida registrada por estación."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl
from sqlalchemy import Engine

from halito.ingest import cdmb, ideam, landsat, load
from halito.ingest.catalog import DEFAULT_SINCE, IDEAM_DATASETS, Station, stations
from halito.ingest.socrata import SocrataClient
from halito.quality.clean import LOCAL_TZ, to_hourly

# Al reanudar se vuelven a pedir los últimos días: las fuentes publican lecturas tardías.
OVERLAP = timedelta(days=2)

Report = Callable[[str], None]


def resume_point(watermark: datetime | None, since: datetime | None) -> datetime:
    """Desde qué hora local pedir datos: lo que se indique, o donde quedó la última corrida."""
    if since is not None:
        return since
    if watermark is None:
        return DEFAULT_SINCE
    local = watermark.astimezone(ZoneInfo(LOCAL_TZ)).replace(tzinfo=None)
    return max(DEFAULT_SINCE, (local - OVERLAP).replace(minute=0, second=0, microsecond=0))


def ingest_station(
    engine: Engine,
    station: Station,
    dataset: str,
    fetch: Callable[[datetime], pl.DataFrame],
    since: datetime | None,
) -> load.RunCounts:
    with engine.begin() as conn:
        station_id = load.sync_stations(conn)[(station.source, station.code)]
        ranges = load.load_ranges(conn)
        start = resume_point(load.last_watermark(conn, dataset, station.code), since)
        run_id = load.start_run(conn, station.source, dataset, station.code)
    try:
        readings = fetch(start)
        hourly = to_hourly(readings, ranges)
        rejected = hourly.filter(pl.col("quality_flag") == "rejected").height
        counts = load.RunCounts(
            rows_in=readings.height,
            rows_ok=hourly.height - rejected,
            rows_rejected=rejected,
            watermark=hourly["ts"].max() if not hourly.is_empty() else None,  # type: ignore[arg-type]
        )
        with engine.begin() as conn:
            load.upsert_observations(conn, hourly, station_id, run_id)
            load.finish_run(conn, run_id, counts)
    except BaseException:
        with engine.begin() as conn:
            load.fail_run(conn, run_id)
        raise
    return counts


def ingest_ideam(
    engine: Engine,
    client: SocrataClient,
    *,
    since: datetime | None = None,
    datasets: list[str] | None = None,
    workers: int = 4,
    report: Report = print,
) -> None:
    jobs = [
        (dataset, variable, station)
        for dataset, variable in IDEAM_DATASETS.items()
        if not datasets or dataset in datasets
        for station in stations("ideam")
    ]

    def run(job: tuple[str, str, Station]) -> str:
        dataset, variable, station = job
        fetch = partial(_fetch_ideam, client, dataset, variable, station)
        try:
            return _line(variable, station, ingest_station(engine, station, dataset, fetch, since))
        except Exception as error:
            # La corrida queda como fallida y la siguiente la reintenta; las demás siguen.
            return f"{variable:<11} {station.name:<36} falló: {type(error).__name__} {error}"

    # La espera es casi toda de red: unas pocas consultas en paralelo acortan mucho la corrida
    # sin cargar el portal.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for line in pool.map(run, jobs):
            report(line)


def ingest_cdmb(
    engine: Engine,
    client: SocrataClient,
    *,
    since: datetime | None = None,
    report: Report = print,
) -> None:
    for station in stations("cdmb"):
        assert station.dataset_id is not None
        fetch = partial(cdmb.fetch_readings, client, station)
        counts = ingest_station(engine, station, station.dataset_id, fetch, since)
        report(_line("todas", station, counts))


def _fetch_ideam(
    client: SocrataClient, dataset: str, variable: str, station: Station, since: datetime
) -> pl.DataFrame:
    return ideam.fetch_readings(client, dataset, variable, station, since)


def _line(variable: str, station: Station, counts: load.RunCounts) -> str:
    until = f"{counts.watermark:%Y-%m-%d %H:%M}" if counts.watermark else "sin datos"
    return (
        f"{variable:<11} {station.name:<36} {counts.rows_in:>8} lecturas → "
        f"{counts.rows_ok:>6} horas ok, {counts.rows_rejected:>5} rechazadas · hasta {until}"
    )


def ingest_landsat(
    engine: Engine,
    *,
    since: datetime,
    until: datetime,
    max_cloud: float = 80.0,
    workers: int = 6,
    report: Report = print,
) -> None:
    """Procesa las escenas que falten y recalcula el calor de superficie por celda."""
    with engine.begin() as conn:
        done = load.known_scenes(conn)
    pending = [
        item for item in landsat.search_scenes(since, until, max_cloud) if item.id not in done
    ]
    report(f"{len(pending)} escenas por procesar ({len(done)} ya estaban).")

    def run(item: Any) -> tuple[Any, landsat.SceneCells | None]:
        try:
            return item, landsat.read_scene(item)
        except Exception as error:  # una escena ilegible no debe tumbar las demás
            report(f"  {item.id}: no se pudo leer ({error})")
            return item, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for item, scene in pool.map(run, pending):
            if scene is None:
                continue
            with engine.begin() as conn:
                load.save_scene(
                    conn,
                    landsat_id=item.id,
                    acquired_at=item.datetime,
                    cloud_pct=item.properties["eo:cloud_cover"],
                    season=landsat.season_of(item.datetime),
                    clear_pct=round(100 * scene.clear_fraction, 1),
                    used=scene.usable,
                    cells=[
                        (landsat.cell_id(cell), float(lst), float(anomaly))
                        for cell, lst, anomaly in zip(
                            scene.cells, scene.lst_c, scene.anomaly_c, strict=True
                        )
                    ]
                    if scene.usable
                    else [],
                )
            verdict = "usada" if scene.usable else "descartada por nubes"
            report(
                f"  {item.datetime:%Y-%m-%d} {item.id[:4]} "
                f"despejado {100 * scene.clear_fraction:5.1f}% · {verdict}"
            )

    with engine.begin() as conn:
        published = load.rebuild_heat_cells(conn)
    report(f"{published} celdas publicadas.")

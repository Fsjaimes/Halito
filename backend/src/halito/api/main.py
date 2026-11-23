"""API de solo lectura de Hálito."""

from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from halito import __version__
from halito.api.schemas import (
    Episode,
    Frames,
    Health,
    HeatCells,
    HeatPeriod,
    HeatSummary,
    Station,
    Variable,
)
from halito.db.session import get_session
from halito.quality.clean import LOCAL_TZ

app = FastAPI(
    title="Hálito",
    description="El calor y el aire del área metropolitana de Bucaramanga.",
    version=__version__,
)

Session = Annotated[AsyncSession, Depends(get_session)]
BOGOTA = ZoneInfo(LOCAL_TZ)


@app.get("/health")
async def health(session: Session) -> Health:
    """Comprueba la conexión a la base de datos y qué migración está aplicada."""
    extensions = await session.execute(
        text(
            "SELECT extname, extversion FROM pg_extension "
            "WHERE extname IN ('postgis', 'timescaledb') ORDER BY extname"
        )
    )
    has_alembic = await session.scalar(text("SELECT to_regclass('alembic_version') IS NOT NULL"))
    revision = (
        await session.scalar(text("SELECT version_num FROM alembic_version"))
        if has_alembic
        else None
    )
    return Health(
        status="ok",
        version=__version__,
        schema_revision=revision,
        extensions={name: version for name, version in extensions.all()},
    )


# «Tiene datos» se pregunta con EXISTS por estación y variable: son pocas combinaciones y cada
# una se resuelve con la clave primaria, sin recorrer el millón y medio de observaciones.
MEASURED = (
    "EXISTS (SELECT 1 FROM observation o WHERE o.station_id = s.id "
    "        AND o.variable_code = v.code AND o.quality_flag <> 'rejected')"
)


@app.get("/variables")
async def variables(session: Session) -> list[Variable]:
    """Variables que tienen datos, con el primer y el último día disponibles."""
    rows = await session.execute(
        text(
            "SELECT v.code, v.name, v.unit, v.kind, "
            "       (r.first_ts AT TIME ZONE 'America/Bogota')::date AS first_day, "
            "       (r.last_ts AT TIME ZONE 'America/Bogota')::date AS last_day "
            "FROM variable v CROSS JOIN LATERAL ( "
            "    SELECT min(o.ts) AS first_ts, max(o.ts) AS last_ts FROM observation o "
            "    WHERE o.variable_code = v.code AND o.quality_flag <> 'rejected') r "
            "WHERE r.first_ts IS NOT NULL ORDER BY v.kind, v.code"
        )
    )
    return [Variable.model_validate(row, from_attributes=True) for row in rows]


@app.get("/stations")
async def stations(session: Session) -> list[Station]:
    """Estaciones con su ubicación y las variables que miden."""
    rows = await session.execute(
        text(
            "SELECT s.id, s.source, s.name, s.municipality, s.elevation_m, "
            "       ST_Y(s.geom::geometry) AS lat, ST_X(s.geom::geometry) AS lon, "
            "       ARRAY(SELECT v.code FROM variable v WHERE " + MEASURED + " ORDER BY v.code) "
            "           AS variables "
            "FROM station s ORDER BY s.name"
        )
    )
    return [Station.model_validate(row, from_attributes=True) for row in rows]


@app.get("/frames")
async def frames(
    session: Session,
    variable: Annotated[str, Query(description="Código de la variable, p. ej. temp_2m")],
    start: Annotated[date, Query(description="Primer día, en hora de Colombia")],
    days: Annotated[int, Query(ge=1, le=31, description="Cuántos días devolver")] = 7,
) -> Frames:
    """Valores horarios de todas las estaciones que miden una variable, en un rango de días."""
    unit = await session.scalar(
        text("SELECT unit FROM variable WHERE code = :code"), {"code": variable}
    )
    if unit is None:
        raise HTTPException(status_code=404, detail=f"No existe la variable «{variable}».")

    first = datetime.combine(start, time(), tzinfo=BOGOTA)
    timestamps = [first + timedelta(hours=hour) for hour in range(days * 24)]
    station_ids = list(
        await session.scalars(
            text(
                "SELECT s.id FROM station s JOIN variable v ON v.code = :code "
                "WHERE " + MEASURED + " ORDER BY s.id"
            ),
            {"code": variable},
        )
    )
    rows = await session.execute(
        text(
            "SELECT ts, station_id, value FROM observation "
            "WHERE variable_code = :code AND ts >= :first AND ts < :last "
            "  AND quality_flag <> 'rejected'"
        ),
        {"code": variable, "first": first, "last": first + timedelta(days=days)},
    )

    column = {station_id: index for index, station_id in enumerate(station_ids)}
    values: list[list[float | None]] = [[None] * len(station_ids) for _ in timestamps]
    for ts, station_id, value in rows:
        hour = int((ts - first).total_seconds() // 3600)
        if station_id in column:
            values[hour][column[station_id]] = round(value, 2)

    return Frames(
        variable=variable, unit=unit, stations=station_ids, timestamps=timestamps, values=values
    )


@app.get("/heat")
async def heat(session: Session) -> HeatSummary:
    """Resumen del calor de superficie calculado con Landsat."""
    scenes = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE used), count(*) FILTER (WHERE NOT used), "
                "       (min(local_time) FILTER (WHERE used))::date, "
                "       (max(local_time) FILTER (WHERE used))::date "
                "FROM (SELECT used, acquired_at AT TIME ZONE 'America/Bogota' AS local_time "
                "      FROM lst_scene) scenes"
            )
        )
    ).one()
    periods = await session.execute(
        text(
            "SELECT c.period, count(*) AS cells, "
            "       (SELECT count(*) FROM lst_scene s "
            "        WHERE s.used AND (c.period = 'all' OR s.season = c.period)) AS scenes "
            "FROM lst_cell c GROUP BY c.period ORDER BY (c.period <> 'all'), c.period"
        )
    )
    return HeatSummary(
        scenes_used=scenes[0],
        scenes_discarded=scenes[1],
        first_scene=scenes[2],
        last_scene=scenes[3],
        periods=[HeatPeriod.model_validate(row, from_attributes=True) for row in periods],
    )


@app.get("/heat/cells")
async def heat_cells(
    session: Session,
    period: Annotated[
        str, Query(pattern="^(all|DEF|MAM|JJA|SON)$", description="Todo el periodo o un trimestre")
    ] = "all",
) -> HeatCells:
    """Temperatura de superficie y anomalía por celda H3."""
    rows = (
        await session.execute(
            text(
                "SELECT h3, lst_c_median, anomaly_c, n_scenes FROM lst_cell "
                "WHERE period = :period ORDER BY h3"
            ),
            {"period": period},
        )
    ).all()
    return HeatCells(
        period=period,
        cells=[row[0] for row in rows],
        lst_c=[round(row[1], 2) for row in rows],
        anomaly_c=[round(row[2], 2) for row in rows],
        n_scenes=[row[3] for row in rows],
    )


EpisodeKind = Literal["heat", "pm25", "o3", "heavy_rain"]


@app.get("/episodes")
async def episodes(
    session: Session,
    kind: Annotated[EpisodeKind | None, Query(description="Filtrar por tipo")] = None,
    start: Annotated[date | None, Query(description="Desde este día (hora de Colombia)")] = None,
    end: Annotated[date | None, Query(description="Hasta este día, inclusive")] = None,
) -> list[Episode]:
    """Episodios detectados, del más reciente al más antiguo."""

    def midnight(day: date) -> datetime:
        return datetime.combine(day, time(), tzinfo=BOGOTA)

    rows = await session.execute(
        text(
            "SELECT id, kind, variable_code AS variable, starts_at, ends_at, peak_value, peak_at, "
            "       peak_station_id, stations, threshold, severity, summary, calm_hours "
            "FROM episode "
            "WHERE (CAST(:kind AS text) IS NULL OR kind = :kind) "
            "  AND (CAST(:first AS timestamptz) IS NULL OR ends_at > :first) "
            "  AND (CAST(:last AS timestamptz) IS NULL OR starts_at < :last) "
            "ORDER BY starts_at DESC"
        ),
        {
            "kind": kind,
            "first": datetime.combine(start, time(), tzinfo=BOGOTA) if start else None,
            "last": datetime.combine(end + timedelta(days=1), time(), tzinfo=BOGOTA)
            if end
            else None,
        },
    )
    return [Episode.model_validate(row, from_attributes=True) for row in rows]

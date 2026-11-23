"""Detección de episodios: tramos en que una o varias estaciones superan un umbral.

Cada regla produce *tramos por estación* (horas o días seguidos por encima del umbral). Los
tramos de distintas estaciones que se solapan se agrupan en un mismo *episodio*, con el pico
más alto como referencia.

| Episodio | Regla |
|---|---|
| Calor | Máxima diaria por encima del percentil 90 de la estación para ese mes, 3 días seguidos |
| PM2.5 | Media móvil de 24 h por encima de 37 µg/m³ (norma de 24 h, Res. 2254 de 2017) |
| Ozono | Media móvil de 8 h por encima de 100 µg/m³ (norma de 8 h, Res. 2254 de 2017) |
| Lluvia fuerte | Más de 20 mm en una hora |
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text

from halito.quality.clean import LOCAL_TZ

BOGOTA = ZoneInfo(LOCAL_TZ)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)

PM25_LIMIT_24H = 37.0
O3_LIMIT_8H = 100.0
HEAVY_RAIN_MM_H = 20.0
HEAT_PERCENTILE = 0.9
HEAT_MIN_DAYS = 3
CALM_WIND_MS = 1.0


@dataclass(frozen=True)
class Reading:
    """Una hora o un día por encima del umbral en una estación."""

    station_id: int
    at: datetime
    value: float
    threshold: float


@dataclass(frozen=True)
class Run:
    """Tramo seguido por encima del umbral en una estación. ``end`` es exclusivo."""

    station_id: int
    start: datetime
    end: datetime
    peak: float
    peak_at: datetime
    threshold: float
    steps: int


@dataclass
class Episode:
    kind: str
    variable: str
    start: datetime
    end: datetime
    peak: float
    peak_at: datetime
    peak_station_id: int
    threshold: float
    stations: set[int] = field(default_factory=set)
    steps: int = 0
    calm_hours: int = 0


def runs_from_readings(
    readings: Sequence[Reading], *, step: timedelta, max_gap: timedelta, min_steps: int
) -> list[Run]:
    """Une lecturas consecutivas de cada estación en tramos.

    Dos lecturas siguen en el mismo tramo si las separa a lo sumo ``step + max_gap``. Un tramo
    con menos de ``min_steps`` lecturas se descarta.
    """
    runs: list[Run] = []
    current: list[Reading] = []

    def close() -> None:
        if len(current) >= min_steps:
            top = max(current, key=lambda r: r.value)
            runs.append(
                Run(
                    station_id=current[0].station_id,
                    start=current[0].at,
                    end=current[-1].at + step,
                    peak=top.value,
                    peak_at=top.at,
                    threshold=top.threshold,
                    steps=len(current),
                )
            )
        current.clear()

    for reading in sorted(readings, key=lambda r: (r.station_id, r.at)):
        if current and (
            reading.station_id != current[-1].station_id
            or reading.at - current[-1].at > step + max_gap
        ):
            close()
        current.append(reading)
    close()
    return runs


def merge_runs(kind: str, variable: str, runs: Sequence[Run], max_gap: timedelta) -> list[Episode]:
    """Agrupa en un episodio los tramos de cualquier estación que se solapan o casi se tocan."""
    episodes: list[Episode] = []
    for run in sorted(runs, key=lambda r: (r.start, r.station_id)):
        last = episodes[-1] if episodes else None
        if last and run.start <= last.end + max_gap:
            last.end = max(last.end, run.end)
            last.stations.add(run.station_id)
            last.steps = max(last.steps, run.steps)
            if run.peak > last.peak:
                last.peak, last.peak_at = run.peak, run.peak_at
                last.peak_station_id, last.threshold = run.station_id, run.threshold
            continue
        episodes.append(
            Episode(
                kind=kind,
                variable=variable,
                start=run.start,
                end=run.end,
                peak=run.peak,
                peak_at=run.peak_at,
                peak_station_id=run.station_id,
                threshold=run.threshold,
                stations={run.station_id},
                steps=run.steps,
            )
        )
    return episodes


# ── Lecturas por encima del umbral, en SQL ──────────────────────────────────────────

ROLLING_SQL = """
SELECT station_id, ts, mean FROM (
    SELECT station_id, ts,
           avg(value) OVER w AS mean,
           count(value) OVER w AS n
    FROM observation
    WHERE variable_code = :variable AND quality_flag <> 'rejected'
      AND (CAST(:stations AS int[]) IS NULL OR station_id = ANY(CAST(:stations AS int[])))
    WINDOW w AS (PARTITION BY station_id ORDER BY ts
                 RANGE BETWEEN CAST(:window AS interval) PRECEDING AND CURRENT ROW)
) rolling
WHERE n >= :min_hours AND mean > :limit
"""

HOURLY_SQL = """
SELECT station_id, ts, value FROM observation
WHERE variable_code = :variable AND quality_flag = 'ok' AND value > :limit
  AND (CAST(:stations AS int[]) IS NULL OR station_id = ANY(CAST(:stations AS int[])))
"""

# Máxima diaria (con al menos 18 horas medidas) frente al percentil 90 de la estación para
# ese mes del año, calculado con todos los años disponibles (y al menos 20 días).
HEAT_SQL = """
WITH daily AS (
    SELECT station_id, (ts AT TIME ZONE 'America/Bogota')::date AS day, max(value) AS tmax
    FROM observation
    WHERE variable_code = 'temp_2m' AND quality_flag = 'ok'
      AND (CAST(:stations AS int[]) IS NULL OR station_id = ANY(CAST(:stations AS int[])))
    GROUP BY 1, 2
    HAVING count(*) >= 18
), climate AS (
    SELECT station_id, extract(month FROM day) AS month,
           percentile_cont(:percentile) WITHIN GROUP (ORDER BY tmax) AS limit
    FROM daily GROUP BY 1, 2 HAVING count(*) >= 20
)
SELECT d.station_id, d.day, d.tmax, c.limit
FROM daily d JOIN climate c ON c.station_id = d.station_id AND c.month = extract(month FROM d.day)
WHERE d.tmax > c.limit
"""


def _stations(station_ids: Sequence[int] | None) -> list[int] | None:
    return list(station_ids) if station_ids is not None else None


def rolling_readings(
    conn: Connection,
    variable: str,
    *,
    hours: int,
    min_hours: int,
    limit: float,
    station_ids: Sequence[int] | None = None,
) -> list[Reading]:
    rows = conn.execute(
        text(ROLLING_SQL),
        {
            "variable": variable,
            "window": f"{hours - 1} hours",
            "min_hours": min_hours,
            "limit": limit,
            "stations": _stations(station_ids),
        },
    )
    return [Reading(station, ts, mean, limit) for station, ts, mean in rows]


def hourly_readings(
    conn: Connection, variable: str, *, limit: float, station_ids: Sequence[int] | None = None
) -> list[Reading]:
    rows = conn.execute(
        text(HOURLY_SQL),
        {"variable": variable, "limit": limit, "stations": _stations(station_ids)},
    )
    return [Reading(station, ts, value, limit) for station, ts, value in rows]


def heat_readings(conn: Connection, station_ids: Sequence[int] | None = None) -> list[Reading]:
    rows = conn.execute(
        text(HEAT_SQL), {"percentile": HEAT_PERCENTILE, "stations": _stations(station_ids)}
    )
    return [
        Reading(station, datetime.combine(day, time(), BOGOTA), tmax, limit)
        for station, day, tmax, limit in rows
    ]


def detect(conn: Connection, station_ids: Sequence[int] | None = None) -> list[Episode]:
    """Aplica todas las reglas y devuelve los episodios, del más antiguo al más reciente."""
    heat = heat_readings(conn, station_ids)
    pm25 = rolling_readings(
        conn, "pm25", hours=24, min_hours=18, limit=PM25_LIMIT_24H, station_ids=station_ids
    )
    o3 = rolling_readings(
        conn, "o3", hours=8, min_hours=6, limit=O3_LIMIT_8H, station_ids=station_ids
    )
    rain = hourly_readings(conn, "precip", limit=HEAVY_RAIN_MM_H, station_ids=station_ids)
    rules = [
        # (episodio, variable, tramos por estación, separación máxima para unir estaciones)
        (
            "heat",
            "temp_2m",
            runs_from_readings(heat, step=DAY, max_gap=timedelta(), min_steps=HEAT_MIN_DAYS),
            timedelta(),
        ),
        (
            "pm25",
            "pm25",
            runs_from_readings(pm25, step=HOUR, max_gap=2 * HOUR, min_steps=1),
            6 * HOUR,
        ),
        ("o3", "o3", runs_from_readings(o3, step=HOUR, max_gap=2 * HOUR, min_steps=1), 6 * HOUR),
        (
            "heavy_rain",
            "precip",
            runs_from_readings(rain, step=HOUR, max_gap=HOUR, min_steps=1),
            3 * HOUR,
        ),
    ]
    episodes = [
        episode
        for kind, variable, runs, gap in rules
        for episode in merge_runs(kind, variable, runs, gap)
    ]
    for episode in episodes:
        if episode.kind in ("pm25", "o3"):
            episode.calm_hours = calm_hours(conn, episode.start, episode.end)
    return sorted(episodes, key=lambda e: e.start)


CALM_SQL = """
SELECT count(*) FROM (
    SELECT ts FROM observation
    WHERE variable_code = 'wind_speed' AND quality_flag = 'ok' AND ts >= :start AND ts < :end
    GROUP BY ts HAVING avg(value) < :calm
) calm
"""


def calm_hours(conn: Connection, start: datetime, end: datetime) -> int:
    """Horas del intervalo en que la media del viento entre estaciones fue menor a 1 m/s."""
    hours: int = conn.execute(
        text(CALM_SQL), {"start": start, "end": end, "calm": CALM_WIND_MS}
    ).scalar_one()
    return hours


# ── Textos ─────────────────────────────────────────────────────────────────────────

MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)  # fmt: skip


def _date(moment: datetime | date) -> str:
    local = moment.astimezone(BOGOTA) if isinstance(moment, datetime) else moment
    return f"{local.day} de {MONTHS[local.month - 1]} de {local.year}"


def _number(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def _hours(count: int) -> str:
    return "1 hora" if count == 1 else f"{count} horas"


def _others(episode: Episode) -> str:
    others = len(episode.stations) - 1
    if others == 0:
        return ""
    return f" Lo registraron {others + 1} estaciones."


def severity(episode: Episode) -> str:
    if episode.kind == "pm25":
        return "Dañina" if episode.peak >= 55.5 else "Dañina para grupos sensibles"
    if episode.kind == "heavy_rain":
        return "Muy fuerte" if episode.peak >= 40 else "Fuerte"
    if episode.kind == "heat":
        return "Larga" if episode.steps >= 5 else "Corta"
    return "Sobre la norma"


def summary(episode: Episode, names: dict[int, str]) -> str:
    """Una o dos frases que cuentan el episodio con sus números."""
    where = names.get(episode.peak_station_id, "una estación")
    hours = round((episode.end - episode.start) / HOUR)
    calm = (
        f" Coincidió con {_hours(episode.calm_hours)} de calma (viento de menos de 1 m/s)."
        if episode.calm_hours >= 3
        else ""
    )
    if episode.kind == "heat":
        days = round((episode.end - episode.start) / DAY)
        return (
            f"{days} días seguidos con máximas por encima de lo habitual para el mes. "
            f"Pico de {_number(episode.peak)} °C en {where} el {_date(episode.peak_at)}."
            + _others(episode)
        )
    if episode.kind == "pm25":
        return (
            f"El PM2.5 superó la norma de 24 horas ({_number(PM25_LIMIT_24H, 0)} µg/m³) durante "
            f"{_hours(hours)}. La media de 24 horas llegó a {_number(episode.peak)} µg/m³ "
            f"en {where}." + _others(episode) + calm
        )
    if episode.kind == "o3":
        return (
            f"El ozono superó la norma de 8 horas ({_number(O3_LIMIT_8H, 0)} µg/m³) durante "
            f"{_hours(hours)}, con un máximo de {_number(episode.peak)} µg/m³ en {where}."
            + _others(episode)
            + calm
        )
    return f"Cayeron {_number(episode.peak)} mm en una hora en {where}." + _others(episode)


def save(conn: Connection, episodes: Sequence[Episode]) -> None:
    """Reemplaza los episodios guardados: la detección se recalcula completa cada vez."""
    names: dict[int, str] = {
        station_id: name for station_id, name in conn.execute(text("SELECT id, name FROM station"))
    }
    conn.execute(text("DELETE FROM episode"))
    for episode in episodes:
        conn.execute(
            text(
                "INSERT INTO episode (kind, variable_code, starts_at, ends_at, peak_value, "
                "    peak_station_id, peak_at, stations, severity, summary, threshold, calm_hours) "
                "VALUES (:kind, :variable, :start, :end, :peak, :station, :peak_at, :stations, "
                "    :severity, :summary, :threshold, :calm)"
            ),
            {
                "kind": episode.kind,
                "variable": episode.variable,
                "start": episode.start,
                "end": episode.end,
                "peak": episode.peak,
                "station": episode.peak_station_id,
                "peak_at": episode.peak_at,
                "stations": sorted(episode.stations),
                "severity": severity(episode),
                "summary": summary(episode, names),
                "threshold": episode.threshold,
                "calm": episode.calm_hours,
            },
        )

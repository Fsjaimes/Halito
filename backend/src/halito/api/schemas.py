"""Formas de las respuestas de la API."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class Health(BaseModel):
    status: str
    version: str
    schema_revision: str | None
    extensions: dict[str, str]


class Variable(BaseModel):
    code: str
    name: str
    unit: str
    kind: str
    first_day: date
    last_day: date


class Station(BaseModel):
    id: int
    source: str
    name: str
    municipality: str
    lat: float
    lon: float
    elevation_m: int | None
    variables: list[str]


class Frames(BaseModel):
    """Matriz tiempo × estación de una variable, lista para animar.

    ``values[i][j]`` es el valor en ``timestamps[i]`` de la estación ``stations[j]``;
    ``null`` si esa hora no tiene dato válido.
    """

    variable: str
    unit: str
    stations: list[int]
    timestamps: list[datetime]
    values: list[list[float | None]]


class HeatPeriod(BaseModel):
    period: str
    scenes: int
    cells: int


class HeatSummary(BaseModel):
    """Qué hay de calor de superficie: escenas usadas, fechas y periodos disponibles."""

    scenes_used: int
    scenes_discarded: int
    first_scene: date | None
    last_scene: date | None
    periods: list[HeatPeriod]


class HeatCells(BaseModel):
    """Celdas H3 de un periodo, en columnas alineadas.

    ``lst_c`` es la temperatura de superficie mediana y ``anomaly_c`` cuánto se aparta la celda
    de la mediana del área, en °C. ``n_scenes`` indica con cuántas escenas se calculó.
    """

    period: str
    cells: list[str]
    lst_c: list[float]
    anomaly_c: list[float]
    n_scenes: list[int]


class Episode(BaseModel):
    """Un tramo en que una o varias estaciones superaron un umbral. ``ends_at`` es exclusivo."""

    id: int
    kind: Literal["heat", "pm25", "o3", "heavy_rain"]
    variable: str
    starts_at: datetime
    ends_at: datetime
    peak_value: float
    peak_at: datetime
    peak_station_id: int
    stations: list[int]
    threshold: float
    severity: str
    summary: str
    calm_hours: int

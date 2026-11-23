"""Temperatura de superficie de Landsat 8/9 resumida en celdas H3.

Cada escena se lleva a una rejilla fija sobre el área metropolitana, se le quitan nubes y
sombras, y se promedia por celda. De cada escena se guarda también la *anomalía*: cuánto más
caliente o fría está la celda que la mediana de esa misma escena. La anomalía se puede comparar
entre fechas (el valor absoluto depende del día y de la hora de paso), y es lo que deja ver
las islas de calor.

Ojo: es la temperatura del suelo y los techos hacia las 10:30 a. m., no la del aire.
"""

from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo

import h3.api.basic_int as h3
import numpy as np
import numpy.typing as npt
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT

from halito.quality.clean import LOCAL_TZ

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "landsat-c2-l2"

# Área de estudio: Bucaramanga, Floridablanca, Girón, Piedecuesta y el borde de Lebrija.
WEST, SOUTH, EAST, NORTH = -73.26, 6.93, -73.02, 7.23
BBOX = (WEST, SOUTH, EAST, NORTH)
PIXEL_DEG = 0.00027  # unos 30 m, la resolución de la banda térmica remuestreada
WIDTH = round((EAST - WEST) / PIXEL_DEG)
HEIGHT = round((NORTH - SOUTH) / PIXEL_DEG)
GRID_TRANSFORM = from_origin(WEST, NORTH, PIXEL_DEG, PIXEL_DEG)

H3_RESOLUTION = 9  # hexágonos de unos 0,1 km²

# Banda ST_B10 de Landsat Collection 2 Level-2: kelvin = DN × escala + desplazamiento.
ST_SCALE = 0.00341802
ST_OFFSET = 149.0
KELVIN = 273.15
# Bits de QA_PIXEL que invalidan un píxel: relleno, nube dilatada, cirros, nube y sombra.
QA_REJECT = 0b11111

MIN_CELL_COVERAGE = 0.5  # porción de píxeles despejados para aceptar una celda
MIN_SCENE_COVERAGE = 0.4  # porción de celdas válidas para aceptar una escena

FloatArray = npt.NDArray[np.float32]
IntArray = npt.NDArray[np.int64]


def surface_temperature(st_dn: npt.NDArray[np.uint16], qa: npt.NDArray[np.uint16]) -> FloatArray:
    """Convierte la banda térmica a °C y deja NaN donde hay nube, sombra o no hay dato."""
    celsius = st_dn.astype(np.float32) * ST_SCALE + (ST_OFFSET - KELVIN)
    invalid = (st_dn == 0) | ((qa & QA_REJECT) != 0)
    celsius[invalid] = np.nan
    return celsius


@cache
def grid_cells() -> IntArray:
    """Celda H3 del centro de cada píxel de la rejilla, en el mismo orden que la imagen."""
    lons = WEST + (np.arange(WIDTH) + 0.5) * PIXEL_DEG
    lats = NORTH - (np.arange(HEIGHT) + 0.5) * PIXEL_DEG
    cells = np.empty((HEIGHT, WIDTH), dtype=np.int64)
    for row, lat in enumerate(lats):
        for col, lon in enumerate(lons):
            cells[row, col] = h3.latlng_to_cell(float(lat), float(lon), H3_RESOLUTION)
    return cells


@dataclass(frozen=True)
class SceneCells:
    """Resumen de una escena: celdas válidas, su temperatura y su anomalía."""

    cells: IntArray
    lst_c: FloatArray
    anomaly_c: FloatArray
    clear_fraction: float

    @property
    def usable(self) -> bool:
        return self.clear_fraction >= MIN_SCENE_COVERAGE


def summarize(celsius: FloatArray, cells: IntArray) -> SceneCells:
    """Promedia la temperatura por celda y calcula la anomalía respecto a la mediana de la escena.

    Una celda cuenta solo si al menos la mitad de sus píxeles está despejada: un par de píxeles
    entre nubes no representa al hexágono.
    """
    unique, inverse = np.unique(cells.ravel(), return_inverse=True)
    values = celsius.ravel()
    clear = ~np.isnan(values)
    total = np.bincount(inverse, minlength=unique.size)
    seen = np.bincount(inverse, weights=clear, minlength=unique.size)
    sums = np.bincount(inverse, weights=np.where(clear, values, 0.0), minlength=unique.size)

    valid = seen >= MIN_CELL_COVERAGE * total
    mean = (sums[valid] / seen[valid]).astype(np.float32)
    anomaly = mean - np.median(mean) if mean.size else mean
    return SceneCells(
        cells=unique[valid],
        lst_c=mean,
        anomaly_c=anomaly.astype(np.float32),
        clear_fraction=float(valid.mean()) if unique.size else 0.0,
    )


def season_of(acquired_at: datetime) -> str:
    """Trimestre climático de la fecha, en hora de Colombia."""
    month = acquired_at.astimezone(ZoneInfo(LOCAL_TZ)).month
    return ("DEF", "DEF", "MAM", "MAM", "MAM", "JJA", "JJA", "JJA", "SON", "SON", "SON", "DEF")[
        month - 1
    ]


def search_scenes(since: datetime, until: datetime, max_cloud: float) -> list[Any]:
    """Escenas de Landsat 8 y 9 que tocan el área, de la menos a la más nublada."""
    import pystac_client

    catalog = pystac_client.Client.open(STAC_URL)
    search = catalog.search(
        collections=[COLLECTION],
        bbox=BBOX,
        datetime=f"{since:%Y-%m-%d}/{until:%Y-%m-%d}",
        query={
            "platform": {"in": ["landsat-8", "landsat-9"]},
            "eo:cloud_cover": {"lt": max_cloud},
        },
    )
    return sorted(search.items(), key=lambda item: item.properties["eo:cloud_cover"])


def read_scene(item: Any) -> SceneCells:
    """Descarga solo la ventana del área de estudio de las bandas térmica y de calidad."""
    import planetary_computer

    signed = planetary_computer.sign(item)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY=3):
        st_dn = _read_on_grid(signed.assets["lwir11"].href)
        qa = _read_on_grid(signed.assets["qa_pixel"].href)
    return summarize(surface_temperature(st_dn, qa), grid_cells())


def _read_on_grid(href: str) -> npt.NDArray[np.uint16]:
    # «Vecino más cercano»: interpolar mezclaría valores de calidad, que son banderas de bits.
    with (
        rasterio.open(href) as source,
        WarpedVRT(
            source,
            crs="EPSG:4326",
            transform=GRID_TRANSFORM,
            width=WIDTH,
            height=HEIGHT,
            resampling=Resampling.nearest,
        ) as on_grid,
    ):
        band: npt.NDArray[np.uint16] = on_grid.read(1)
        return band


def cell_id(cell: int) -> str:
    return str(h3.int_to_str(cell))

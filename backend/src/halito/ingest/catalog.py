"""Qué estaciones y datasets alimentan Hálito.

Las estaciones se identifican siempre por código: los nombres y municipios cambian de
escritura con el tiempo («BUCARAMANGA» → «Bucaramanga») y filtrar por ellos pierde datos.
"""

from dataclasses import dataclass
from datetime import datetime

# Primer instante que se ingiere: el inicio de los datasets por estación de la CDMB.
DEFAULT_SINCE = datetime(2024, 1, 1)

MMHG_TO_HPA = 1.333224


@dataclass(frozen=True)
class Station:
    source: str
    code: str
    name: str
    municipality: str
    lat: float
    lon: float
    elevation_m: int | None
    aliases: tuple[str, ...] = ()
    # Solo CDMB: cada estación publica su propio dataset.
    dataset_id: str | None = None

    @property
    def codes(self) -> tuple[str, ...]:
        return (self.code, *self.aliases)


STATIONS: tuple[Station, ...] = (
    # La altitud de la UIS figura como 118 m en el catálogo del IDEAM, un error evidente
    # (la meseta está cerca de los 1.000 m). Se deja vacía hasta tomarla del modelo de elevación.
    Station("ideam", "0023195040", "Universidad Industrial de Santander", "Bucaramanga",
            7.144722, -73.122222, None),
    Station("ideam", "2319500043", "Floridablanca", "Floridablanca", 7.056797, -73.090627, 894),
    Station("ideam", "0023195110", "Llano Grande", "Girón", 7.025556, -73.167222, 777),
    Station("ideam", "0023190600", "El Pantano", "Girón", 6.997500, -73.230278, 1280),
    Station("ideam", "2319500065", "Granja Piedecuesta", "Piedecuesta", 6.993333, -73.067778, 949,
            aliases=("0023190700",)),
    Station("ideam", "0023195502", "Aeropuerto Palonegro", "Lebrija", 7.121472, -73.184528, 1189),
    Station("ideam", "0023190440", "El Naranjo", "Lebrija", 7.205000, -73.299722, 825),
    Station("ideam", "0023197690", "Palogordo", "Girón", 6.969167, -73.130556, 690),
    Station("cdmb", "31916", "Club Unión", "Bucaramanga", 7.114339, -73.110959, 976,
            dataset_id="5dsf-x7tb"),
    Station("cdmb", "31914", "Colegio Gaitán", "Bucaramanga", 7.130190, -73.134559, 928,
            dataset_id="fvvs-hxtq"),
    Station("cdmb", "31915", "Hospital del Norte", "Bucaramanga", 7.149116, -73.134661, 787,
            dataset_id="3v9a-6acv"),
    Station("cdmb", "31913", "Centro Cultural", "Piedecuesta", 6.985113, -73.048825, 1008,
            dataset_id="kh7q-whyx"),
)  # fmt: skip

# IDEAM: un dataset nacional por variable.
IDEAM_DATASETS: dict[str, str] = {
    "sbwg-7ju4": "temp_2m",
    "uext-mhny": "rh_2m",
    "62tk-nxj5": "pressure",
    "sgfv-3yp8": "wind_speed",
    "kiw7-v9ta": "wind_dir",
    "s54a-sgyg": "precip",
}

# CDMB: una columna por variable. El factor lleva el valor a la unidad de Hálito.
CDMB_COLUMNS: dict[str, tuple[str, float]] = {
    "temperatura_ambiente_celsius": ("temp_2m", 1.0),
    "humedad_relativa": ("rh_2m", 1.0),
    "presi_n_atmosf_rica_mmhg": ("pressure", MMHG_TO_HPA),
    "velocidad_del_viento_m_s": ("wind_speed", 1.0),
    "direcci_n_del_viento": ("wind_dir", 1.0),
    "precipitaci_n_mm": ("precip", 1.0),
    "radiaci_n_solar_w_m": ("radiation", 1.0),
    "pm2_5_g_m": ("pm25", 1.0),
    "pm10_g_m": ("pm10", 1.0),
    "o3_g_m": ("o3", 1.0),
    "no_g_m": ("no", 1.0),
    "no2_g_m": ("no2", 1.0),
    "so2_g_m": ("so2", 1.0),
    "co_g_m": ("co", 1.0),
}


def stations(source: str) -> tuple[Station, ...]:
    return tuple(s for s in STATIONS if s.source == source)

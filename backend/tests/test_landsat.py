from datetime import UTC, datetime

import numpy as np
import pytest
from sqlalchemy import Connection, text

from halito.ingest import landsat, load

CLEAR = 0b1000000  # QA_PIXEL de un píxel despejado
CLOUD = 0b0001000
SHADOW = 0b0010000


def dn_for(celsius: float) -> int:
    return round((celsius + landsat.KELVIN - landsat.ST_OFFSET) / landsat.ST_SCALE)


def test_convierte_a_celsius_y_anula_nubes_sombras_y_vacios() -> None:
    st = np.array([dn_for(30.0), dn_for(45.0), dn_for(12.0), 0], dtype=np.uint16)
    qa = np.array([CLEAR, CLOUD, SHADOW, CLEAR], dtype=np.uint16)

    celsius = landsat.surface_temperature(st, qa)

    assert celsius[0] == pytest.approx(30.0, abs=0.01)
    assert np.isnan(celsius[1:]).all()


def test_resume_por_celda_y_mide_la_anomalia_contra_la_mediana_de_la_escena() -> None:
    # Tres celdas de dos píxeles: una fresca, una media y una caliente.
    cells = np.array([[1, 1, 2, 2, 3, 3]], dtype=np.int64)
    celsius = np.array([[24.0, 26.0, 30.0, 30.0, 38.0, 40.0]], dtype=np.float32)

    scene = landsat.summarize(celsius, cells)

    assert scene.cells.tolist() == [1, 2, 3]
    assert scene.lst_c.tolist() == pytest.approx([25.0, 30.0, 39.0])
    assert scene.anomaly_c.tolist() == pytest.approx([-5.0, 0.0, 9.0])
    assert scene.clear_fraction == 1.0


def test_una_celda_mayormente_nublada_no_cuenta() -> None:
    cells = np.array([[1, 1, 1, 1, 2, 2, 2, 2]], dtype=np.int64)
    nan = np.nan
    celsius = np.array([[30.0, nan, nan, nan, 28.0, 30.0, nan, nan]], dtype=np.float32)

    scene = landsat.summarize(celsius, cells)

    # La celda 1 solo tiene un píxel despejado de cuatro; la 2, justo la mitad.
    assert scene.cells.tolist() == [2]
    assert scene.lst_c.tolist() == pytest.approx([29.0])
    assert scene.clear_fraction == 0.5


def test_una_escena_casi_toda_nublada_no_es_utilizable() -> None:
    cells = np.arange(10, dtype=np.int64).reshape(1, 10)
    celsius = np.full((1, 10), np.nan, dtype=np.float32)
    celsius[0, :3] = 30.0

    assert not landsat.summarize(celsius, cells).usable


def test_el_trimestre_se_toma_en_hora_de_colombia() -> None:
    assert landsat.season_of(datetime(2025, 1, 15, 15, tzinfo=UTC)) == "DEF"
    assert landsat.season_of(datetime(2025, 7, 15, 15, tzinfo=UTC)) == "JJA"
    # Las 03:00 UTC del 1 de marzo todavía son 28 de febrero en Colombia.
    assert landsat.season_of(datetime(2025, 3, 1, 3, tzinfo=UTC)) == "DEF"


def test_la_rejilla_cubre_el_area_con_celdas_de_la_resolucion_esperada() -> None:
    cells = landsat.grid_cells()

    assert cells.shape == (landsat.HEIGHT, landsat.WIDTH)
    # El píxel de la esquina noroeste cae en la celda de esa coordenada.
    assert landsat.cell_id(int(cells[0, 0])).startswith("89")
    # Hexágonos de ~0,1 km² con píxeles de ~30 m: del orden de cien píxeles por celda.
    assert 80 < cells.size / np.unique(cells).size < 160


def add_scene(conn: Connection, name: str, season: str, used: bool, lst: float) -> None:
    load.save_scene(
        conn,
        landsat_id=name,
        acquired_at=datetime(2025, 1, 1, 15, tzinfo=UTC),
        cloud_pct=10.0,
        season=season,
        clear_pct=90.0,
        used=used,
        cells=[("89prueba", lst, lst - 30.0)],
    )


def test_publica_la_mediana_por_celda_solo_con_suficientes_escenas(conn: Connection) -> None:
    conn.execute(text("DELETE FROM lst_scene"))
    for name, season, lst in [("a", "DEF", 30.0), ("b", "DEF", 34.0), ("c", "JJA", 41.0)]:
        add_scene(conn, name, season, True, lst)
    add_scene(conn, "nublada", "DEF", False, 99.0)

    load.rebuild_heat_cells(conn, min_scenes=3)

    rows = conn.execute(text("SELECT period, lst_c_median, anomaly_c, n_scenes FROM lst_cell"))
    # Tres escenas útiles en total, pero ningún trimestre llega a tres: solo se publica «all».
    assert [tuple(row) for row in rows] == [("all", 34.0, 4.0, 3)]
    assert load.known_scenes(conn) == {"a", "b", "c", "nublada"}

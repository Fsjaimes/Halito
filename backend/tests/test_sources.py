from datetime import datetime

import pytest

from halito.ingest import cdmb, ideam
from halito.ingest.catalog import MMHG_TO_HPA


def test_ideam_convierte_filas_de_la_api_en_lecturas() -> None:
    rows = [
        {"fechaobservacion": "2026-11-14T23:00:00.000", "valorobservado": "21.4"},
        {"fechaobservacion": "2026-11-14T23:10:00.000", "valorobservado": "no-es-numero"},
    ]

    frame = ideam.parse_rows(rows, "0023195040", "temp_2m")

    assert frame.rows() == [
        ("0023195040", "temp_2m", datetime(2026, 11, 14, 23, 0), 21.4, "21.4"),
        ("0023195040", "temp_2m", datetime(2026, 11, 14, 23, 10), None, "no-es-numero"),
    ]


def test_ideam_lleva_a_hora_local_lo_publicado_en_utc_antes_de_agosto_de_2026() -> None:
    rows = [
        # Las 18:00 UTC son las 13:00 en Colombia: la hora más caliente del día.
        {"fechaobservacion": "2026-07-24T18:00:00.000", "valorobservado": "29.1"},
        {"fechaobservacion": "2026-08-12T13:00:00.000", "valorobservado": "28.7"},
    ]

    frame = ideam.parse_rows(rows, "0023195040", "temp_2m")

    assert frame["ts"].to_list() == [datetime(2026, 7, 24, 13), datetime(2026, 8, 12, 13)]


def test_ideam_usa_un_solo_sensor_por_hora_cuando_hay_dos() -> None:
    readings = [
        # Palonegro: el sensor convencional (0240) y el GPRS (0257) miden la misma lluvia.
        ("2026-10-20T15:00:00.000", "2.0", "0240"),
        ("2026-10-20T15:01:00.000", "0.3", "0257"),
        ("2026-10-20T15:02:00.000", "0.4", "0257"),
        # En la hora siguiente solo reportó el GPRS: se usa.
        ("2026-10-20T16:05:00.000", "1.1", "0257"),
    ]
    rows = [
        {"fechaobservacion": ts, "valorobservado": value, "codigosensor": sensor}
        for ts, value, sensor in readings
    ]

    frame = ideam.parse_rows(rows, "0023195502", "precip")

    assert frame["value"].to_list() == [2.0, 1.1]


def test_ideam_descarta_lecturas_repetidas() -> None:
    readings = [("15:10", "1.6"), ("15:10", "1.6"), ("15:20", "0.4")]
    rows = [
        {
            "fechaobservacion": f"2024-11-21T{time}:00.000",
            "valorobservado": value,
            "codigosensor": "0240",
        }
        for time, value in readings
    ]

    frame = ideam.parse_rows(rows, "2319500043", "precip")

    assert frame["value"].to_list() == [1.6, 0.4]


def test_ideam_pide_los_datos_por_tramos_contiguos_sin_huecos() -> None:
    start, end = datetime(2024, 1, 1), datetime(2024, 9, 1)

    spans = ideam.windows(start, end)

    assert len(spans) == 9
    assert spans[0][0] == start and spans[-1][1] == end
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:], strict=False))


def test_cdmb_pasa_de_columnas_a_lecturas_y_omite_lo_que_no_se_mide() -> None:
    rows = [
        {
            "fecha_y_hora": "2024-02-18T01:00:00.000",
            "temperatura_ambiente_celsius": "25.055",
            "pm2_5_g_m": "24.717",
            "co_g_m": "-999.000",  # lectura inválida: se conserva para rechazarla
            "trs_g_m": "NA",  # variable que Hálito no usa
            "so2_g_m": "NA",  # la estación no la mide: no genera lectura
        }
    ]

    frame = cdmb.parse_rows(rows, "31916")

    values = dict(frame.select("variable", "value").rows())
    assert values == {"temp_2m": 25.055, "pm25": 24.717, "co": -999.0}


def test_cdmb_lleva_la_presion_de_mmhg_a_hpa() -> None:
    rows = [{"fecha_y_hora": "2024-02-18T01:00:00.000", "presi_n_atmosf_rica_mmhg": "679.535"}]

    frame = cdmb.parse_rows(rows, "31916")

    assert frame["value"][0] == pytest.approx(679.535 * MMHG_TO_HPA)
    assert frame["value"][0] == pytest.approx(906.0, abs=0.5)
    assert frame["raw_value"][0] == "679.535"

import pytest

from halito.config import Settings


def test_database_url_se_arma_con_las_variables_de_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HALITO_DB_HOST", "db.interna")
    monkeypatch.setenv("HALITO_DB_PORT", "6000")
    monkeypatch.setenv("HALITO_DB_PASSWORD", "con@arroba")

    url = Settings(_env_file=None).database_url

    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port) == ("db.interna", 6000)
    # Los caracteres especiales de la contraseña no rompen la URL.
    assert url.password == "con@arroba"

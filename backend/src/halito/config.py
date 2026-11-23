"""Configuración leída de variables de entorno (prefijo ``HALITO_``)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HALITO_",
        env_file=(".env", "../.env"),
        extra="ignore",
    )

    db_host: str = "localhost"
    db_port: int = 5433
    db_user: str = "halito"
    db_password: str = "halito_dev"
    db_name: str = "halito"

    socrata_app_token: str | None = None

    @property
    def database_url(self) -> URL:
        """URL para SQLAlchemy; psycopg 3 sirve tanto en modo síncrono como asíncrono."""
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

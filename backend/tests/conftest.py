"""Los tests de integración usan la base de datos de ``docker compose`` ya migrada.

Si no está disponible se omiten, salvo que ``HALITO_REQUIRE_DB=1`` (así corre en CI),
caso en el que fallan para que una base caída no pase inadvertida.
"""

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from halito.db.session import get_engine, get_sync_engine


@pytest.fixture
async def db() -> AsyncIterator[AsyncConnection]:
    """Conexión dentro de una transacción que se deshace al terminar el test."""
    try:
        connection = await get_engine().connect()
    except OperationalError as error:
        if os.environ.get("HALITO_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"Base de datos no disponible: {error.orig}")
    try:
        if not await connection.scalar(text("SELECT to_regclass('alembic_version') IS NOT NULL")):
            pytest.fail("La base de datos no está migrada: ejecuta `make migrate`.")
        yield connection
    finally:
        await connection.rollback()
        await connection.close()


@pytest.fixture
def conn() -> Iterator[Connection]:
    """Igual que ``db`` pero síncrona, para el código de ingesta."""
    try:
        connection = get_sync_engine().connect()
    except OperationalError as error:
        if os.environ.get("HALITO_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"Base de datos no disponible: {error.orig}")
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()

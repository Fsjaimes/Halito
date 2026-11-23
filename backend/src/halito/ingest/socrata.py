"""Cliente mínimo de la API de datos.gov.co (Socrata / SODA)."""

import time
from collections.abc import Iterator
from typing import Any

import httpx

Row = dict[str, Any]

RETRYABLE = {429, 500, 502, 503, 504}


class SocrataClient:
    def __init__(
        self,
        *,
        domain: str = "www.datos.gov.co",
        app_token: str | None = None,
        page_size: int = 1_000_000,
        max_retries: int = 4,
        backoff_seconds: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"X-App-Token": app_token} if app_token else {}
        self._http = httpx.Client(
            base_url=f"https://{domain}/resource",
            headers=headers,
            timeout=httpx.Timeout(120.0, connect=15.0),
            transport=transport,
        )
        self._page_size = page_size
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    def close(self) -> None:
        self._http.close()

    def rows(self, dataset: str, *, select: str, where: str, order: str) -> list[Row]:
        """Devuelve todas las filas que cumplen ``where``.

        Primero las pide de una vez y sin ordenar: ordenar un dataset de decenas de millones
        de filas multiplica el tiempo de respuesta, y si el resultado cabe en una página el
        orden no importa. Solo cuando no cabe se pagina, y entonces sí con orden.
        """
        params = {"$select": select, "$where": where, "$limit": str(self._page_size)}
        first = self._get(dataset, params)
        if len(first) < self._page_size:
            return first
        return [
            row
            for page in self.pages(dataset, select=select, where=where, order=order)
            for row in page
        ]

    def pages(self, dataset: str, *, select: str, where: str, order: str) -> Iterator[list[Row]]:
        """Recorre las filas de página en página.

        El orden termina en ``:id`` para que sea total: sin eso, dos filas con la misma
        marca de tiempo pueden intercambiarse entre páginas y se pierde o repite alguna.
        """
        offset = 0
        while True:
            page = self._get(
                dataset,
                {
                    "$select": select,
                    "$where": where,
                    "$order": f"{order}, :id",
                    "$limit": str(self._page_size),
                    "$offset": str(offset),
                },
            )
            if page:
                yield page
            if len(page) < self._page_size:
                return
            offset += self._page_size

    def _get(self, dataset: str, params: dict[str, str]) -> list[Row]:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.get(f"/{dataset}.json", params=params)
            except httpx.TransportError:
                if attempt == self._max_retries:
                    raise
            else:
                if response.status_code not in RETRYABLE or attempt == self._max_retries:
                    response.raise_for_status()
                    rows: list[Row] = response.json()
                    return rows
            time.sleep(self._backoff_seconds * 2**attempt)
        raise AssertionError("inalcanzable")

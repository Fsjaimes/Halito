import httpx
import pytest

from halito.ingest.socrata import SocrataClient

QUERY = {"select": "a", "where": "a > 1", "order": "a"}


def client_for(
    handler: httpx.MockTransport, *, page_size: int = 50_000, max_retries: int = 4
) -> SocrataClient:
    return SocrataClient(
        transport=handler,
        backoff_seconds=0,
        app_token="token-de-prueba",
        page_size=page_size,
        max_retries=max_retries,
    )


def test_recorre_todas_las_paginas_con_un_orden_total() -> None:
    data = [{"a": str(n)} for n in range(5)]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = int(request.url.params["$offset"])
        return httpx.Response(200, json=data[offset : offset + 2])

    pages = list(client_for(httpx.MockTransport(handler), page_size=2).pages("abcd-1234", **QUERY))

    assert [row for page in pages for row in page] == data
    assert [request.url.params["$offset"] for request in requests] == ["0", "2", "4"]
    assert requests[0].url.path == "/resource/abcd-1234.json"
    assert requests[0].url.params["$order"] == "a, :id"
    assert requests[0].headers["X-App-Token"] == "token-de-prueba"


def test_si_todo_cabe_en_una_pagina_no_ordena_ni_pagina() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[{"a": "1"}, {"a": "2"}])

    rows = client_for(httpx.MockTransport(handler), page_size=10).rows("abcd-1234", **QUERY)

    assert rows == [{"a": "1"}, {"a": "2"}]
    assert len(requests) == 1
    assert "$order" not in requests[0].url.params


def test_si_no_cabe_vuelve_a_pedir_ordenado_y_por_paginas() -> None:
    data = [{"a": str(n)} for n in range(5)]

    def handler(request: httpx.Request) -> httpx.Response:
        if "$order" not in request.url.params:
            return httpx.Response(200, json=data[:2])
        offset = int(request.url.params["$offset"])
        return httpx.Response(200, json=data[offset : offset + 2])

    rows = client_for(httpx.MockTransport(handler), page_size=2).rows("abcd-1234", **QUERY)

    assert rows == data


def test_reintenta_cuando_el_servidor_falla_de_forma_pasajera() -> None:
    responses = iter([httpx.Response(503), httpx.Response(200, json=[{"a": "1"}])])

    pages = list(
        client_for(httpx.MockTransport(lambda _: next(responses))).pages("abcd-1234", **QUERY)
    )

    assert pages == [[{"a": "1"}]]


def test_un_error_de_consulta_no_se_reintenta() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"message": "query.soql.type-mismatch"})

    with pytest.raises(httpx.HTTPStatusError):
        list(client_for(httpx.MockTransport(handler)).pages("abcd-1234", **QUERY))
    assert calls == 1


def test_se_rinde_tras_agotar_los_reintentos() -> None:
    client = client_for(httpx.MockTransport(lambda _: httpx.Response(503)), max_retries=2)

    with pytest.raises(httpx.HTTPStatusError):
        list(client.pages("abcd-1234", **QUERY))

import json
from pathlib import Path

import httpx
import pytest

from comparador.providers.mercadona import BASE_URL, MercadonaProvider
from comparador.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_fake_mercadona(category_72: dict | None = None) -> MercadonaProvider:
    """Provider de Mercadona que responde con los JSON guardados, sin red.

    Los fixtures son una muestra real recortada de la API. `category_72`
    permite sustituir esa respuesta para probar casos límite."""
    routes = {
        "/api/categories/": load("mercadona_categories.json"),
        "/api/categories/72/": category_72 or load("mercadona_category_72.json"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["wh"] == "vlc1"
        data = routes.get(request.url.path)
        return httpx.Response(200, json=data) if data else httpx.Response(404)

    client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    return MercadonaProvider(delay=0, client=client)


@pytest.fixture
def fake_mercadona() -> MercadonaProvider:
    return make_fake_mercadona()


@pytest.fixture
def storage() -> Storage:
    s = Storage(":memory:")
    yield s
    s.close()

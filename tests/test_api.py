import pytest
from conftest import kill_connections
from fastapi.testclient import TestClient

from comparador.api import create_app


@pytest.fixture
def client(db_url, seeded):
    with TestClient(create_app(db_url)) as c:
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_provincias(client):
    assert [p["id"] for p in client.get("/api/v1/provinces").json()] == [
        "barcelona", "madrid", "malaga"]


def test_busqueda_por_provincia(client):
    r = client.get("/api/v1/products/search", params={"q": "semidesnatada", "province": "madrid"})
    assert r.status_code == 200
    [item] = r.json()["results"]
    assert item["price"] == 0.89 and item["zone"] == "mad3"


def test_provincia_obligatoria_y_existente(client):
    assert client.get("/api/v1/products/search", params={"q": "leche"}).status_code == 422
    r = client.get("/api/v1/products/search", params={"q": "leche", "province": "atlantida"})
    assert r.status_code == 404


def test_parametros_invalidos(client):
    base = {"q": "leche", "province": "barcelona"}
    assert client.get("/api/v1/products/search", params={**base, "sort": "nombre"}).status_code == 422
    assert client.get("/api/v1/products/search", params={**base, "q": "l"}).status_code == 422


def test_detalle_e_historial(client):
    [item] = client.get("/api/v1/products/search",
                        params={"q": "semidesnatada pack", "province": "barcelona"}).json()["results"]
    pid = item["id"]
    assert client.get(f"/api/v1/products/{pid}", params={"province": "barcelona"}).json()["price"] == 4.98
    hist = client.get(f"/api/v1/products/{pid}/history", params={"province": "barcelona"}).json()
    assert len(hist["history"]) == 1
    assert client.get(f"/api/v1/products/{pid}", params={"province": "madrid"}).status_code == 404
    assert client.get("/api/v1/products/999999/history",
                      params={"province": "barcelona"}).status_code == 404


def test_supermercados(client):
    assert client.get("/api/v1/supermarkets").json()[0]["products"] == 6


def test_api_sobrevive_a_conexiones_cortadas(client, db_url):
    assert client.get("/api/v1/provinces").status_code == 200
    kill_connections(db_url)  # p. ej. el pooler de Supabase cierra conexiones ociosas
    assert client.get("/api/v1/provinces").status_code == 200

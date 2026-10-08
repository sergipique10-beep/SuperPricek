from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from comparador.api import create_app
from comparador.importer import run_import


def test_importar_y_buscar(fake_mercadona, storage):
    result = run_import(fake_mercadona, storage, "vlc1")
    assert result.products == 4

    assert len(storage.search("leche")) == 4
    # ordenado por precio por litro: Hacendado (0,96 €/l) antes que Asturiana (1,26 €/l)
    res = storage.search("leche entera")
    assert [r["external_id"] for r in res] == ["10380", "10504"]

    assert storage.supermarkets()[0]["products"] == 4
    assert storage.supermarkets()[0]["last_import"] is not None


def test_reimportar_el_mismo_dia_no_duplica_historico(fake_mercadona, storage):
    run_import(fake_mercadona, storage, "vlc1")
    run_import(fake_mercadona, storage, "vlc1")
    pid = storage.search("semidesnatada")[0]["id"]
    assert len(storage.price_history(pid)) == 1


def test_historico_de_varios_dias(fake_mercadona, storage):
    storage.ensure_supermarket("mercadona", "Mercadona")
    item = next(fake_mercadona.fetch_all("vlc1"))
    hoy = datetime.now(timezone.utc)
    for dias, precio in [(2, 5.10), (1, 5.04), (0, 4.98)]:
        obs = replace(item.price, price=precio, observed_at=hoy - timedelta(days=dias))
        pid = storage.save(replace(item, price=obs))
    storage.commit()

    historia = storage.price_history(pid)
    assert [h["price"] for h in historia] == [5.10, 5.04, 4.98]
    assert storage.get_product(pid)["price"] == 4.98


def test_dry_run_no_escribe(fake_mercadona, storage, capsys):
    result = run_import(fake_mercadona, None, "vlc1", dry_run=True)
    assert result.products == 4
    assert "Leche entera Asturiana" in capsys.readouterr().out
    assert storage.search("leche") == []


def test_api(fake_mercadona, storage):
    run_import(fake_mercadona, storage, "vlc1")
    client = TestClient(create_app(storage))

    assert client.get("/health").json() == {"status": "ok"}

    r = client.get("/api/v1/products/search", params={"q": "leche entera asturiana"})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1 and results[0]["on_promotion"] is True

    pid = results[0]["id"]
    assert client.get(f"/api/v1/products/{pid}").json()["name"] == "Leche entera Asturiana"
    assert len(client.get(f"/api/v1/products/{pid}/history").json()["history"]) == 1

    assert client.get("/api/v1/products/9999").status_code == 404
    assert client.get("/api/v1/products/search", params={"q": "a"}).status_code == 422

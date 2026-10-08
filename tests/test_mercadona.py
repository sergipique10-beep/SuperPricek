import httpx
import pytest
from conftest import load, make_fake_mercadona

from comparador.providers.base import ZoneNotServed
from comparador.providers.mercadona import BASE_URL, MercadonaProvider, parse_product


def test_fetch_all_normaliza_datos_reales(fake_mercadona):
    items = list(fake_mercadona.fetch_all("vlc1"))
    assert [i.product.external_id for i in items] == ["10381", "10382", "10380", "10504"]

    pack = items[0]
    assert pack.product.name == "Leche semidesnatada Hacendado"
    assert pack.product.category == (
        "Huevos, leche y mantequilla > Leche y bebidas vegetales > Leche semidesnatada"
    )
    assert pack.product.size == 6.0 and pack.product.size_unit == "l"
    assert pack.product.brand is None  # el listado no trae marca
    assert pack.price.price == 4.98
    assert pack.price.unit_price == 0.83
    assert pack.price.unit == "l"
    assert pack.price.zone == "vlc1"
    # previous_unit_price llega como "        5.04" y price_decreased es false
    assert pack.price.previous_price == 5.04
    assert pack.price.on_promotion

    brik = items[1]
    assert brik.product.packaging == "Brik"
    assert brik.price.price == 0.83
    assert brik.price.previous_price is None and not brik.price.on_promotion


def test_quita_duplicados_e_ignora_sin_precio():
    data = load("mercadona_category_72.json")
    repetido = data["categories"][0]["products"][1]
    sin_precio = {**repetido, "id": "99999",
                  "price_instructions": {**repetido["price_instructions"], "unit_price": None}}
    data["categories"][1]["products"] += [repetido, sin_precio]

    ids = [i.product.external_id for i in make_fake_mercadona(data).fetch_all("vlc1")]
    assert ids == ["10381", "10382", "10380", "10504"]


def test_detecta_bajada_de_precio():
    item = parse_product({
        "id": "1",
        "display_name": "Prueba",
        "price_instructions": {"unit_price": "0.95", "previous_unit_price": "  1.05"},
    })
    assert item.price.on_promotion
    assert item.price.previous_price == 1.05


def test_ignora_productos_sin_precio():
    assert parse_product({"id": "1", "price_instructions": {"unit_price": None}}) is None


def test_acepta_decimales_con_coma():
    item = parse_product({"id": "1", "display_name": "X",
                          "price_instructions": {"unit_price": "2,50"}})
    assert item.price.price == 2.5


def _postal_provider(status: int, headers: dict | None = None) -> MercadonaProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/api/postal-codes/actions/change-pc/"
        return httpx.Response(status, json={}, headers=headers or {})

    client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    return MercadonaProvider(delay=0, client=client)


def test_zona_desde_codigo_postal():
    provider = _postal_provider(200, {"x-customer-wh": "bcn1"})
    assert provider.zone_for_postal_code("08820") == "bcn1"


def test_codigo_postal_sin_servicio():
    # Respuesta real para Málaga: 404 "outside of our working area"
    with pytest.raises(ZoneNotServed):
        _postal_provider(404).zone_for_postal_code("29001")


def test_codigo_postal_no_valido():
    with pytest.raises(ValueError):
        _postal_provider(200).zone_for_postal_code("8820")


def test_granel_usa_precio_por_kg():
    # Valores reales: unit_price viene como precio/kg x 99
    item = parse_product({
        "id": "24706",
        "display_name": "Alistado mediano congelado",
        "packaging": "Granel",
        "price_instructions": {
            "unit_price": "3663.00", "bulk_price": "37.00", "reference_price": "37.000",
            "reference_format": "kg", "unit_size": None, "size_format": "kg",
            "selling_method": 1, "approx_size": True, "previous_unit_price": None,
        },
    })
    assert item.price.price == 37.0
    assert item.price.unit_price == 37.0 and item.price.unit == "kg"

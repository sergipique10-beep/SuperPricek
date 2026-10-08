from datetime import timedelta
from decimal import Decimal

from conftest import D1, D2

from comparador import queries
from comparador.importer import run_import


def ids(rows):
    return [r["external_id"] for r in rows]


def product_id(db, external_id):
    return db.execute("select id from products where external_id = %s",
                      (external_id,)).fetchone()["id"]


def test_busca_solo_en_la_provincia(db, seeded):
    bcn = queries.search(db, "barcelona", "semidesnatada")
    assert [(r["external_id"], r["price"]) for r in bcn] == [
        ("2", Decimal("4.98")), ("1", Decimal("0.85"))]
    mad = queries.search(db, "madrid", "semidesnatada")
    assert [(r["external_id"], r["price"], r["zone"]) for r in mad] == [
        ("1", Decimal("0.89"), "mad3")]


def test_producto_que_ya_no_se_vende_no_aparece(db, seeded):
    assert "4" not in ids(queries.search(db, "barcelona", "leche"))
    assert queries.get_product(db, "barcelona", product_id(db, "4")) is None


def test_orden_por_unidad_mas_frecuente(db, seeded):
    # l (2 resultados) primero; empate 100ml/ud por nombre de unidad
    assert ids(queries.search(db, "barcelona", "leche")) == ["2", "1", "5", "3"]


def test_orden_por_precio(db, seeded):
    assert ids(queries.search(db, "barcelona", "leche", sort="price")) == ["1", "5", "3", "2"]


def test_porcentaje_y_guion_bajo_son_literales(db, seeded):
    assert ids(queries.search(db, "barcelona", "50%")) == ["6"]
    assert queries.search(db, "barcelona", "_") == []


def test_importacion_fallida_mantiene_los_ultimos_precios(db, seeded):
    seeded.failing = {"bcn1"}
    run_import(db, seeded, zone_code="bcn1", import_date=D2 + timedelta(days=1))
    assert "1" in ids(queries.search(db, "barcelona", "semidesnatada"))


def test_provincia_sin_servicio(db, seeded):
    assert queries.search(db, "malaga", "leche") == []


def test_detalle_del_producto(db, seeded):
    item = queries.get_product(db, "barcelona", product_id(db, "1"))
    assert (item["price"], item["valid_from"], item["last_seen"], item["zone"]) == (
        Decimal("0.85"), D2, D2, "bcn1")


def test_historial_por_provincia(db, seeded):
    pid = product_id(db, "1")
    hist = queries.price_history(db, "barcelona", pid)
    assert [(h["price"], h["valid_from"], h["last_seen"]) for h in hist] == [
        (Decimal("0.83"), D1, D1), (Decimal("0.85"), D2, D2)]
    assert [h["price"] for h in queries.price_history(db, "madrid", pid)] == [Decimal("0.89")]


def test_provincias(db, seeded):
    provs = {p["id"]: p for p in queries.provinces(db)}
    assert list(provs) == ["barcelona", "madrid", "malaga"]
    [bcn] = provs["barcelona"]["supermarkets"]
    assert bcn["zone"] == "bcn1" and bcn["last_import"] is not None
    assert provs["malaga"]["supermarkets"] == []


def test_existencia_y_supermercados(db, seeded):
    assert queries.province_exists(db, "barcelona")
    assert not queries.province_exists(db, "atlantida")
    assert queries.product_exists(db, product_id(db, "4"))
    assert not queries.product_exists(db, 999999)
    assert queries.supermarkets(db) == [
        {"id": "mercadona", "display_name": "Mercadona", "products": 6}]

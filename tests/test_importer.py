from decimal import Decimal

import pytest
from conftest import D1, D2, FakeProvider, make_fake_mercadona, make_item

from comparador.catalog import add_province, ensure_supermarket, ensure_zone
from comparador.importer import run_import


def periods(db, external_id):
    return db.execute(
        "select pp.price, pp.valid_from, pp.last_seen, z.code from price_periods pp "
        "join products p on p.id = pp.product_id join price_zones z on z.id = pp.zone_id "
        "where p.external_id = %s order by z.code, pp.valid_from",
        (external_id,),
    ).fetchall()


def barcelona(db, catalog, **kwargs):
    prov = FakeProvider(zones={"08001": "bcn1"}, catalogs={"bcn1": catalog}, **kwargs)
    add_province(db, [prov], "barcelona", "Barcelona", "08001")
    return prov


def test_primera_importacion(db):
    prov = barcelona(db, [make_item("1", 0.83), make_item("2", 1.20)])
    [res] = run_import(db, prov, import_date=D1)
    assert (res.zone, res.products, res.error) == ("bcn1", 2, None)
    assert periods(db, "1") == [
        {"price": Decimal("0.83"), "valid_from": D1, "last_seen": D1, "code": "bcn1"}]
    run = db.execute("select status, products, import_date from import_runs").fetchone()
    assert run == {"status": "ok", "products": 2, "import_date": D1}


def test_mismo_precio_alarga_el_periodo(db):
    prov = barcelona(db, [make_item("1", 0.83)])
    run_import(db, prov, import_date=D1)
    run_import(db, prov, import_date=D2)
    assert [(p["valid_from"], p["last_seen"]) for p in periods(db, "1")] == [(D1, D2)]


def test_precio_distinto_abre_otro_periodo(db):
    prov = barcelona(db, [make_item("1", 0.83)])
    run_import(db, prov, import_date=D1)
    prov.catalogs["bcn1"] = [make_item("1", 0.85)]
    run_import(db, prov, import_date=D2)
    assert [(p["price"], p["valid_from"], p["last_seen"]) for p in periods(db, "1")] == [
        (Decimal("0.83"), D1, D1), (Decimal("0.85"), D2, D2)]


def test_reimportar_el_mismo_dia_sobrescribe(db):
    prov = barcelona(db, [make_item("1", 0.83)])
    run_import(db, prov, import_date=D1)
    prov.catalogs["bcn1"] = [make_item("1", 0.85)]
    run_import(db, prov, import_date=D1)
    assert [(p["price"], p["valid_from"]) for p in periods(db, "1")] == [(Decimal("0.85"), D1)]


def test_fallo_en_una_zona_no_afecta_a_otra(db):
    prov = FakeProvider(zones={"08001": "bcn1", "28001": "mad3"},
                        catalogs={"bcn1": [make_item("1", 0.83)]}, failing={"mad3"})
    add_province(db, [prov], "barcelona", "Barcelona", "08001")
    add_province(db, [prov], "madrid", "Madrid", "28001")
    results = {r.zone: r for r in run_import(db, prov, import_date=D1)}
    assert results["bcn1"].error is None and results["bcn1"].products == 1
    assert "Fallo simulado" in results["mad3"].error
    runs = db.execute("select z.code, r.status from import_runs r "
                      "join price_zones z on z.id = r.zone_id").fetchall()
    assert {r["code"]: r["status"] for r in runs} == {"bcn1": "ok", "mad3": "error"}
    assert [p["code"] for p in periods(db, "1")] == ["bcn1"]


def test_catalogo_vacio_es_un_error(db):
    prov = barcelona(db, [])
    [res] = run_import(db, prov, import_date=D1)
    assert "0 productos" in res.error


def test_zona_desconocida(db):
    prov = barcelona(db, [])
    with pytest.raises(ValueError):
        run_import(db, prov, zone_code="xxx1", import_date=D1)


def test_importa_la_muestra_real_de_mercadona(db):
    prov = make_fake_mercadona()
    ensure_supermarket(db, prov)
    ensure_zone(db, "mercadona", "vlc1")
    db.commit()
    [res] = run_import(db, prov, import_date=D1)
    assert res.products == 4
    row = db.execute(
        "select pp.price, pp.unit_price, pp.unit, pp.previous_price, pp.on_promotion "
        "from price_periods pp join products p on p.id = pp.product_id "
        "where p.external_id = '10381'"
    ).fetchone()
    assert row == {"price": Decimal("4.98"), "unit_price": Decimal("0.83"), "unit": "l",
                   "previous_price": Decimal("5.04"), "on_promotion": True}

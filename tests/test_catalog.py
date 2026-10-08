import pytest
from conftest import FakeProvider

from comparador.catalog import active_zones, add_province


def count(db, table):
    return db.execute(f"select count(*) as n from {table}").fetchone()["n"]


def test_provincias_que_comparten_zona(db):
    prov = FakeProvider(zones={"03001": "alc1", "30001": "alc1"})
    assert add_province(db, [prov], "alicante", "Alicante", "03001") == {"mercadona": "alc1"}
    add_province(db, [prov], "murcia", "Murcia", "30001")
    assert count(db, "price_zones") == 1
    assert count(db, "province_zones") == 2


def test_volver_a_anadir_actualiza(db):
    prov = FakeProvider(zones={"08001": "bcn1", "08820": "bcn2"})
    add_province(db, [prov], "barcelona", "Barcelona", "08001")
    add_province(db, [prov], "barcelona", "Barcelona y El Prat", "08820")
    row = db.execute(
        "select p.name, p.postal_code, z.code from provinces p "
        "join province_zones pz on pz.province_id = p.id "
        "join price_zones z on z.id = pz.zone_id"
    ).fetchone()
    assert row == {"name": "Barcelona y El Prat", "postal_code": "08820", "code": "bcn2"}


def test_provincia_sin_servicio(db):
    assert add_province(db, [FakeProvider()], "malaga", "Málaga", "29001") == {"mercadona": None}
    assert count(db, "provinces") == 1
    assert count(db, "province_zones") == 0


@pytest.mark.parametrize("province_id, postal_code", [
    ("Barcelona", "08001"), ("bar celona", "08001"), ("barcelona", "8001"), ("barcelona", "0800A"),
])
def test_valida_id_y_codigo_postal(db, province_id, postal_code):
    with pytest.raises(ValueError):
        add_province(db, [FakeProvider(zones={postal_code: "bcn1"})], province_id, "X", postal_code)
    assert count(db, "provinces") == 0


def test_zonas_activas(db):
    prov = FakeProvider(zones={"08001": "bcn1", "28001": "mad3"})
    add_province(db, [prov], "madrid", "Madrid", "28001")
    add_province(db, [prov], "barcelona", "Barcelona", "08001")
    assert [z["code"] for z in active_zones(db, "mercadona")] == ["bcn1", "mad3"]

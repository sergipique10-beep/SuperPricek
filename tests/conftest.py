import json
import os
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg
import pytest
from dotenv import dotenv_values

from comparador.db import connect
from comparador.models import PriceObservation, Product, ScrapedItem
from comparador.providers.base import Provider, ZoneNotServed
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


# ---------- Postgres de tests ----------

ROOT = Path(__file__).parent.parent
MIGRATIONS = ROOT / "supabase" / "migrations"
TABLES = ("supermarkets, provinces, price_zones, province_zones, "
          "products, price_periods, import_runs")


def assert_local(url: str) -> None:
    """Los tests borran el esquema: jamás contra Supabase."""
    if urlsplit(url).hostname not in ("localhost", "127.0.0.1"):
        raise RuntimeError("TEST_DATABASE_URL debe apuntar a localhost (Docker), nunca a Supabase")


@pytest.fixture(scope="session")
def db_url() -> str:
    # Solo se lee TEST_DATABASE_URL del .env: DATABASE_URL (Supabase) no entra en los tests
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Sin TEST_DATABASE_URL: arranca Postgres con `docker compose up -d`")
    assert_local(url)
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("drop schema if exists public cascade")
            conn.execute("create schema public")
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                conn.execute(migration.read_text(encoding="utf-8"))
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres de tests no disponible ({exc}). ¿`docker compose up -d`?")
    return url


@pytest.fixture
def db(db_url):
    conn = connect(db_url)
    conn.execute(f"truncate {TABLES} restart identity cascade")
    conn.commit()
    yield conn
    conn.close()


# ---------- providers y productos de prueba ----------

D1 = date(2026, 10, 8)
D2 = D1 + timedelta(days=1)


class FakeProvider(Provider):
    """Provider en memoria: zona por código postal y catálogo por zona."""

    name = "mercadona"
    display_name = "Mercadona"

    def __init__(self, zones=None, catalogs=None, failing=()):
        self.zones = zones or {}
        self.catalogs = catalogs or {}
        self.failing = set(failing)

    def zone_for_postal_code(self, postal_code):
        if postal_code not in self.zones:
            raise ZoneNotServed(f"Sin servicio en {postal_code}")
        return self.zones[postal_code]

    def fetch_all(self, zone, limit_categories=None):
        if zone in self.failing:
            raise RuntimeError(f"Fallo simulado en {zone}")
        yield from self.catalogs.get(zone, [])


def make_item(external_id, price, unit_price=None, unit="l", name=None):
    return ScrapedItem(
        product=Product(supermarket="mercadona", external_id=external_id,
                        name=name or f"Producto {external_id}"),
        price=PriceObservation(supermarket="mercadona", external_id=external_id,
                               price=price,
                               unit_price=price if unit_price is None else unit_price,
                               unit=unit),
    )


@pytest.fixture
def seeded(db):
    """Barcelona (bcn1) con dos días, Madrid (mad3) con uno, Málaga sin servicio."""
    from comparador.catalog import add_province
    from comparador.importer import run_import

    leche = "Leche semidesnatada Hacendado"
    prov = FakeProvider(zones={"08001": "bcn1", "28001": "mad3"}, catalogs={
        "bcn1": [make_item("1", 0.83, name=leche),
                 make_item("2", 4.98, unit_price=0.83, name=f"{leche} pack-6"),
                 make_item("3", 3.25, unit_price=0.20, unit="ud", name="Café con leche en cápsula"),
                 make_item("4", 0.96, name="Leche entera Hacendado"),
                 make_item("5", 1.50, unit_price=0.60, unit="100ml", name="Leche facial limpiadora"),
                 make_item("6", 2.00, unit="kg", name="Harina 50% integral")],
        "mad3": [make_item("1", 0.89, name=leche)],
    })
    for pid, name, pc in [("barcelona", "Barcelona", "08001"), ("madrid", "Madrid", "28001"),
                          ("malaga", "Málaga", "29001")]:
        add_province(db, [prov], pid, name, pc)
    run_import(db, prov, import_date=D1)
    # Día 2 en Barcelona: la leche entera (4) deja de venderse y la semi (1) sube
    prov.catalogs["bcn1"] = [make_item("1", 0.85, name=leche)] + [
        i for i in prov.catalogs["bcn1"] if i.product.external_id not in ("1", "4")]
    run_import(db, prov, zone_code="bcn1", import_date=D2)
    return prov

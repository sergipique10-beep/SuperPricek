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

# Supabase y precios por provincia: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sustituir SQLite por Postgres (Supabase), guardar el histórico como periodos de precio por zona y servir precios por provincia en CLI y API.

**Architecture:** Provider (Mercadona) → `importer.save_snapshot` escribe por conjuntos (COPY + SQL) en Postgres → `queries` lee el precio actual por provincia → `api` (FastAPI con pool) y `cli`. Las provincias apuntan a zonas de precio (almacenes); el histórico son periodos `valid_from`→`last_seen`.

**Tech Stack:** Python ≥3.11, psycopg 3 + psycopg-pool, FastAPI, httpx, Postgres 17 (Supabase en producción, Docker en tests), GitHub Actions, Render.

**Spec:** `docs/superpowers/specs/2026-10-08-supabase-multi-provincia-design.md`

## Global Constraints

- Python ≥3.11 (CI usa 3.12; en local hay 3.14). Postgres 17.
- Dinero: `numeric(10,2)`; precio por unidad: `numeric(12,4)`. Nunca `float` en la base de datos.
- Toda conexión usa `prepare_threshold=None` (pooler de Supabase en modo transaction).
- Fecha de importación = fecha actual en `Europe/Madrid` (requiere el paquete `tzdata` en Windows).
- Los tests nunca hacen peticiones a Mercadona ni tocan Supabase: `TEST_DATABASE_URL` solo puede apuntar a `localhost`/`127.0.0.1`.
- Respeto a Mercadona: `delay` por defecto 1.0 s; sin importaciones completas repetidas.
- Secretos solo en `.env` (ignorado por git), secrets de GitHub y variables de Render.
- Textos, comentarios y mensajes en español, como el resto del código.
- Comandos en Windows: `.venv/Scripts/python -m pytest ...` desde la raíz del repo.

## Review Focus

- `TEST_DATABASE_URL` apuntando a Supabase borraría producción (los tests hacen `drop schema public cascade`) → los tests se niegan a ejecutarse fuera de localhost (Task 2).
- Mercadona devuelve 0 productos (cambio de API o bloqueo) → la zona se marca como error y no "desaparece" todo el catálogo (Task 4).
- La última importación de una zona falla → la API sigue mostrando los precios del último día correcto (Task 5).
- Búsquedas con `%` o `_` → se tratan como texto literal, no como comodines (Task 5).
- Provincia sin servicio de una cadena (Málaga) → búsqueda vacía y provincia listada sin zona, sin errores (Task 5).

## Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `supabase/migrations/20261008120000_init.sql` | Esquema completo (crear) |
| `src/comparador/db.py` | `database_url()`, `connect()`, `today()` (crear) |
| `src/comparador/catalog.py` | Supermercados, zonas y provincias: escritura (crear) |
| `src/comparador/importer.py` | `save_snapshot`, `run_import` por zonas (reescribir) |
| `src/comparador/queries.py` | Consultas de solo lectura por provincia (crear) |
| `src/comparador/api.py` | FastAPI con pool y `province` (reescribir) |
| `src/comparador/cli.py` | Comandos `province`, `import`, `search`, `serve` (reescribir) |
| `src/comparador/providers/base.py` | Contrato con zonas + `ZoneNotServed` (modificar) |
| `src/comparador/providers/mercadona.py` | Zona como parámetro (modificar) |
| `src/comparador/models.py` | `region` → `zone` (modificar) |
| `src/comparador/storage.py`, `schema.sql` | Eliminar (Task 7) |
| `tests/conftest.py` | Fixtures de BD, `FakeProvider`, `make_item`, `seeded` |
| `tests/test_db.py`, `test_catalog.py`, `test_importer.py`, `test_queries.py`, `test_api.py`, `test_cli.py` | Tests nuevos |
| `docker-compose.yml`, `.env.example`, `render.yaml` | Infraestructura (crear) |
| `.github/workflows/ci.yml`, `import-diario.yml` | CI e importación diaria (modificar) |

---

### Task 1: Contrato de provider con zonas

**Files:**
- Modify: `src/comparador/models.py`, `src/comparador/providers/base.py`, `src/comparador/providers/mercadona.py`
- Modify (adaptación temporal, se eliminan en Tasks 4-7): `src/comparador/storage.py`, `src/comparador/importer.py`, `src/comparador/cli.py`, `tests/test_storage_api.py`
- Test: `tests/test_mercadona.py`, `tests/conftest.py`

**Interfaces:**
- Produces: `ZoneNotServed(Exception)` en `providers/base.py`; `Provider.zone_for_postal_code(postal_code: str) -> str`; `Provider.fetch_all(zone: str, limit_categories: int | None = None) -> Iterator[ScrapedItem]`; `MercadonaProvider(delay: float = 1.0, client: httpx.Client | None = None)`; `parse_product(raw, category=None, zone=None)`; `PriceObservation.zone: str | None`.

- [ ] **Step 1: Actualizar los tests de Mercadona**

En `tests/conftest.py`, en `make_fake_mercadona`, cambiar la última línea por:

```python
    return MercadonaProvider(delay=0, client=client)
```

En `tests/test_mercadona.py`: cambiar el import a
`from comparador.providers.base import ZoneNotServed` + `from comparador.providers.mercadona import BASE_URL, MercadonaProvider, parse_product`;
sustituir `fake_mercadona.fetch_all()` por `fake_mercadona.fetch_all("vlc1")`, `make_fake_mercadona(data).fetch_all()` por `make_fake_mercadona(data).fetch_all("vlc1")`, `pack.price.region` por `pack.price.zone`; y reemplazar `test_almacen_desde_codigo_postal` por:

```python
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
```

- [ ] **Step 2: Ejecutar y ver que fallan**

Run: `.venv/Scripts/python -m pytest tests/test_mercadona.py -q`
Expected: FAIL (`fetch_all() takes 1 positional argument`, `has no attribute 'zone_for_postal_code'`).

- [ ] **Step 3: Implementar**

`src/comparador/models.py`, en `PriceObservation`, sustituir la línea de `region` por:

```python
    zone: str | None = None            # zona de precio (almacén en Mercadona)
```

`src/comparador/providers/base.py` completo:

```python
"""Contrato que cumple cada supermercado."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..models import ScrapedItem


class ZoneNotServed(Exception):
    """La cadena no tiene tienda online en ese código postal."""


class Provider(ABC):
    #: identificador corto, p. ej. "mercadona"
    name: str
    #: nombre para mostrar
    display_name: str

    @abstractmethod
    def zone_for_postal_code(self, postal_code: str) -> str:
        """Zona de precio que corresponde a un código postal.

        Lanza `ZoneNotServed` si la cadena no da servicio allí."""

    @abstractmethod
    def fetch_all(self, zone: str, limit_categories: int | None = None) -> Iterator[ScrapedItem]:
        """Recorre el catálogo de una zona y devuelve cada producto con su precio.

        `limit_categories` permite hacer pruebas rápidas sin bajar todo.
        """
```

`src/comparador/providers/mercadona.py`:
- importar `from .base import Provider, ZoneNotServed`;
- constructor sin `warehouse` ni `postal_code`:

```python
    def __init__(self, delay: float = 1.0, client: httpx.Client | None = None):
        self.delay = delay  # segundos entre peticiones, para no sobrecargar
        self.client = client or httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=20,
        )
```

- `warehouse_for` se renombra y gestiona el 404:

```python
    def zone_for_postal_code(self, postal_code: str) -> str:
        """Almacén que Mercadona asigna a un código postal (p. ej. 08820 -> bcn1).

        Es lo mismo que hace la web al escribir el código postal: el almacén
        llega en la cabecera `x-customer-wh`. Fuera de zona responde 404."""
        if not (len(postal_code) == 5 and postal_code.isdigit()):
            raise ValueError(f"Código postal no válido: {postal_code!r}")
        resp = self.client.put(
            "/postal-codes/actions/change-pc/", json={"new_postal_code": postal_code}
        )
        if resp.status_code == 404:
            raise ZoneNotServed(f"Mercadona no tiene tienda online en {postal_code}")
        resp.raise_for_status()
        warehouse = resp.headers.get("x-customer-wh")
        if not warehouse:
            raise RuntimeError(f"Mercadona no devolvió almacén para {postal_code}")
        log.info("Código postal %s -> almacén %s", postal_code, warehouse)
        return warehouse
```

- `_get(self, path: str, zone: str, retries: int = 3)` con `params = {"lang": "es", "wh": zone}`;
- `list_categories(self, zone: str)` llama a `self._get("/categories/", zone)`;
- `fetch_all(self, zone: str, limit_categories: int | None = None)`: `categories = self.list_categories(zone)`, el log usa `zone`, `data = self._get(f"/categories/{cat['id']}/", zone)` y `parse_product(raw, category=path, zone=zone)`;
- `parse_product(raw, category=None, zone=None)` y en `PriceObservation(...)` `zone=zone`.

Adaptación temporal del código SQLite (desaparece en Tasks 4-7):
- `storage.py`: `obs.region or ""` → `obs.zone or ""`.
- `importer.py`: firma `def run_import(provider, storage, zone, limit_categories=None, dry_run=False)` y `provider.fetch_all(zone, limit_categories=limit_categories)`.
- `cli.py`, rama `import`:

```python
        provider = get_provider(args.supermarket, delay=args.delay)
        zone = (provider.zone_for_postal_code(args.postal_code)
                if args.postal_code else args.warehouse)
        storage = None if args.dry_run else Storage(args.db)
        result = run_import(provider, storage, zone, args.limit_categories, args.dry_run)
```

- `tests/test_storage_api.py`: `run_import(fake_mercadona, storage)` → `run_import(fake_mercadona, storage, "vlc1")` (todas), `run_import(fake_mercadona, None, dry_run=True)` → `run_import(fake_mercadona, None, "vlc1", dry_run=True)`, `next(fake_mercadona.fetch_all())` → `next(fake_mercadona.fetch_all("vlc1"))`.

- [ ] **Step 4: Ejecutar todos los tests**

Run: `.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Provider: zona como parámetro y ZoneNotServed para CP sin servicio"
```

---

### Task 2: Postgres: dependencias, migración, conexión y fixtures

**Files:**
- Create: `supabase/migrations/20261008120000_init.sql`, `src/comparador/db.py`, `docker-compose.yml`, `.env.example`, `tests/test_db.py`
- Modify: `pyproject.toml`, `tests/conftest.py`, `.github/workflows/ci.yml`, `.env` (añadir una línea)

**Interfaces:**
- Produces: `db.database_url() -> str`, `db.connect(url: str | None = None) -> psycopg.Connection` (filas `dict`), `db.today() -> date`; fixtures `db_url` (sesión, str) y `db` (conexión limpia por test); `conftest.assert_local(url)`.

- [ ] **Step 1: Dependencias y Postgres local**

`pyproject.toml`, `dependencies`:

```toml
dependencies = [
    "httpx>=0.27",
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "psycopg[binary]>=3.2",
    "psycopg-pool>=3.2",
    "python-dotenv>=1.0",
    "tzdata>=2024.1",
]
```

`docker-compose.yml`:

```yaml
# Postgres local para los tests (misma versión que Supabase).
services:
  db:
    image: postgres:17
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: comparador_test
    ports:
      - "54329:5432"
```

`.env.example`:

```
# Copia este archivo como .env y rellénalo. .env no se sube a git.
# Supabase → Connect → Direct → Session pooler (puerto 5432)
DATABASE_URL=postgresql://postgres.<ref>:<contraseña>@aws-1-eu-central-1.pooler.supabase.com:5432/postgres
# Postgres de Docker para los tests (docker compose up -d)
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:54329/comparador_test
```

Añadir al final de `.env` (sin tocar la línea existente):

```
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:54329/comparador_test
```

Run: `.venv/Scripts/python -m pip install -q -e ".[dev]" && docker compose up -d`
Expected: contenedor `db` arrancado.

- [ ] **Step 2: Test que falla**

`tests/test_db.py`:

```python
import pytest
from conftest import assert_local

from comparador.db import today

TABLES = ["import_runs", "price_periods", "price_zones", "products",
          "province_zones", "provinces", "supermarkets"]


def test_migracion_crea_tablas_con_rls(db):
    rows = db.execute(
        "select relname, relrowsecurity from pg_class "
        "where relnamespace = 'public'::regnamespace and relkind = 'r' order by relname"
    ).fetchall()
    assert {r["relname"]: r["relrowsecurity"] for r in rows} == {t: True for t in TABLES}


def test_today_devuelve_fecha():
    assert today().year >= 2026


@pytest.mark.parametrize("url", [
    "postgresql://postgres.abc:x@aws-1-eu-central-1.pooler.supabase.com:5432/postgres",
    "postgresql://postgres:x@db.abc.supabase.co:5432/postgres",
])
def test_tests_se_niegan_a_usar_bases_remotas(url):
    with pytest.raises(RuntimeError):
        assert_local(url)
```

Añadir a `tests/conftest.py` (imports arriba, resto al final):

```python
import os
from urllib.parse import urlsplit

import psycopg
from dotenv import dotenv_values

from comparador.db import connect

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
```

Run: `.venv/Scripts/python -m pytest tests/test_db.py -q`
Expected: FAIL (`ModuleNotFoundError: comparador.db`).

- [ ] **Step 3: Implementar `db.py` y la migración**

`src/comparador/db.py`:

```python
"""Conexión a Postgres: Supabase en producción, Docker en los tests."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

MADRID = ZoneInfo("Europe/Madrid")


def database_url() -> str:
    """DATABASE_URL del entorno o, en local, del archivo .env."""
    load_dotenv(Path.cwd() / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Falta DATABASE_URL: ponla en .env o en el entorno")
    return url


def connect(url: str | None = None) -> psycopg.Connection:
    """Conexión con filas como dict.

    prepare_threshold=None: el pooler de Supabase en modo transaction no
    admite sentencias preparadas."""
    return psycopg.connect(url or database_url(), row_factory=dict_row,
                           prepare_threshold=None)


def today() -> date:
    """Fecha de importación: el día en España, no en UTC."""
    return datetime.now(MADRID).date()
```

`supabase/migrations/20261008120000_init.sql`: copiar literalmente el bloque SQL de la sección 1 de la spec (desde `create schema if not exists extensions;` hasta el último `alter table ... enable row level security;`).

- [ ] **Step 4: Ejecutar**

Run: `.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS (17 tests). Si `test_migracion_crea_tablas_con_rls` falla por varias sentencias en un `execute`, ejecutar el archivo partiéndolo por `;\n` (ninguna sentencia de la migración contiene `;` interno).

- [ ] **Step 5: CI con Postgres**

`.github/workflows/ci.yml`, sustituir `jobs:` por:

```yaml
jobs:
  tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: comparador_test
        ports:
          - 54329:5432
        options: >-
          --health-cmd pg_isready --health-interval 5s
          --health-timeout 5s --health-retries 10
    env:
      TEST_DATABASE_URL: postgresql://postgres:postgres@localhost:54329/comparador_test
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "Postgres: migración inicial, conexión y fixtures de tests con Docker"
```

---

### Task 3: Catálogo: supermercados, zonas y provincias

**Files:**
- Create: `src/comparador/catalog.py`, `tests/test_catalog.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: `Provider`, `ZoneNotServed` (Task 1); fixture `db` (Task 2).
- Produces: `ensure_supermarket(conn, provider) -> None`; `ensure_zone(conn, supermarket: str, code: str) -> int`; `add_province(conn, providers: list[Provider], province_id: str, name: str, postal_code: str) -> dict[str, str | None]` (hace commit); `active_zones(conn, supermarket: str) -> list[dict]` con claves `id`, `code`. En conftest: `FakeProvider(zones=None, catalogs=None, failing=())`, `make_item(...)`, `D1`, `D2`.

- [ ] **Step 1: Helpers de test**

Añadir a `tests/conftest.py`:

```python
from datetime import date, timedelta

from comparador.models import PriceObservation, Product, ScrapedItem
from comparador.providers.base import Provider, ZoneNotServed

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
```

- [ ] **Step 2: Tests que fallan**

`tests/test_catalog.py`:

```python
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
```

Run: `.venv/Scripts/python -m pytest tests/test_catalog.py -q`
Expected: FAIL (`ModuleNotFoundError: comparador.catalog`).

- [ ] **Step 3: Implementar**

`src/comparador/catalog.py`:

```python
"""Supermercados, zonas de precio y provincias.

Una provincia usa, en cada cadena, la zona de precio que corresponde al
código postal de su capital. Varias provincias pueden compartir zona."""

from __future__ import annotations

import logging
import re

import psycopg

from .providers.base import Provider, ZoneNotServed

log = logging.getLogger(__name__)

PROVINCE_ID = re.compile(r"^[a-z0-9-]+$")


def ensure_supermarket(conn: psycopg.Connection, provider: Provider) -> None:
    conn.execute(
        "insert into supermarkets (id, display_name) values (%s, %s) "
        "on conflict (id) do update set display_name = excluded.display_name",
        (provider.name, provider.display_name),
    )


def ensure_zone(conn: psycopg.Connection, supermarket: str, code: str) -> int:
    row = conn.execute(
        "insert into price_zones (supermarket, code) values (%s, %s) "
        "on conflict (supermarket, code) do update set code = excluded.code "
        "returning id",
        (supermarket, code),
    ).fetchone()
    return row["id"]


def add_province(conn: psycopg.Connection, providers: list[Provider],
                 province_id: str, name: str, postal_code: str) -> dict[str, str | None]:
    """Crea o actualiza la provincia y la enlaza con la zona de cada cadena.

    Devuelve {supermercado: código de zona, o None si no hay servicio}."""
    if not PROVINCE_ID.match(province_id):
        raise ValueError(f"Id de provincia no válido: {province_id!r} (minúsculas, sin espacios)")
    if not (len(postal_code) == 5 and postal_code.isdigit()):
        raise ValueError(f"Código postal no válido: {postal_code!r}")

    conn.execute(
        "insert into provinces (id, name, postal_code) values (%s, %s, %s) "
        "on conflict (id) do update set name = excluded.name, postal_code = excluded.postal_code",
        (province_id, name, postal_code),
    )
    result: dict[str, str | None] = {}
    for provider in providers:
        ensure_supermarket(conn, provider)
        try:
            code = provider.zone_for_postal_code(postal_code)
        except ZoneNotServed as exc:
            log.warning("%s: %s", provider.name, exc)
            conn.execute(
                "delete from province_zones where province_id = %s and supermarket = %s",
                (province_id, provider.name),
            )
            result[provider.name] = None
            continue
        zone_id = ensure_zone(conn, provider.name, code)
        conn.execute(
            "insert into province_zones (province_id, supermarket, zone_id) values (%s, %s, %s) "
            "on conflict (province_id, supermarket) do update set zone_id = excluded.zone_id",
            (province_id, provider.name, zone_id),
        )
        result[provider.name] = code
    conn.commit()
    return result


def active_zones(conn: psycopg.Connection, supermarket: str) -> list[dict]:
    return conn.execute(
        "select id, code from price_zones where supermarket = %s and active order by code",
        (supermarket,),
    ).fetchall()
```

- [ ] **Step 4: Ejecutar**

Run: `.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Catálogo: provincias enlazadas a zonas de precio"
```

---

### Task 4: Importador por periodos de precio

**Files:**
- Rewrite: `src/comparador/importer.py`
- Create: `tests/test_importer.py`
- Delete: `tests/test_storage_api.py` (su cobertura pasa a Tasks 4-6)

**Interfaces:**
- Consumes: `active_zones`, `ensure_supermarket`, `ensure_zone`, `add_province` (Task 3); `db.today()` (Task 2).
- Produces: `ZoneResult(zone: str, products: int, error: str | None = None)`; `save_snapshot(conn, zone_id: int, items: list[ScrapedItem], import_date: date) -> None` (no hace commit); `run_import(conn, provider, zone_code: str | None = None, import_date: date | None = None) -> list[ZoneResult]`.

Nota: entre esta tarea y la Task 7, `cli.py` todavía usa el importador antiguo y no funciona; nadie lo usa mientras tanto.

- [ ] **Step 1: Tests que fallan**

`tests/test_importer.py`:

```python
from datetime import timedelta
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
```

Run: `git rm -q tests/test_storage_api.py && .venv/Scripts/python -m pytest tests/test_importer.py -q`
Expected: FAIL (`cannot import name 'run_import'` con la firma nueva / errores de tipo).

- [ ] **Step 2: Implementar**

`src/comparador/importer.py` completo:

```python
"""Une un provider con la base de datos. Es lo que ejecuta el cron diario.

El histórico se guarda como periodos de precio: si el precio de un producto
no cambia, solo se alarga `last_seen` del periodo actual."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import psycopg

from .catalog import active_zones, ensure_supermarket
from .db import today
from .models import ScrapedItem
from .providers.base import Provider

log = logging.getLogger(__name__)

_STAGING_COLUMNS = (
    "supermarket, external_id, name, brand, category, packaging, size, size_unit, "
    "ean, image_url, url, price, unit_price, unit, previous_price, on_promotion"
)

_UPSERT_PRODUCTS = """
insert into products (supermarket, external_id, name, brand, category, packaging,
                      size, size_unit, ean, image_url, url)
select supermarket, external_id, name, brand, category, packaging,
       size, size_unit, ean, image_url, url
from staging
on conflict (supermarket, external_id) do update set
    name = excluded.name, brand = coalesce(excluded.brand, products.brand),
    category = excluded.category, packaging = excluded.packaging,
    size = excluded.size, size_unit = excluded.size_unit,
    ean = coalesce(excluded.ean, products.ean),
    image_url = excluded.image_url, url = excluded.url, last_seen = now()
"""

# Compara cada precio de hoy con el último periodo del producto en la zona:
# igual → alarga; distinto pero abierto hoy → sobrescribe; si no → periodo nuevo.
_UPDATE_PERIODS = """
with obs as (
    select p.id as product_id, s.price, s.unit_price, s.unit, s.previous_price,
           s.on_promotion, lp.id as period_id, lp.valid_from,
           (lp.id is not null
            and lp.price = s.price
            and lp.unit_price is not distinct from s.unit_price
            and lp.unit is not distinct from s.unit
            and lp.previous_price is not distinct from s.previous_price
            and lp.on_promotion = s.on_promotion) as same
    from staging s
    join products p on p.supermarket = s.supermarket and p.external_id = s.external_id
    left join lateral (
        select id, price, unit_price, unit, previous_price, on_promotion, valid_from
        from price_periods pp
        where pp.product_id = p.id and pp.zone_id = %(zone)s
        order by pp.valid_from desc
        limit 1
    ) lp on true
),
extended as (
    update price_periods pp set last_seen = %(today)s
    from obs where obs.period_id = pp.id and obs.same
    returning pp.id
),
overwritten as (
    update price_periods pp set
        price = obs.price, unit_price = obs.unit_price, unit = obs.unit,
        previous_price = obs.previous_price, on_promotion = obs.on_promotion,
        last_seen = %(today)s
    from obs
    where obs.period_id = pp.id and not obs.same and obs.valid_from = %(today)s
    returning pp.id
)
insert into price_periods (product_id, zone_id, price, unit_price, unit,
                           previous_price, on_promotion, valid_from, last_seen)
select product_id, %(zone)s, price, unit_price, unit, previous_price, on_promotion,
       %(today)s, %(today)s
from obs
where not obs.same and (obs.period_id is null or obs.valid_from <> %(today)s)
"""


@dataclass
class ZoneResult:
    zone: str
    products: int
    error: str | None = None


def save_snapshot(conn: psycopg.Connection, zone_id: int,
                  items: list[ScrapedItem], import_date: date) -> None:
    """Guarda los productos y los periodos de precio de una zona. No hace commit."""
    with conn.cursor() as cur:
        cur.execute(f"""
            create temp table staging (
                supermarket text, external_id text, name text, brand text,
                category text, packaging text, size numeric, size_unit text,
                ean text, image_url text, url text,
                price numeric(10,2), unit_price numeric(12,4), unit text,
                previous_price numeric(10,2), on_promotion boolean
            ) on commit drop""")
        with cur.copy(f"copy staging ({_STAGING_COLUMNS}) from stdin") as copy:
            for item in items:
                p, o = item.product, item.price
                copy.write_row((p.supermarket, p.external_id, p.name, p.brand, p.category,
                                p.packaging, p.size, p.size_unit, p.ean, p.image_url, p.url,
                                o.price, o.unit_price, o.unit, o.previous_price,
                                o.on_promotion))
        cur.execute(_UPSERT_PRODUCTS)
        cur.execute(_UPDATE_PERIODS, {"zone": zone_id, "today": import_date})


def run_import(conn: psycopg.Connection, provider: Provider,
               zone_code: str | None = None,
               import_date: date | None = None) -> list[ZoneResult]:
    """Importa todas las zonas activas de la cadena (o solo `zone_code`).

    Cada zona va en su propia transacción: si una falla, las demás siguen."""
    import_date = import_date or today()
    ensure_supermarket(conn, provider)
    conn.commit()

    zones = active_zones(conn, provider.name)
    if zone_code:
        zones = [z for z in zones if z["code"] == zone_code]
        if not zones:
            raise ValueError(f"Zona desconocida o inactiva: {zone_code}. "
                             "Añade una provincia con `comparador province add`.")

    results = []
    for zone in zones:
        run_id = conn.execute(
            "insert into import_runs (zone_id, import_date) values (%s, %s) returning id",
            (zone["id"], import_date),
        ).fetchone()["id"]
        conn.commit()
        try:
            items = list(provider.fetch_all(zone["code"]))
            if not items:
                raise RuntimeError(f"{provider.name} devolvió 0 productos en {zone['code']}")
            save_snapshot(conn, zone["id"], items, import_date)
            conn.execute(
                "update import_runs set status = 'ok', products = %s, finished_at = now() "
                "where id = %s",
                (len(items), run_id),
            )
            conn.commit()
            log.info("%s/%s: %d productos", provider.name, zone["code"], len(items))
            results.append(ZoneResult(zone["code"], len(items)))
        except Exception as exc:
            conn.rollback()
            log.exception("Fallo importando %s/%s", provider.name, zone["code"])
            conn.execute(
                "update import_runs set status = 'error', error = %s, finished_at = now() "
                "where id = %s",
                (str(exc)[:1000], run_id),
            )
            conn.commit()
            results.append(ZoneResult(zone["code"], 0, str(exc)))
    return results
```

- [ ] **Step 3: Ejecutar**

Run: `.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "Importador: histórico por periodos de precio, una transacción por zona"
```

---

### Task 5: Consultas por provincia

**Files:**
- Create: `src/comparador/queries.py`, `tests/test_queries.py`
- Modify: `tests/conftest.py` (fixture `seeded`)

**Interfaces:**
- Consumes: `add_province` (Task 3), `run_import` (Task 4).
- Produces: `province_exists(conn, province_id) -> bool`; `product_exists(conn, product_id) -> bool`; `provinces(conn) -> list[dict]` (`id`, `name`, `postal_code`, `supermarkets: [{supermarket, zone, last_import}]`); `supermarkets(conn) -> list[dict]` (`id`, `display_name`, `products`); `search(conn, province, query, supermarket=None, sort="unit_price", limit=50) -> list[dict]`; `get_product(conn, province, product_id) -> dict | None`; `price_history(conn, province, product_id) -> list[dict]`. Las filas de producto incluyen `zone`, `valid_from` y `last_seen`.

- [ ] **Step 1: Fixture con datos**

Añadir a `tests/conftest.py`:

```python
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
```

- [ ] **Step 2: Tests que fallan**

`tests/test_queries.py`:

```python
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
```

Run: `.venv/Scripts/python -m pytest tests/test_queries.py -q`
Expected: FAIL (`cannot import name 'queries'`).

- [ ] **Step 3: Implementar**

`src/comparador/queries.py`:

```python
"""Consultas de solo lectura, para la API y la CLI.

Precio actual en una provincia: el periodo cuya `last_seen` coincide con la
última importación correcta de la zona de esa provincia. Si un producto no
apareció en esa importación, ya no está a la venta allí."""

from __future__ import annotations

import psycopg

_CURRENT = """
with zones as (
    select pz.zone_id, z.code as zone,
           (select max(r.import_date) from import_runs r
            where r.zone_id = pz.zone_id and r.status = 'ok') as import_date
    from province_zones pz
    join price_zones z on z.id = pz.zone_id
    where pz.province_id = %(province)s
)
select distinct on (p.id)
       p.id, p.supermarket, p.external_id, p.name, p.brand, p.category,
       p.packaging, p.size, p.size_unit, p.ean, p.image_url, p.url,
       pp.price, pp.unit_price, pp.unit, pp.previous_price, pp.on_promotion,
       pp.valid_from, pp.last_seen, zones.zone
from zones
join price_periods pp on pp.zone_id = zones.zone_id and pp.last_seen = zones.import_date
join products p on p.id = pp.product_id
where {where}
order by p.id, pp.valid_from desc
"""

# unit_price: primero la unidad con más resultados (0,20 €/ud y 0,81 €/l no
# son comparables); dentro de cada unidad, el más barato.
_SORTS = {
    "unit_price": "(unit is null), unit_count desc, unit, unit_price, price, name",
    "price": "price, name",
}


def _like(word: str) -> str:
    """Patrón LIKE con % y _ escapados: se buscan como texto literal."""
    escaped = word.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def province_exists(conn: psycopg.Connection, province_id: str) -> bool:
    return conn.execute("select 1 from provinces where id = %s",
                        (province_id,)).fetchone() is not None


def product_exists(conn: psycopg.Connection, product_id: int) -> bool:
    return conn.execute("select 1 from products where id = %s",
                        (product_id,)).fetchone() is not None


def search(conn: psycopg.Connection, province: str, query: str,
           supermarket: str | None = None, sort: str = "unit_price",
           limit: int = 50) -> list[dict]:
    params: dict = {"province": province, "limit": limit}
    conditions = ["true"]
    for i, word in enumerate(query.split()):
        conditions.append(f"lower(p.name) like %(w{i})s")
        params[f"w{i}"] = _like(word)
    if supermarket:
        conditions.append("p.supermarket = %(supermarket)s")
        params["supermarket"] = supermarket
    sql = f"""
        select * from (
            select c.*, count(*) over (partition by c.unit) as unit_count
            from ({_CURRENT.format(where=" and ".join(conditions))}) c
        ) r
        order by {_SORTS[sort]}
        limit %(limit)s
    """
    rows = conn.execute(sql, params).fetchall()
    for row in rows:
        del row["unit_count"]
    return rows


def get_product(conn: psycopg.Connection, province: str, product_id: int) -> dict | None:
    return conn.execute(_CURRENT.format(where="p.id = %(id)s"),
                        {"province": province, "id": product_id}).fetchone()


def price_history(conn: psycopg.Connection, province: str, product_id: int) -> list[dict]:
    return conn.execute(
        """
        select pp.valid_from, pp.last_seen, pp.price, pp.unit_price, pp.unit,
               pp.previous_price, pp.on_promotion
        from price_periods pp
        join province_zones pz on pz.zone_id = pp.zone_id
        where pz.province_id = %s and pp.product_id = %s
        order by pp.valid_from
        """,
        (province, product_id),
    ).fetchall()


def provinces(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute(
        """
        select pr.id, pr.name, pr.postal_code, pz.supermarket, z.code as zone,
               (select max(r.finished_at) from import_runs r
                where r.zone_id = z.id and r.status = 'ok') as last_import
        from provinces pr
        left join province_zones pz on pz.province_id = pr.id
        left join price_zones z on z.id = pz.zone_id
        order by pr.name, pz.supermarket
        """
    ).fetchall()
    result: dict[str, dict] = {}
    for r in rows:
        prov = result.setdefault(r["id"], {"id": r["id"], "name": r["name"],
                                           "postal_code": r["postal_code"],
                                           "supermarkets": []})
        if r["supermarket"]:
            prov["supermarkets"].append({"supermarket": r["supermarket"], "zone": r["zone"],
                                         "last_import": r["last_import"]})
    return list(result.values())


def supermarkets(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        """
        select s.id, s.display_name, count(p.id) as products
        from supermarkets s left join products p on p.supermarket = s.id
        group by s.id order by s.id
        """
    ).fetchall()
```

- [ ] **Step 4: Ejecutar**

Run: `.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Consultas por provincia: precio actual, orden por unidad e historial"
```

---

### Task 6: API con provincia y pool de conexiones

**Files:**
- Rewrite: `src/comparador/api.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Consumes: `queries.*` (Task 5), `db.database_url()` (Task 2), fixtures `db_url` y `seeded`.
- Produces: `create_app(conninfo: str | None = None) -> FastAPI` (usado por `cli serve` y Render con `--factory`).

- [ ] **Step 1: Tests que fallan**

`tests/test_api.py`:

```python
import pytest
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
```

Run: `.venv/Scripts/python -m pytest tests/test_api.py -q`
Expected: FAIL (`create_app` todavía usa `Storage`).

- [ ] **Step 2: Implementar**

`src/comparador/api.py` completo:

```python
"""API pública. Solo lee de la base de datos: nunca hace scraping
durante una petición de usuario."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import __version__, queries
from .db import database_url


def create_app(conninfo: str | None = None) -> FastAPI:
    # prepare_threshold=None: el pooler de Supabase (transaction) no admite
    # sentencias preparadas
    pool = ConnectionPool(conninfo or database_url(), min_size=1, max_size=5, open=False,
                          kwargs={"row_factory": dict_row, "prepare_threshold": None})

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool.open(wait=True)
        yield
        pool.close()

    app = FastAPI(title="Comparador de precios de supermercados", version=__version__,
                  lifespan=lifespan)

    def get_conn() -> Iterator[psycopg.Connection]:
        with pool.connection() as conn:
            yield conn

    def check_province(conn: psycopg.Connection, province: str) -> None:
        if not queries.province_exists(conn, province):
            raise HTTPException(404, f"Provincia desconocida: {province}")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/provinces")
    def provinces(conn: psycopg.Connection = Depends(get_conn)):
        return queries.provinces(conn)

    @app.get("/api/v1/supermarkets")
    def supermarkets(conn: psycopg.Connection = Depends(get_conn)):
        return queries.supermarkets(conn)

    @app.get("/api/v1/products/search")
    def search(
        q: str = Query(..., min_length=2, description="Texto a buscar"),
        province: str = Query(..., description="Id de provincia, p. ej. barcelona"),
        supermarket: str | None = None,
        sort: Literal["unit_price", "price"] = "unit_price",
        limit: int = Query(50, ge=1, le=100),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        check_province(conn, province)
        return {"query": q, "province": province,
                "results": queries.search(conn, province, q, supermarket, sort, limit)}

    @app.get("/api/v1/products/{product_id}")
    def product(product_id: int, province: str = Query(...),
                conn: psycopg.Connection = Depends(get_conn)):
        check_province(conn, province)
        item = queries.get_product(conn, province, product_id)
        if not item:
            raise HTTPException(404, "Producto no encontrado o no disponible en esta provincia")
        return item

    @app.get("/api/v1/products/{product_id}/history")
    def history(product_id: int, province: str = Query(...),
                conn: psycopg.Connection = Depends(get_conn)):
        check_province(conn, province)
        if not queries.product_exists(conn, product_id):
            raise HTTPException(404, "Producto no encontrado")
        return {"product_id": product_id, "province": province,
                "history": queries.price_history(conn, province, product_id)}

    return app
```

- [ ] **Step 3: Ejecutar**

Run: `.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "API: endpoints por provincia con pool de conexiones"
```

---

### Task 7: CLI nueva y retirada de SQLite

**Files:**
- Rewrite: `src/comparador/cli.py`
- Create: `tests/test_cli.py`
- Delete: `src/comparador/storage.py`, `src/comparador/schema.sql`
- Modify: `pyproject.toml` (quitar `[tool.setuptools.package-data]`)

**Interfaces:**
- Consumes: `add_province` (Task 3), `run_import` (Task 4), `queries` (Task 5), `connect` (Task 2), `get_provider`/`PROVIDERS`.
- Produces: `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Tests que fallan**

`tests/test_cli.py`:

```python
import pytest
from conftest import FakeProvider, make_item

from comparador import cli
from comparador.cli import main


@pytest.fixture
def provider(db, db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    prov = FakeProvider(zones={"08001": "bcn1"}, catalogs={
        "bcn1": [make_item("1", 0.83, name="Leche semidesnatada Hacendado")]})
    monkeypatch.setattr(cli, "get_provider", lambda name, **kwargs: prov)
    return prov


def test_flujo_completo(provider, capsys):
    assert main(["province", "add", "barcelona", "Barcelona", "--postal-code", "08001"]) == 0
    assert "mercadona → bcn1" in capsys.readouterr().out
    assert main(["import", "mercadona"]) == 0
    assert "bcn1: 1 productos" in capsys.readouterr().out
    assert main(["search", "leche", "--province", "barcelona"]) == 0
    assert "Leche semidesnatada Hacendado" in capsys.readouterr().out
    assert main(["province", "list"]) == 0
    assert "bcn1" in capsys.readouterr().out


def test_importacion_con_error_devuelve_1(provider, capsys):
    main(["province", "add", "barcelona", "Barcelona", "--postal-code", "08001"])
    provider.failing = {"bcn1"}
    assert main(["import", "mercadona"]) == 1
    assert "ERROR" in capsys.readouterr().out


def test_sin_zonas_devuelve_1(provider, capsys):
    assert main(["import", "mercadona"]) == 1
    assert "province add" in capsys.readouterr().out


def test_dry_run_no_usa_la_base_de_datos(provider, capsys, monkeypatch):
    monkeypatch.setattr(cli, "connect", lambda *a, **k: pytest.fail("dry-run no debe conectar"))
    assert main(["import", "mercadona", "--zone", "bcn1", "--dry-run"]) == 0
    assert "Leche semidesnatada Hacendado" in capsys.readouterr().out


def test_codigo_postal_no_valido(provider, capsys):
    assert main(["province", "add", "barcelona", "Barcelona", "--postal-code", "8001"]) == 2
    assert "no válido" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["import", "mercadona", "--dry-run"],
    ["import", "mercadona", "--limit-categories", "2"],
    ["search", "leche"],
])
def test_argumentos_invalidos(argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
```

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -q`
Expected: FAIL.

- [ ] **Step 2: Implementar**

`src/comparador/cli.py` completo:

```python
"""Línea de comandos.

    comparador province add barcelona "Barcelona" --postal-code 08001
    comparador province list
    comparador import mercadona                    # todas las zonas activas
    comparador import mercadona --zone bcn1 --limit-categories 2 --dry-run
    comparador search "leche" --province barcelona
    comparador serve
"""

from __future__ import annotations

import argparse
import logging

from . import queries
from .catalog import add_province
from .db import connect
from .importer import run_import
from .providers import PROVIDERS, get_provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="comparador")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    prov = sub.add_parser("province", help="Gestionar provincias")
    prov_sub = prov.add_subparsers(dest="action", required=True)
    add = prov_sub.add_parser("add", help="Añadir o actualizar una provincia")
    add.add_argument("id", help="Identificador en minúsculas, p. ej. barcelona")
    add.add_argument("name", help="Nombre para mostrar")
    add.add_argument("--postal-code", required=True, help="Código postal de la capital")
    prov_sub.add_parser("list", help="Ver provincias, zonas y última importación")

    imp = sub.add_parser("import", help="Descargar precios de un supermercado")
    imp.add_argument("supermarket", choices=sorted(PROVIDERS))
    imp.add_argument("--zone", help="Solo esta zona (p. ej. bcn1); por defecto, todas las activas")
    imp.add_argument("--delay", type=float, default=1.0,
                     help="Segundos de espera entre peticiones")
    imp.add_argument("--dry-run", action="store_true",
                     help="Mostrar sin guardar (no necesita base de datos)")
    imp.add_argument("--limit-categories", type=int, default=None,
                     help="Solo las N primeras categorías (solo con --dry-run)")

    se = sub.add_parser("search", help="Buscar en los precios guardados")
    se.add_argument("query")
    se.add_argument("--province", required=True)
    se.add_argument("--sort", choices=["unit_price", "price"], default="unit_price")
    se.add_argument("--limit", type=int, default=20)

    sv = sub.add_parser("serve", help="Arrancar la API")
    sv.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    if args.command == "import":
        if args.dry_run and not args.zone:
            imp.error("--dry-run necesita --zone")
        if args.limit_categories and not args.dry_run:
            imp.error("--limit-categories solo se puede usar con --dry-run")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.command == "province":
        return _province(args)
    if args.command == "import":
        return _import(args)
    if args.command == "search":
        return _search(args)

    import uvicorn

    uvicorn.run("comparador.api:create_app", factory=True, port=args.port)
    return 0


def _province(args: argparse.Namespace) -> int:
    with connect() as conn:
        if args.action == "list":
            for p in queries.provinces(conn):
                zones = ", ".join(
                    f"{s['supermarket']}={s['zone']} (última: {s['last_import'] or 'nunca'})"
                    for s in p["supermarkets"]) or "sin zonas"
                print(f"{p['id']:<12} {p['name']:<14} CP {p['postal_code']}  {zones}")
            return 0
        providers = [get_provider(name) for name in sorted(PROVIDERS)]
        try:
            result = add_province(conn, providers, args.id, args.name, args.postal_code)
        except ValueError as exc:
            print(f"Error: {exc}")
            return 2
    print(f"Provincia {args.id}:")
    for supermarket, zone in result.items():
        print(f"  {supermarket} → {zone}" if zone
              else f"  {supermarket}: sin servicio en {args.postal_code}")
    return 0


def _import(args: argparse.Namespace) -> int:
    provider = get_provider(args.supermarket, delay=args.delay)
    if args.dry_run:
        count = 0
        for item in provider.fetch_all(args.zone, limit_categories=args.limit_categories):
            count += 1
            p, o = item.product, item.price
            print(f"{p.external_id:>8}  {o.price:>7.2f} €  {p.name}")
        print(f"\n{count} productos vistos (sin guardar).")
        return 0

    with connect() as conn:
        results = run_import(conn, provider, zone_code=args.zone)
    if not results:
        print("No hay zonas activas: añade una con `comparador province add`.")
        return 1
    for r in results:
        print(f"{r.zone}: {r.products} productos" if r.error is None
              else f"{r.zone}: ERROR {r.error}")
    return 1 if any(r.error for r in results) else 0


def _search(args: argparse.Namespace) -> int:
    with connect() as conn:
        if not queries.province_exists(conn, args.province):
            print(f"Provincia desconocida: {args.province}")
            return 2
        rows = queries.search(conn, args.province, args.query, sort=args.sort, limit=args.limit)
    for r in rows:
        unit = f"{r['unit_price']:.2f} €/{r['unit']}" if r["unit_price"] is not None else ""
        print(f"[{r['id']:>5}] {r['price']:>6.2f} €  {unit:>13}  {r['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Borrar SQLite y su empaquetado:

```bash
git rm -q src/comparador/storage.py src/comparador/schema.sql
```

En `pyproject.toml`, eliminar el bloque:

```toml
[tool.setuptools.package-data]
comparador = ["schema.sql"]
```

- [ ] **Step 3: Ejecutar y comprobar que no queda rastro de SQLite**

Run: `.venv/Scripts/python -m pip install -q -e ".[dev]" && .venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS.
Run: `git grep -n -i "sqlite\|storage" -- src tests`
Expected: sin resultados.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "CLI: comandos province/import/search sobre Postgres; se retira SQLite"
```

---

### Task 8: Importación diaria, Render y documentación

**Files:**
- Modify: `.github/workflows/import-diario.yml`, `README.md`, `CLAUDE.md`
- Create: `render.yaml`

**Interfaces:**
- Consumes: `comparador import mercadona` (Task 7), `comparador.api:create_app` (Task 6).

- [ ] **Step 1: Workflow diario**

`.github/workflows/import-diario.yml` completo:

```yaml
# Descarga los precios de todas las zonas activas una vez al día y los
# guarda en Supabase. El histórico vive en la base de datos.
name: Importación diaria

on:
  schedule:
    - cron: "17 5 * * *"   # 05:17 UTC, a una hora de poco tráfico
  workflow_dispatch:

jobs:
  mercadona:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install .
      - run: comparador import mercadona
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
```

- [ ] **Step 2: Blueprint de Render**

`render.yaml`:

```yaml
# Render: New → Blueprint → este repositorio. DATABASE_URL se rellena en el
# panel con la cadena del Transaction pooler de Supabase (puerto 6543).
services:
  - type: web
    name: superpricek-api
    runtime: python
    plan: free
    region: frankfurt
    buildCommand: pip install .
    startCommand: uvicorn comparador.api:create_app --factory --host 0.0.0.0 --port $PORT
    healthCheckPath: /health
    envVars:
      - key: DATABASE_URL
        sync: false
      - key: PYTHON_VERSION
        value: "3.12.7"
```

- [ ] **Step 3: README**

En `README.md`: en la arquitectura, `Base de datos + histórico` pasa a `src/comparador/importer.py, queries.py · supabase/migrations/`; sustituir las decisiones de "Histórico", "Región" y "SQLite para empezar" por:

```markdown
- **Histórico por periodos.** Cada fila de `price_periods` es un tramo con el
  mismo precio (`valid_from` → `last_seen`). Si el precio no cambia, solo se
  alarga el tramo: el histórico ocupa ~1 MB al año por zona.
- **Provincias y zonas.** Mercadona fija precios por almacén. Cada provincia
  usa el almacén de su capital (`comparador province add`); varias provincias
  pueden compartir almacén y se descarga una sola vez.
- **Postgres en Supabase**; los tests usan un Postgres 17 en Docker.
```

Sustituir "Puesta en marcha" por:

````markdown
## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env        # y rellena DATABASE_URL (Supabase → Connect → Session pooler)

comparador province add barcelona "Barcelona" --postal-code 08001
comparador province list

# Prueba rápida sin base de datos: 2 categorías
comparador import mercadona --zone bcn1 --limit-categories 2 --dry-run

# Importación de todas las zonas activas (~3 min por zona)
comparador import mercadona

comparador search "leche" --province barcelona
comparador serve            # → http://localhost:8000/docs
```
````

Endpoints: sustituir la tabla por la de la sección 4 de la spec. Tests:

````markdown
## Tests

```bash
docker compose up -d        # Postgres 17 local en el puerto 54329
pytest
```

Los tests usan una muestra real recortada de la API de Mercadona
(`tests/fixtures/`) y un Postgres local: no hacen peticiones a Mercadona ni
tocan Supabase. Sin Docker, los tests de base de datos se saltan.
````

Roadmap: añadir `- [x] Fase 1b — Supabase, histórico por periodos y 6 provincias`.

- [ ] **Step 4: CLAUDE.md**

En `## Comandos`, sustituir el bloque por los comandos de la sección "Puesta en marcha" de arriba más `docker compose up -d` y `pytest`. En `## Pendiente conocido`, borrar las tres líneas ya resueltas (orden de `search`, workflow con `vlc1`, histórico en la caché) y dejar:

```markdown
- Marca y EAN: solo en `/api/products/{id}/` (una petición por producto).
- Vigilar que el proyecto de Supabase (plan gratuito) no se pause por inactividad.
```

- [ ] **Step 5: Verificar y commit**

Run: `.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS.

```bash
git add -A && git commit -m "Importación diaria contra Supabase, blueprint de Render y documentación"
```

---

### Task 9: Puesta en marcha en Supabase

Operativa (sin TDD). Hace peticiones reales a Mercadona: ejecutar **una sola vez**.

**Files:** ninguno nuevo; puede ajustar `CLAUDE.md`.

- [ ] **Step 1: Aplicar la migración**

Con el MCP de Supabase: `apply_migration(project_id="gikayuegystreypnztsp", name="init", query=<contenido de supabase/migrations/20261008120000_init.sql>)`.
Luego `list_tables(project_id, schemas=["public"])`.
Expected: las 7 tablas.

- [ ] **Step 2: Revisor de seguridad y rendimiento**

`get_advisors(project_id, type="security")` y `get_advisors(project_id, type="performance")`.
Expected: solo avisos informativos de "RLS enabled, no policy" (es intencionado: nadie entra por la API REST). Cualquier otro aviso se corrige con una migración nueva antes de seguir.

- [ ] **Step 3: Dar de alta las 6 provincias** (6 peticiones a Mercadona, 1 por provincia)

```bash
comparador province add madrid "Madrid" --postal-code 28001
comparador province add barcelona "Barcelona" --postal-code 08001
comparador province add valencia "Valencia" --postal-code 46001
comparador province add sevilla "Sevilla" --postal-code 41001
comparador province add alicante "Alicante" --postal-code 03001
comparador province add murcia "Murcia" --postal-code 30001
comparador province list
```

Expected: `mad3`, `bcn1`, `vlc1`, `svq1`, `alc1`, `alc1`.

- [ ] **Step 4: Primera importación** (~15 min, 5 zonas)

Run: `comparador import mercadona` (en segundo plano; una sola vez)
Expected: 5 líneas `zona: N productos`, sin `ERROR`; código de salida 0.

- [ ] **Step 5: Comprobaciones**

```bash
comparador search "leche semidesnatada" --province barcelona --limit 5
comparador search "leche semidesnatada" --province madrid --limit 5
```

Con `execute_sql`:
`select pg_size_pretty(pg_database_size('postgres'))` (esperado: unos pocos MB) y
`select z.code, count(*) from price_periods pp join price_zones z on z.id = pp.zone_id group by 1`.

Arrancar `comparador serve` y comprobar `/api/v1/provinces` y una búsqueda por provincia.

- [ ] **Step 6: Commit** (si se ajustó CLAUDE.md con datos reales)

```bash
git add -A && git commit -m "Supabase en marcha: 6 provincias y primera importación"
```

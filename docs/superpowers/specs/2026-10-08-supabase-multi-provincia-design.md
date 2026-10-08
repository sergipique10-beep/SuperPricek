# Diseño: Supabase y precios por provincia

Fecha: 2026-10-08 · Estado: pendiente de revisión

## Objetivo

Pasar la base de datos de SQLite a Supabase (Postgres) y guardar precios de
varias provincias a la vez, para que el usuario final elija la suya.

Criterios de éxito:

- La importación diaria escribe en Supabase los precios de las 6 provincias
  iniciales y el histórico ya no depende de la caché de GitHub Actions.
- La API responde por provincia (`?province=barcelona`) y está publicada en Render.
- Añadir una provincia es un solo comando, sin tocar código.
- El histórico cabe durante años en los 500 MB del plan gratuito.

## Decisiones

| Tema | Decisión | Motivo |
|---|---|---|
| Acceso a datos | Postgres directo con psycopg 3; se elimina SQLite | Un solo dialecto SQL, igual en tests y producción |
| Histórico | Periodos de precio (`valid_from` → `last_seen`), no una fila diaria | ~1 MB/año por zona frente a ~250 MB |
| Ubicación | El usuario elige **provincia**; cada provincia usa el almacén de su capital | Mercadona fija precios por almacén, no por provincia |
| Proyecto Supabase | `superpricek` (`gikayuegystreypnztsp`), eu-central-1, plan gratuito | Ya creado |
| Hosting API | Render, plan gratuito, región Frankfurt | Junto a la base de datos |
| Tests | Postgres 17 en Docker | Misma versión que Supabase |

## Provincias iniciales

| id | Nombre | CP de referencia | Almacén Mercadona |
|---|---|---|---|
| madrid | Madrid | 28001 | mad3 |
| barcelona | Barcelona | 08001 | bcn1 |
| valencia | Valencia | 46001 | vlc1 |
| sevilla | Sevilla | 41001 | svq1 |
| alicante | Alicante | 03001 | alc1 |
| murcia | Murcia | 30001 | alc1 (compartido) |

5 almacenes → 5 × 154 peticiones y ~15 min al día. Málaga no tiene tienda
online (404) y queda fuera.

## 1. Esquema (`supabase/migrations/20261008120000_init.sql`)

```sql
create schema if not exists extensions;
create extension if not exists pg_trgm with schema extensions;

create table supermarkets (
  id           text primary key,                 -- 'mercadona'
  display_name text not null
);

create table provinces (
  id          text primary key,                  -- 'barcelona'
  name        text not null,
  postal_code text not null check (postal_code ~ '^\d{5}$')
);

-- Dónde fija precios cada cadena (en Mercadona, el almacén)
create table price_zones (
  id          bigint generated always as identity primary key,
  supermarket text not null references supermarkets(id),
  code        text not null,                     -- 'bcn1', '3968'
  active      boolean not null default true,
  unique (supermarket, code),
  unique (id, supermarket)                       -- para la FK compuesta de abajo
);

-- Qué zona usa cada provincia en cada cadena (una por cadena)
create table province_zones (
  province_id text   not null references provinces(id) on delete cascade,
  supermarket text   not null,
  zone_id     bigint not null,
  primary key (province_id, supermarket),
  foreign key (zone_id, supermarket) references price_zones(id, supermarket)
);
create index province_zones_zone_id_idx on province_zones (zone_id);

create table products (
  id          bigint generated always as identity primary key,
  supermarket text not null references supermarkets(id),
  external_id text not null,
  name        text not null,
  brand       text,
  category    text,
  packaging   text,
  size        numeric(12,4),
  size_unit   text,
  ean         text,
  image_url   text,
  url         text,
  first_seen  timestamptz not null default now(),
  last_seen   timestamptz not null default now(),
  unique (supermarket, external_id)
);
create index products_name_trgm_idx on products
  using gin (lower(name) extensions.gin_trgm_ops);

create table price_periods (
  id             bigint generated always as identity primary key,
  product_id     bigint not null references products(id),
  zone_id        bigint not null references price_zones(id),
  price          numeric(10,2) not null,
  unit_price     numeric(12,4),
  unit           text,
  previous_price numeric(10,2),
  on_promotion   boolean not null default false,
  valid_from     date not null,
  last_seen      date not null,
  check (last_seen >= valid_from),
  unique (product_id, zone_id, valid_from)       -- sirve también para buscar el último periodo
);
create index price_periods_zone_last_seen_idx on price_periods (zone_id, last_seen);

create table import_runs (
  id          bigint generated always as identity primary key,
  zone_id     bigint not null references price_zones(id),
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  import_date date not null,
  products    integer not null default 0,
  status      text not null default 'running' check (status in ('running', 'ok', 'error')),
  error       text
);
create index import_runs_zone_id_idx on import_runs (zone_id, import_date);

-- Supabase expone `public` en su API REST: RLS activado y sin políticas,
-- así anon/authenticated no ven nada. Importador y API usan conexión directa.
alter table supermarkets   enable row level security;
alter table provinces      enable row level security;
alter table price_zones    enable row level security;
alter table province_zones enable row level security;
alter table products       enable row level security;
alter table price_periods  enable row level security;
alter table import_runs    enable row level security;
```

**Precio actual** de un producto en una zona: su último periodo (mayor
`valid_from`), solo si `last_seen` = `import_date` de la última importación
`ok` de esa zona. Si no, el producto no está a la venta allí.

## 2. Importador

**Fecha de importación**: la fecha actual en `Europe/Madrid`, calculada en Python.

**Flujo por zona** (`run_import(provider, db, zone, ...)`):

1. Registrar `import_runs` (`running`) en su propia transacción.
2. Descargar el catálogo completo en memoria (`provider.fetch_all(zone)`).
3. En **una transacción**:
   1. `COPY` de los productos a una tabla temporal `staging`.
   2. Upsert en `products` desde `staging` (actualiza nombre, envase… y `last_seen`).
   3. Para cada producto de `staging`, se toma su último periodo en la zona:
      - mismos valores (`price`, `unit_price`, `unit`, `previous_price`,
        `on_promotion`, comparados con `is not distinct from`) → `last_seen = hoy`;
      - valores distintos y `valid_from = hoy` (reimportación el mismo día)
        → se sobrescriben los valores;
      - valores distintos o sin periodo → `insert` de un periodo nuevo `(hoy, hoy)`.
   4. `import_runs` → `ok` con el número de productos.
4. Si algo falla: `rollback`, `import_runs` → `error` con el mensaje, y se
   **continúa con la siguiente zona**. El comando termina con código ≠ 0 si
   alguna zona falló.

Simplificación aceptada: si un producto desaparece unos días y vuelve con el
mismo precio, el periodo se alarga y cubre el hueco.

**Contrato `Provider`** (cambia):

```python
class Provider(ABC):
    name: str
    display_name: str
    def zone_for_postal_code(self, postal_code: str) -> str: ...   # ZoneNotServed si no hay servicio
    def fetch_all(self, zone: str, limit_categories: int | None = None) -> Iterator[ScrapedItem]: ...
```

`MercadonaProvider` deja de recibir el almacén en el constructor.
`warehouse_for()` pasa a ser `zone_for_postal_code()` y convierte el 404
"outside of our working area" en `ZoneNotServed`. En el modelo,
`PriceObservation.region` se renombra a `zone`.

## 3. CLI

```bash
comparador province add barcelona "Barcelona" --postal-code 08001
    # para cada provider: resuelve la zona, la crea si no existe y la enlaza.
    # si la cadena no da servicio en ese CP, avisa y sigue.
comparador province list            # provincia → zona por cadena, última importación
comparador import mercadona         # todas las zonas activas
comparador import mercadona --zone bcn1 [--limit-categories N] [--dry-run] [--delay S]
comparador search "leche" --province barcelona [--sort unit_price|price]
comparador serve
```

`--dry-run` no necesita base de datos y requiere `--zone`. `--limit-categories`
solo se admite con `--dry-run` (una importación parcial marcaría el resto del
catálogo como no disponible).

## 4. API

`province` es obligatorio en los endpoints de productos; si no existe → 404.

| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/health` | `{"status": "ok"}` |
| GET | `/api/v1/provinces` | id, nombre y, por cadena, zona y última importación ok |
| GET | `/api/v1/supermarkets` | cadenas y nº de productos |
| GET | `/api/v1/products/search?q=&province=&supermarket=&sort=&limit=` | productos a la venta hoy |
| GET | `/api/v1/products/{id}?province=` | detalle con precio actual (404 si no se vende allí) |
| GET | `/api/v1/products/{id}/history?province=` | lista de periodos (`valid_from`, `last_seen`, precios) |

**Orden de búsqueda** (`sort=unit_price`, por defecto): primero la unidad más
frecuente entre los resultados (empate → por nombre de unidad; sin unidad al
final), y dentro de cada unidad por `unit_price` y `price`. `sort=price`: por precio del envase.

Búsqueda: cada palabra con `lower(name) like '%palabra%'` (usa el índice trigram).

**Conexiones**: `psycopg_pool.ConnectionPool` (min 1, max 5) abierto en el
`lifespan` de FastAPI, con `prepare_threshold=None` porque el pooler de
Supabase en modo *transaction* no admite sentencias preparadas.

## 5. Configuración

- `DATABASE_URL` en entorno; en local se carga de `.env` (python-dotenv).
  - Importador y GitHub Actions: Supavisor **session** (puerto 5432).
  - API en Render: Supavisor **transaction** (puerto 6543).
- Dependencias nuevas: `psycopg[binary]`, `psycopg-pool`, `python-dotenv`.
- Se eliminan `storage.py` (SQLite) y `schema.sql`; los sustituyen `db.py` y
  `supabase/migrations/`.

## 6. Tests y CI

- `docker-compose.yml`: `postgres:17` en el puerto 54329.
- Los tests de base de datos usan `TEST_DATABASE_URL`. Al inicio de la sesión
  recrean el esquema `public` y aplican `supabase/migrations/*.sql` en orden;
  entre tests hacen `truncate ... restart identity cascade`. Sin
  `TEST_DATABASE_URL` se saltan con un aviso; los del parser siempre se ejecutan.
- Tests nuevos: mismo precio al día siguiente alarga el periodo; precio
  distinto abre otro; reimportar el mismo día no duplica; producto que
  desaparece deja de salir; fallo en una zona no afecta a otra;
  `province add` con provider simulado (incluido CP sin servicio); API con
  `province` (404 si no existe) y orden agrupado por unidad.
- `ci.yml`: Postgres 17 como *service container*.

## 7. Importación diaria y despliegue

- `import-diario.yml`: secret `DATABASE_URL`, `comparador import mercadona`,
  `timeout-minutes: 60`. Se quitan la caché y el artefacto del `.db`.
- `render.yaml`: servicio web `superpricek-api`, Python, plan free, región
  frankfurt, `pip install .`, arranque
  `uvicorn comparador.api:create_app --factory --host 0.0.0.0 --port $PORT`,
  `healthCheckPath: /health`, `DATABASE_URL` con `sync: false`.

## 8. Pasos manuales (usuario)

1. Restablecer la contraseña de la base de datos en el panel de Supabase.
2. Crear `.env` con `DATABASE_URL` (session pooler).
3. Subir el repo a GitHub y crear el secret `DATABASE_URL`.
4. Crear el servicio en Render desde el blueprint y poner `DATABASE_URL`
   (transaction pooler).

## Fuera de alcance

Marca y EAN (requieren una petición por producto), búsqueda sin tildes
(`unaccent`), otras cadenas, frontend, más provincias (se añaden con
`province add`).

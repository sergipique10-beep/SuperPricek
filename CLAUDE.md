# CLAUDE.md

Comparador de precios de supermercados en España (proyecto de portfolio).
Arquitectura y uso: ver README.md.

## Cómo trabajar con el usuario

- Responder y explicar en español: qué se hace y por qué (está aprendiendo).
- Preguntar antes de cambios grandes de arquitectura (p. ej. cambiar de base
  de datos, añadir peticiones por producto, cambiar el modelo común).

## Comandos

```bash
.venv/Scripts/python -m pip install -e ".[dev]"     # Windows; en Linux .venv/bin/...
docker compose up -d                                # Postgres 17 de tests (puerto 54329)
.venv/Scripts/python -m pytest -q
comparador province add barcelona "Barcelona" --postal-code 08001
comparador province list
comparador import mercadona --zone bcn1 --limit-categories 2 --dry-run  # sin BD, 3 peticiones
comparador import mercadona                          # todas las zonas activas, ~3 min/zona
comparador search "leche" --province barcelona
comparador serve                                     # http://localhost:8000/docs
```

`.env` (no versionado) tiene `DATABASE_URL` (Supabase, session pooler) y
`TEST_DATABASE_URL` (Docker). Los tests solo aceptan una base de datos en localhost.

En la consola de Windows, usar `PYTHONIOENCODING=utf-8` para ver bien las tildes.

## Reglas

- Ser respetuoso con Mercadona: mantener la espera entre peticiones, no lanzar
  importaciones completas repetidas. Para investigar el formato, pedir solo una
  o dos categorías y guardar el JSON localmente.
- La API (`api.py`) nunca hace scraping; solo lee de la base de datos.
- Todo el conocimiento del formato de Mercadona vive en `providers/mercadona.py`.
- Los tests no usan red: `tests/fixtures/` es una muestra real recortada.

## API de Mercadona (verificado con datos reales, octubre 2026)

- `GET /api/categories/?lang=es&wh=<almacén>`: 26 categorías de nivel 1 y 152
  de nivel 2. Productos: `GET /api/categories/{id}/` → `categories[].products[]`.
- Almacén desde código postal: `PUT /api/postal-codes/actions/change-pc/` con
  `{"new_postal_code": "08820"}`. El almacén llega en la **cabecera**
  `x-customer-wh` (08820 → `bcn1`). Hay almacenes con código numérico
  (Granada `3968`, Bizkaia `4391`) que también devuelven catálogo con precios.
  Fuera de zona → 404 `{"error_msg":"This zip code is outside of our working area"}`
  (toda la provincia de Málaga, octubre 2026).
- Capitales probadas: Barcelona `bcn1`, Madrid `mad3`, Valencia `vlc1`,
  Sevilla `svq1`, Alicante y Murcia `alc1` (compartido), Granada `3968`,
  Córdoba `3769`, Bizkaia `4391`, Zaragoza `4493`, Málaga sin servicio.
- `price_instructions`:
  - `unit_price` = precio del envase; `reference_price` + `reference_format`
    = precio por kg / L / ud / 100 ml / 100 g / docena / lavado.
  - `previous_unit_price` llega con espacios delante (`"        5.04"`).
  - `price_decreased` viene siempre a `false`: la rebaja se detecta comparando
    `previous_unit_price` con `unit_price`.
  - Granel (`selling_method: 1`, pescadería): `unit_price` es precio/kg × 99,
    así que se usa el precio por kg. Sin verificar si `previous_unit_price`
    también viene ×99 en esos productos.
- El listado **no trae marca ni EAN**: solo están en `/api/products/{id}/`
  (1 petición por producto, ~4300). Decisión pendiente.

## Infraestructura

- Supabase: proyecto `superpricek` (ref `gikayuegystreypnztsp`), región
  eu-central-1 (Fráncfort), Postgres 17, plan gratuito (500 MB). Migraciones
  de `supabase/migrations/` aplicadas (la del índice de FK, desde el SQL Editor:
  no figura en el historial de Supabase; con la CLI haría falta `migration repair`).
  Primera importación 2026-10-08: 5 almacenes, ~4300 productos cada uno, 23 MB.
- Hosting de la API: Render (plan gratuito; se duerme tras 15 min sin tráfico).
- Diseño (2026-10-08): `docs/superpowers/specs/2026-10-08-supabase-multi-provincia-design.md`.
  Provincias → zonas de precio (almacén) → histórico por periodos
  (`price_periods`), solo Postgres (psycopg 3), Docker para tests.
  Provincias iniciales: Madrid, Barcelona, Valencia, Sevilla, Alicante, Murcia.

## Pendiente conocido

- Marca y EAN: solo en `/api/products/{id}/` (una petición por producto).
- Vigilar que el proyecto de Supabase (plan gratuito) no se pause por inactividad.

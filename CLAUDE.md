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
.venv/Scripts/python -m pytest -q
comparador import mercadona --limit-categories 2 --dry-run   # prueba rápida, 3 peticiones
comparador import mercadona --postal-code 08820              # completa, ~3 min
comparador search "leche"
comparador serve                                             # http://localhost:8000/docs
```

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
  `x-customer-wh` (08820 → `bcn1`). Un código sin tienda online no da error.
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

## Pendiente conocido

- `search` ordena por `unit_price` mezclando unidades (€/ud de cápsulas antes
  que €/l de leche).
- El workflow diario usa `--warehouse vlc1`; el usuario vive en El Prat (bcn1).
- El histórico en GitHub Actions vive en la caché: si caduca, se pierde.
  Plan: migrar a Supabase/Postgres.

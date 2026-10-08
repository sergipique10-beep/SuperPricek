# Comparador de precios de supermercados

Recopila, normaliza y guarda el histórico de precios de supermercados en España
para poder compararlos por provincia. Empieza con **Mercadona**; la arquitectura
está pensada para añadir DIA, Lidl y otras cadenas sin tocar el resto del código.

## Arquitectura

```
  Web del supermercado
          │  (API interna, 1 vez al día, peticiones espaciadas)
          ▼
  Provider (uno por cadena)      src/comparador/providers/
          │  traduce al modelo común
          ▼
  Modelo normalizado             src/comparador/models.py
          │
          ▼
  Importador                     src/comparador/importer.py
          │
          ▼
  Postgres (Supabase)            supabase/migrations/
  provincias → zonas → periodos de precio
          │
          ▼
  Consultas + API (solo lectura) src/comparador/queries.py, api.py
          │
          ▼
  Frontend / otros consumidores  (siguiente fase)
```

Decisiones clave:

- **La API nunca hace scraping.** Solo consulta lo ya guardado: es rápida,
  estable y no castiga la web del supermercado con cada visita.
- **Un provider por cadena.** Todo lo que sabemos del formato de Mercadona
  vive en `providers/mercadona.py`. Si su API cambia, solo se toca ese archivo.
- **Histórico por periodos.** Cada fila de `price_periods` es un tramo con el
  mismo precio (`valid_from` → `last_seen`). Si el precio no cambia, solo se
  alarga el tramo: el histórico ocupa ~1 MB al año por zona.
- **Provincias y zonas.** Mercadona fija precios por almacén. Cada provincia
  usa el almacén de su capital (`comparador province add`); varias provincias
  pueden compartir almacén y se descarga una sola vez.
- **Una transacción por zona.** Si la importación de una zona falla, se
  deshace entera y las demás siguen; la API muestra el último día correcto.
- **Postgres en Supabase**; los tests usan un Postgres 17 en Docker.

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

### Endpoints

`province` es obligatorio en los endpoints de productos (404 si no existe).

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/health` | Estado del servicio |
| GET | `/api/v1/provinces` | Provincias, su zona por cadena y última importación |
| GET | `/api/v1/supermarkets` | Cadenas y nº de productos |
| GET | `/api/v1/products/search?q=leche&province=barcelona` | Productos a la venta hoy; `sort=unit_price` (por defecto) o `price` |
| GET | `/api/v1/products/{id}?province=barcelona` | Detalle con el precio actual |
| GET | `/api/v1/products/{id}/history?province=barcelona` | Histórico como periodos de precio |

## Tests

```bash
docker compose up -d        # Postgres 17 local en el puerto 54329
pytest
```

Los tests usan una muestra real recortada de la API de Mercadona
(`tests/fixtures/`) y un Postgres local: no hacen peticiones a Mercadona ni
tocan Supabase. Sin Docker, los tests de base de datos se saltan.

## Despliegue

- **Importación diaria:** `.github/workflows/import-diario.yml`, con el secret
  `DATABASE_URL` (Session pooler de Supabase).
- **API:** Render, desde `render.yaml` (New → Blueprint). `DATABASE_URL` con
  el Transaction pooler de Supabase (puerto 6543).

## Uso responsable

Mercadona no ofrece una API pública: este proyecto usa los mismos endpoints
que su propia web. Por eso se identifica con un User-Agent propio, espera entre
peticiones, reintenta con espera creciente si hay errores y solo se ejecuta una
vez al día. Es un proyecto personal sin ánimo de lucro.

## Roadmap

- [x] Fase 1 — Arquitectura + provider de Mercadona + API básica
- [x] Fase 1b — Supabase, histórico por periodos y 6 provincias
- [ ] Fase 2 — Segundo supermercado (DIA o Lidl)
- [ ] Fase 3 — Emparejar el mismo producto entre cadenas
- [ ] Fase 4 — Optimizador de lista de la compra
- [ ] Fase 5 — Frontend con gráficos y alertas de bajada de precio

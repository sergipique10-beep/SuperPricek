# Comparador de precios de supermercados

Recopila, normaliza y guarda el histórico de precios de supermercados en España
para poder compararlos. Empieza con **Mercadona**; la arquitectura está pensada
para añadir DIA, Lidl y otras cadenas sin tocar el resto del código.

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
  Base de datos + histórico      src/comparador/storage.py, schema.sql
          │
          ▼
  API pública (solo lectura)     src/comparador/api.py
          │
          ▼
  Frontend / otros consumidores  (siguiente fase)
```

Decisiones clave:

- **La API nunca hace scraping.** Solo consulta lo ya guardado: es rápida,
  estable y no castiga la web del supermercado con cada visita.
- **Un provider por cadena.** Todo lo que sabemos del formato de Mercadona
  vive en `providers/mercadona.py`. Si su API cambia, solo se toca ese archivo.
- **Histórico de precios.** Cada día se guarda una observación por producto
  (`price_observations`); reimportar el mismo día no duplica filas.
- **Región.** Mercadona cambia catálogo y precios según el almacén (`wh`),
  que depende del código postal (`--postal-code` lo consulta a Mercadona,
  p. ej. 08820 → `bcn1`). Se guarda con cada precio.
- **SQLite para empezar**, con un esquema fácil de migrar a PostgreSQL/Supabase.

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Prueba rápida: 2 categorías, sin guardar nada
comparador import mercadona --limit-categories 2 --dry-run

# Importación completa (~3 min, ~4300 productos: hay 1 s de espera entre peticiones)
comparador import mercadona --postal-code 08820   # o --warehouse bcn1

# Buscar en lo guardado
comparador search "leche entera"

# Arrancar la API → http://localhost:8000/docs
comparador serve
```

### Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/health` | Estado del servicio |
| GET | `/api/v1/supermarkets` | Cadenas, nº de productos y última importación |
| GET | `/api/v1/products/search?q=leche` | Búsqueda, ordenada por precio por unidad |
| GET | `/api/v1/products/{id}` | Detalle con el último precio |
| GET | `/api/v1/products/{id}/history` | Histórico de precios |

## Tests

```bash
pytest
```

Los tests usan una muestra real recortada de la API de Mercadona guardada en
`tests/fixtures/`, así que no hacen peticiones reales.

## Uso responsable

Mercadona no ofrece una API pública: este proyecto usa los mismos endpoints
que su propia web. Por eso se identifica con un User-Agent propio, espera entre
peticiones, reintenta con espera creciente si hay errores y solo se ejecuta una
vez al día. Es un proyecto personal sin ánimo de lucro.

## Roadmap

- [x] Fase 1 — Arquitectura + provider de Mercadona + API básica
- [ ] Fase 2 — Segundo supermercado (DIA o Lidl)
- [ ] Fase 3 — Emparejar el mismo producto entre cadenas
- [ ] Fase 4 — Optimizador de lista de la compra
- [ ] Fase 5 — Frontend con gráficos y alertas de bajada de precio

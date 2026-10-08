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
        cur.execute("""
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
            log.exception("Fallo importando %s/%s", provider.name, zone["code"])
            try:
                conn.rollback()
                conn.execute(
                    "update import_runs set status = 'error', error = %s, finished_at = now() "
                    "where id = %s",
                    (str(exc)[:1000], run_id),
                )
                conn.commit()
            except psycopg.Error:
                # Conexión perdida: el fallo no queda registrado, pero no se
                # oculta el error original ni se aborta el resto del comando.
                log.error("No se pudo registrar el error de %s: conexión perdida", zone["code"])
            results.append(ZoneResult(zone["code"], 0, str(exc)))
    return results

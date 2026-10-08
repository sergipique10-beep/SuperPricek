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

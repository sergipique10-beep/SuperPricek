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

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

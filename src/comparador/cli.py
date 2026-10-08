"""Línea de comandos.

    comparador import mercadona --limit-categories 2 --dry-run
    comparador import mercadona --warehouse bcn1
    comparador import mercadona --postal-code 08820
    comparador search "leche entera"
    comparador serve
"""

from __future__ import annotations

import argparse
import logging

from .importer import run_import
from .providers import PROVIDERS, get_provider
from .storage import Storage


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="comparador")
    parser.add_argument("--db", default="data/precios.db", help="Ruta de la base de datos")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="Descargar precios de un supermercado")
    imp.add_argument("supermarket", choices=sorted(PROVIDERS))
    where = imp.add_mutually_exclusive_group()
    where.add_argument("--warehouse", default="vlc1",
                       help="Almacén de Mercadona (depende del código postal)")
    where.add_argument("--postal-code",
                       help="Código postal; se consulta a Mercadona su almacén")
    imp.add_argument("--limit-categories", type=int, default=None,
                     help="Solo las N primeras categorías (para pruebas)")
    imp.add_argument("--delay", type=float, default=1.0,
                     help="Segundos de espera entre peticiones")
    imp.add_argument("--dry-run", action="store_true",
                     help="Mostrar sin guardar en la base de datos")

    se = sub.add_parser("search", help="Buscar en los precios guardados")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=20)

    sv = sub.add_parser("serve", help="Arrancar la API")
    sv.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.command == "import":
        provider = get_provider(args.supermarket, warehouse=args.warehouse,
                                postal_code=args.postal_code, delay=args.delay)
        storage = None if args.dry_run else Storage(args.db)
        result = run_import(provider, storage, args.limit_categories, args.dry_run)
        print(f"\n{result.products} productos {'vistos' if result.dry_run else 'guardados'}.")

    elif args.command == "search":
        for r in Storage(args.db).search(args.query, limit=args.limit):
            unit = f"{r['unit_price']:.2f} €/{r['unit']}" if r["unit_price"] else ""
            print(f"[{r['id']:>5}] {r['price']:>6.2f} €  {unit:>12}  {r['name']}")

    elif args.command == "serve":
        import os
        import uvicorn

        os.environ["DATABASE_PATH"] = args.db
        uvicorn.run("comparador.api:create_app", factory=True, port=args.port)


if __name__ == "__main__":
    main()

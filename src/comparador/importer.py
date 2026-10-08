"""Une un provider con el almacenamiento. Es lo que ejecuta el cron diario."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .providers.base import Provider
from .storage import Storage

log = logging.getLogger(__name__)


@dataclass
class ImportResult:
    supermarket: str
    products: int
    dry_run: bool


def run_import(provider: Provider, storage: Storage | None,
               limit_categories: int | None = None,
               dry_run: bool = False) -> ImportResult:
    """Descarga el catálogo y lo guarda. Con dry_run solo cuenta y muestra."""
    count = 0
    run_id = None
    if not dry_run:
        assert storage is not None
        storage.ensure_supermarket(provider.name, provider.display_name)
        run_id = storage.start_run(provider.name)

    try:
        for item in provider.fetch_all(limit_categories=limit_categories):
            count += 1
            if dry_run:
                p, o = item.product, item.price
                print(f"{p.external_id:>8}  {o.price:>7.2f} €  {p.name}")
            else:
                storage.save(item)
                if count % 200 == 0:
                    storage.commit()
                    log.info("%d productos guardados…", count)
        if storage and not dry_run:
            storage.commit()
            storage.finish_run(run_id, count)
    except Exception as exc:
        if storage and run_id is not None:
            storage.conn.rollback()
            storage.finish_run(run_id, count, error=str(exc))
        raise

    log.info("%s: %d productos", provider.name, count)
    return ImportResult(provider.name, count, dry_run)

"""Capa de almacenamiento. Usa SQLite para empezar sin instalar nada;
el esquema está pensado para migrar a PostgreSQL/Supabase más adelante."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

from .models import ScrapedItem


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: str | Path = "data/precios.db"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(files("comparador").joinpath("schema.sql").read_text())

    def close(self) -> None:
        self.conn.close()

    # ---------- escritura ----------

    def ensure_supermarket(self, id_: str, display_name: str) -> None:
        self.conn.execute(
            "INSERT INTO supermarkets(id, display_name) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name",
            (id_, display_name),
        )

    def save(self, item: ScrapedItem) -> int:
        """Guarda (o actualiza) el producto y añade su precio al histórico.
        Si ya hay un precio de ese producto hoy, se sustituye por el nuevo."""
        p, obs = item.product, item.price
        now = _now()
        cur = self.conn.execute(
            """
            INSERT INTO products (supermarket, external_id, name, brand, category,
                packaging, size, size_unit, ean, image_url, url, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(supermarket, external_id) DO UPDATE SET
                name = excluded.name, brand = excluded.brand,
                category = excluded.category, packaging = excluded.packaging,
                size = excluded.size, size_unit = excluded.size_unit,
                ean = COALESCE(excluded.ean, products.ean),
                image_url = excluded.image_url, url = excluded.url,
                last_seen = excluded.last_seen
            RETURNING id
            """,
            (p.supermarket, p.external_id, p.name, p.brand, p.category, p.packaging,
             p.size, p.size_unit, p.ean, p.image_url, p.url, now, now),
        )
        product_id = cur.fetchone()[0]

        self.conn.execute(
            """
            INSERT INTO price_observations (product_id, price, unit_price, unit,
                previous_price, on_promotion, region, observed_at, observed_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, region, observed_date) DO UPDATE SET
                price = excluded.price, unit_price = excluded.unit_price,
                unit = excluded.unit, previous_price = excluded.previous_price,
                on_promotion = excluded.on_promotion, observed_at = excluded.observed_at
            """,
            (product_id, obs.price, obs.unit_price, obs.unit, obs.previous_price,
             int(obs.on_promotion), obs.region or "",
             obs.observed_at.isoformat(timespec="seconds"),
             obs.observed_at.date().isoformat()),
        )
        return product_id

    def start_run(self, supermarket: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO import_runs(supermarket, started_at) VALUES (?, ?)",
            (supermarket, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, products: int, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE import_runs SET finished_at = ?, products = ?, status = ?, error = ? "
            "WHERE id = ?",
            (_now(), products, "error" if error else "ok", error, run_id),
        )
        self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()

    # ---------- lectura ----------

    _LATEST = """
        SELECT p.id, p.supermarket, p.external_id, p.name, p.brand, p.category,
               p.packaging, p.size, p.size_unit, p.ean, p.image_url, p.url,
               o.price, o.unit_price, o.unit, o.previous_price, o.on_promotion,
               o.observed_at
        FROM products p
        JOIN price_observations o ON o.id = (
            SELECT id FROM price_observations
            WHERE product_id = p.id ORDER BY observed_at DESC LIMIT 1
        )
    """

    def search(self, query: str, supermarket: str | None = None,
               limit: int = 50) -> list[dict]:
        sql = self._LATEST + " WHERE 1 = 1"
        params: list = []
        for word in query.split():
            sql += " AND p.name LIKE ?"
            params.append(f"%{word}%")
        if supermarket:
            sql += " AND p.supermarket = ?"
            params.append(supermarket)
        sql += " ORDER BY o.unit_price IS NULL, o.unit_price, o.price LIMIT ?"
        params.append(limit)
        return [_row(r) for r in self.conn.execute(sql, params)]

    def get_product(self, product_id: int) -> dict | None:
        row = self.conn.execute(self._LATEST + " WHERE p.id = ?", (product_id,)).fetchone()
        return _row(row) if row else None

    def price_history(self, product_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT observed_date AS date, price, unit_price, on_promotion "
            "FROM price_observations WHERE product_id = ? ORDER BY observed_date",
            (product_id,),
        )
        return [_row(r) for r in rows]

    def supermarkets(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT s.id, s.display_name, COUNT(p.id) AS products,
                   (SELECT MAX(finished_at) FROM import_runs r
                    WHERE r.supermarket = s.id AND r.status = 'ok') AS last_import
            FROM supermarkets s LEFT JOIN products p ON p.supermarket = s.id
            GROUP BY s.id ORDER BY s.id
            """
        )
        return [_row(r) for r in rows]


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    if "on_promotion" in d:
        d["on_promotion"] = bool(d["on_promotion"])
    return d

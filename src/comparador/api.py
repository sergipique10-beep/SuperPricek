"""API pública. Solo lee de la base de datos: nunca hace scraping
durante una petición de usuario."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import __version__, queries
from .db import database_url


def create_app(conninfo: str | None = None) -> FastAPI:
    # prepare_threshold=None: el pooler de Supabase (transaction) no admite
    # sentencias preparadas
    pool = ConnectionPool(conninfo or database_url(), min_size=1, max_size=5, open=False,
                          kwargs={"row_factory": dict_row, "prepare_threshold": None})

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool.open(wait=True)
        yield
        pool.close()

    app = FastAPI(title="Comparador de precios de supermercados", version=__version__,
                  lifespan=lifespan)

    def get_conn() -> Iterator[psycopg.Connection]:
        with pool.connection() as conn:
            yield conn

    def check_province(conn: psycopg.Connection, province: str) -> None:
        if not queries.province_exists(conn, province):
            raise HTTPException(404, f"Provincia desconocida: {province}")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/provinces")
    def provinces(conn: psycopg.Connection = Depends(get_conn)):
        return queries.provinces(conn)

    @app.get("/api/v1/supermarkets")
    def supermarkets(conn: psycopg.Connection = Depends(get_conn)):
        return queries.supermarkets(conn)

    @app.get("/api/v1/products/search")
    def search(
        q: str = Query(..., min_length=2, description="Texto a buscar"),
        province: str = Query(..., description="Id de provincia, p. ej. barcelona"),
        supermarket: str | None = None,
        sort: Literal["unit_price", "price"] = "unit_price",
        limit: int = Query(50, ge=1, le=100),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        check_province(conn, province)
        return {"query": q, "province": province,
                "results": queries.search(conn, province, q, supermarket, sort, limit)}

    @app.get("/api/v1/products/{product_id}")
    def product(product_id: int, province: str = Query(...),
                conn: psycopg.Connection = Depends(get_conn)):
        check_province(conn, province)
        item = queries.get_product(conn, province, product_id)
        if not item:
            raise HTTPException(404, "Producto no encontrado o no disponible en esta provincia")
        return item

    @app.get("/api/v1/products/{product_id}/history")
    def history(product_id: int, province: str = Query(...),
                conn: psycopg.Connection = Depends(get_conn)):
        check_province(conn, province)
        if not queries.product_exists(conn, product_id):
            raise HTTPException(404, "Producto no encontrado")
        return {"product_id": product_id, "province": province,
                "history": queries.price_history(conn, province, product_id)}

    return app

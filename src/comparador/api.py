"""API pública. Solo lee de la base de datos: nunca hace scraping
durante una petición de usuario."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query

from . import __version__
from .storage import Storage


def create_app(storage: Storage | None = None) -> FastAPI:
    db = storage or Storage(os.environ.get("DATABASE_PATH", "data/precios.db"))
    app = FastAPI(title="Comparador de precios de supermercados", version=__version__)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/supermarkets")
    def supermarkets():
        return db.supermarkets()

    @app.get("/api/v1/products/search")
    def search(
        q: str = Query(..., min_length=2, description="Texto a buscar"),
        supermarket: str | None = None,
        limit: int = Query(50, ge=1, le=100),
    ):
        return {"query": q, "results": db.search(q, supermarket, limit)}

    @app.get("/api/v1/products/{product_id}")
    def product(product_id: int):
        item = db.get_product(product_id)
        if not item:
            raise HTTPException(404, "Producto no encontrado")
        return item

    @app.get("/api/v1/products/{product_id}/history")
    def history(product_id: int):
        if not db.get_product(product_id):
            raise HTTPException(404, "Producto no encontrado")
        return {"product_id": product_id, "history": db.price_history(product_id)}

    return app

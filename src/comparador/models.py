"""Modelo normalizado, común a todos los supermercados.

Cada provider traduce los datos de su cadena a estas clases. El resto del
sistema (base de datos, API, comparador) solo conoce este formato.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Product:
    supermarket: str          # "mercadona", "dia", ...
    external_id: str          # id del producto en la web del supermercado
    name: str
    brand: str | None = None
    category: str | None = None        # ruta, p. ej. "Lácteos > Leche"
    packaging: str | None = None       # "Brick", "Garrafa", ...
    size: float | None = None          # cantidad del envase (1.0, 0.5 ...)
    size_unit: str | None = None       # "l", "kg", "ud" ...
    ean: str | None = None             # código de barras, si la cadena lo da
    image_url: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class PriceObservation:
    """Un precio visto en un momento concreto. Es la base del histórico."""

    supermarket: str
    external_id: str
    price: float                       # precio del envase en euros
    unit_price: float | None = None    # precio por kg / l / ud
    unit: str | None = None            # unidad de unit_price
    previous_price: float | None = None  # precio antes de una bajada
    on_promotion: bool = False
    zone: str | None = None            # zona de precio (almacén en Mercadona)
    observed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True)
class ScrapedItem:
    """Lo que devuelve un provider: el producto y su precio actual."""

    product: Product
    price: PriceObservation

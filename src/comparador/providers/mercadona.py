"""Provider de Mercadona.

Usa la API interna (no oficial) que consume tienda.mercadona.es:

    GET /api/categories/            -> árbol de categorías (nivel 1 y 2)
    GET /api/categories/{id}/       -> subcategorías (nivel 3) con sus productos

El catálogo y los precios dependen del almacén ("warehouse"), que Mercadona
asigna según el código postal. Se pasa con el parámetro `wh`.

Es una API no documentada: puede cambiar sin aviso. Por eso todo el
conocimiento sobre su formato vive solo en este archivo.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from ..models import PriceObservation, Product, ScrapedItem
from .base import Provider, ZoneNotServed

log = logging.getLogger(__name__)

BASE_URL = "https://tienda.mercadona.es/api"
USER_AGENT = (
    "comparador-super/0.1 (proyecto personal de portfolio; "
    "peticiones espaciadas, una vez al dia)"
)


class MercadonaProvider(Provider):
    name = "mercadona"
    display_name = "Mercadona"

    def __init__(self, delay: float = 1.0, client: httpx.Client | None = None):
        self.delay = delay  # segundos entre peticiones, para no sobrecargar
        self.client = client or httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=20,
        )

    def zone_for_postal_code(self, postal_code: str) -> str:
        """Almacén que Mercadona asigna a un código postal (p. ej. 08820 -> bcn1).

        Es lo mismo que hace la web al escribir el código postal: el almacén
        llega en la cabecera `x-customer-wh`. Fuera de zona responde 404."""
        if not (len(postal_code) == 5 and postal_code.isdigit()):
            raise ValueError(f"Código postal no válido: {postal_code!r}")
        resp = self.client.put(
            "/postal-codes/actions/change-pc/", json={"new_postal_code": postal_code}
        )
        if resp.status_code == 404:
            raise ZoneNotServed(f"Mercadona no tiene tienda online en {postal_code}")
        resp.raise_for_status()
        warehouse = resp.headers.get("x-customer-wh")
        if not warehouse:
            raise RuntimeError(f"Mercadona no devolvió almacén para {postal_code}")
        log.info("Código postal %s -> almacén %s", postal_code, warehouse)
        return warehouse

    # ---------- HTTP ----------

    def _get(self, path: str, zone: str, retries: int = 3) -> dict[str, Any]:
        params = {"lang": "es", "wh": zone}
        for attempt in range(1, retries + 1):
            try:
                resp = self.client.get(path, params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if attempt == retries:
                    raise
                wait = self.delay * 2 ** attempt
                log.warning("Fallo en %s (%s). Reintento en %.0fs", path, exc, wait)
                time.sleep(wait)
        raise RuntimeError("inalcanzable")

    def _pause(self) -> None:
        if self.delay:
            time.sleep(self.delay)

    # ---------- recorrido del catálogo ----------

    def list_categories(self, zone: str) -> list[dict[str, Any]]:
        """Devuelve las categorías de nivel 2, que son las que tienen productos."""
        data = self._get("/categories/", zone)
        result = []
        for top in data.get("results", []):
            for sub in top.get("categories", []):
                result.append({"id": sub["id"], "path": f"{top['name']} > {sub['name']}"})
        return result

    def fetch_all(self, zone: str, limit_categories: int | None = None) -> Iterator[ScrapedItem]:
        categories = self.list_categories(zone)
        if limit_categories:
            categories = categories[:limit_categories]
        log.info("Mercadona: %d categorías (almacén %s)", len(categories), zone)

        seen: set[str] = set()  # un producto puede aparecer en varias categorías
        for cat in categories:
            self._pause()
            data = self._get(f"/categories/{cat['id']}/", zone)
            for leaf in data.get("categories", []):
                path = f"{cat['path']} > {leaf['name']}"
                for raw in leaf.get("products", []):
                    item = parse_product(raw, category=path, zone=zone)
                    if item and item.product.external_id not in seen:
                        seen.add(item.product.external_id)
                        yield item


# ---------- normalización (función pura, fácil de testear) ----------

UNIT_MAP = {"kg": "kg", "l": "l", "ud": "ud", "docena": "docena", "100 ml": "100ml", "100 g": "100g"}


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return None


def parse_product(raw: dict[str, Any], category: str | None = None,
                  zone: str | None = None) -> ScrapedItem | None:
    """Convierte un producto del JSON de Mercadona al modelo común.

    Formato verificado con la API real (octubre 2026):
    - `unit_price` es el precio del envase; `reference_price` + `reference_format`
      el precio por kg / L.
    - `previous_unit_price` llega con espacios delante ("        5.04").
    - `price_decreased` viene siempre a false, aunque haya precio anterior: la
      bajada se detecta comparando `previous_unit_price` con `unit_price`.
    - El listado de categorías no trae marca ni EAN (solo /api/products/{id}/).
    - Los productos a granel (`selling_method` 1, p. ej. pescadería) traen un
      `unit_price` sin sentido (precio/kg x 99); su precio es el de 1 kg.
    """
    pi = raw.get("price_instructions") or {}
    if pi.get("selling_method") == 1:
        price = _num(pi.get("reference_price")) or _num(pi.get("bulk_price"))
    else:
        price = _num(pi.get("unit_price"))
    if price is None or not raw.get("id"):
        log.debug("Producto sin precio ignorado: %s", raw.get("id"))
        return None

    unit_raw = (pi.get("reference_format") or "").strip().lower()
    previous = _num(pi.get("previous_unit_price"))

    product = Product(
        supermarket="mercadona",
        external_id=str(raw["id"]),
        name=(raw.get("display_name") or "").strip(),
        brand=raw.get("brand"),
        category=category,
        packaging=raw.get("packaging"),
        size=_num(pi.get("unit_size")),
        size_unit=(pi.get("size_format") or None),
        ean=raw.get("ean"),
        image_url=raw.get("thumbnail"),
        url=raw.get("share_url"),
    )
    observation = PriceObservation(
        supermarket="mercadona",
        external_id=product.external_id,
        price=price,
        unit_price=_num(pi.get("reference_price")) or _num(pi.get("bulk_price")),
        unit=UNIT_MAP.get(unit_raw, unit_raw or None),
        previous_price=previous if previous and previous > price else None,
        on_promotion=bool(previous and previous > price),
        zone=zone,
    )
    return ScrapedItem(product=product, price=observation)

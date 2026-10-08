"""Contrato que cumple cada supermercado."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..models import ScrapedItem


class ZoneNotServed(Exception):
    """La cadena no tiene tienda online en ese código postal."""


class Provider(ABC):
    #: identificador corto, p. ej. "mercadona"
    name: str
    #: nombre para mostrar
    display_name: str

    @abstractmethod
    def zone_for_postal_code(self, postal_code: str) -> str:
        """Zona de precio que corresponde a un código postal.

        Lanza `ZoneNotServed` si la cadena no da servicio allí."""

    @abstractmethod
    def fetch_all(self, zone: str, limit_categories: int | None = None) -> Iterator[ScrapedItem]:
        """Recorre el catálogo de una zona y devuelve cada producto con su precio.

        `limit_categories` permite hacer pruebas rápidas sin bajar todo.
        """

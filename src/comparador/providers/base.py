"""Contrato que cumple cada supermercado."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..models import ScrapedItem


class Provider(ABC):
    #: identificador corto, p. ej. "mercadona"
    name: str
    #: nombre para mostrar
    display_name: str

    @abstractmethod
    def fetch_all(self, limit_categories: int | None = None) -> Iterator[ScrapedItem]:
        """Recorre el catálogo y devuelve cada producto con su precio actual.

        `limit_categories` permite hacer pruebas rápidas sin bajar todo.
        """

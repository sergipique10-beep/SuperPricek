"""Registro de providers. Para añadir un supermercado, crea su módulo
y regístralo aquí."""

from __future__ import annotations

from .base import Provider
from .mercadona import MercadonaProvider

PROVIDERS: dict[str, type[Provider]] = {
    MercadonaProvider.name: MercadonaProvider,
}


def get_provider(name: str, **kwargs) -> Provider:
    try:
        return PROVIDERS[name](**kwargs)
    except KeyError:
        available = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Supermercado desconocido '{name}'. Disponibles: {available}")

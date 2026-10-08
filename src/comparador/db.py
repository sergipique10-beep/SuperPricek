"""Conexión a Postgres: Supabase en producción, Docker en los tests."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

MADRID = ZoneInfo("Europe/Madrid")


def database_url() -> str:
    """DATABASE_URL del entorno o, en local, del archivo .env.

    Se quitan espacios y saltos de línea: al pegar la URL en el panel de
    Render o en un secret de GitHub es fácil que se cuele un Enter al final."""
    load_dotenv(Path.cwd() / ".env")
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("Falta DATABASE_URL: ponla en .env o en el entorno")
    return url


def connect(url: str | None = None) -> psycopg.Connection:
    """Conexión con filas como dict.

    prepare_threshold=None: el pooler de Supabase en modo transaction no
    admite sentencias preparadas."""
    return psycopg.connect(url or database_url(), row_factory=dict_row,
                           prepare_threshold=None)


def today() -> date:
    """Fecha de importación: el día en España, no en UTC."""
    return datetime.now(MADRID).date()

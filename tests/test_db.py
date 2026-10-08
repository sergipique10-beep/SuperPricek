import pytest
from conftest import assert_local

from comparador.db import today

TABLES = ["import_runs", "price_periods", "price_zones", "products",
          "province_zones", "provinces", "supermarkets"]


def test_migracion_crea_tablas_con_rls(db):
    rows = db.execute(
        "select relname, relrowsecurity from pg_class "
        "where relnamespace = 'public'::regnamespace and relkind = 'r' order by relname"
    ).fetchall()
    assert {r["relname"]: r["relrowsecurity"] for r in rows} == {t: True for t in TABLES}


def test_today_devuelve_fecha():
    assert today().year >= 2026


@pytest.mark.parametrize("url", [
    "postgresql://postgres.abc:x@aws-1-eu-central-1.pooler.supabase.com:5432/postgres",
    "postgresql://postgres:x@db.abc.supabase.co:5432/postgres",
])
def test_tests_se_niegan_a_usar_bases_remotas(url):
    with pytest.raises(RuntimeError):
        assert_local(url)

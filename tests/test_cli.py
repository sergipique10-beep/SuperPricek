import pytest
from conftest import FakeProvider, kill_connections, make_item

from comparador import cli
from comparador.cli import main


@pytest.fixture
def provider(db, db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    prov = FakeProvider(zones={"08001": "bcn1"}, catalogs={
        "bcn1": [make_item("1", 0.83, name="Leche semidesnatada Hacendado")]})
    monkeypatch.setattr(cli, "get_provider", lambda name, **kwargs: prov)
    return prov


def test_flujo_completo(provider, capsys):
    assert main(["province", "add", "barcelona", "Barcelona", "--postal-code", "08001"]) == 0
    assert "mercadona → bcn1" in capsys.readouterr().out
    assert main(["import", "mercadona"]) == 0
    assert "bcn1: 1 productos" in capsys.readouterr().out
    assert main(["search", "leche", "--province", "barcelona"]) == 0
    assert "Leche semidesnatada Hacendado" in capsys.readouterr().out
    assert main(["province", "list"]) == 0
    assert "bcn1" in capsys.readouterr().out


def test_importacion_con_error_devuelve_1(provider, capsys):
    main(["province", "add", "barcelona", "Barcelona", "--postal-code", "08001"])
    provider.failing = {"bcn1"}
    assert main(["import", "mercadona"]) == 1
    assert "ERROR" in capsys.readouterr().out


def test_sin_zonas_devuelve_1(provider, capsys):
    assert main(["import", "mercadona"]) == 1
    assert "province add" in capsys.readouterr().out


def test_dry_run_no_usa_la_base_de_datos(provider, capsys, monkeypatch):
    monkeypatch.setattr(cli, "connect", lambda *a, **k: pytest.fail("dry-run no debe conectar"))
    assert main(["import", "mercadona", "--zone", "bcn1", "--dry-run"]) == 0
    assert "Leche semidesnatada Hacendado" in capsys.readouterr().out


def test_codigo_postal_no_valido(provider, capsys):
    assert main(["province", "add", "barcelona", "Barcelona", "--postal-code", "8001"]) == 2
    assert "no válido" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["import", "mercadona", "--dry-run"],
    ["import", "mercadona", "--limit-categories", "2"],
    ["search", "leche"],
])
def test_argumentos_invalidos(argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_una_conexion_cortada_no_tumba_las_demas_zonas(db, db_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db_url)

    class CortaConexion(FakeProvider):
        def fetch_all(self, zone, limit_categories=None):
            if zone == "bcn1":
                kill_connections(db_url)  # se corta mientras se descarga bcn1
            yield from super().fetch_all(zone, limit_categories)

    prov = CortaConexion(zones={"08001": "bcn1", "28001": "mad3"}, catalogs={
        "bcn1": [make_item("1", 0.83)], "mad3": [make_item("1", 0.89)]})
    monkeypatch.setattr(cli, "get_provider", lambda name, **kwargs: prov)
    main(["province", "add", "barcelona", "Barcelona", "--postal-code", "08001"])
    main(["province", "add", "madrid", "Madrid", "--postal-code", "28001"])

    assert main(["import", "mercadona"]) == 1
    out = capsys.readouterr().out
    assert "bcn1: ERROR" in out and "mad3: 1 productos" in out

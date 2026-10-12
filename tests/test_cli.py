from routewise.auth import authenticate
from routewise.cli import main
from routewise.db import Repository


def run(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_create_tenant_prints_a_working_key(settings, capsys):
    code, out, _ = run(capsys, "create-tenant", "Acme Ltd", "--db", settings.db_path)
    assert code == 0
    lines = [line for line in out.splitlines() if line.startswith("API key: ")]
    assert len(lines) == 1
    key = lines[0].split(": ", 1)[1]
    tenant = authenticate(Repository(settings.db_path), key)
    assert tenant is not None
    assert tenant.name == "Acme Ltd"


def test_duplicate_and_invalid_names_fail(settings, capsys):
    assert run(capsys, "create-tenant", "Acme", "--db", settings.db_path)[0] == 0
    code, _, err = run(capsys, "create-tenant", "Acme", "--db", settings.db_path)
    assert code == 1
    assert "already exists" in err
    code, _, err = run(capsys, "create-tenant", "bad/name", "--db", settings.db_path)
    assert code == 2
    assert "tenant name" in err


def test_list_tenants(settings, capsys):
    assert "No tenants" in run(capsys, "list-tenants", "--db", settings.db_path)[1]
    run(capsys, "create-tenant", "Acme", "--db", settings.db_path)
    code, out, _ = run(capsys, "list-tenants", "--db", settings.db_path)
    assert code == 0
    assert "Acme" in out

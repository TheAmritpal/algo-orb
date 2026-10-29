import subprocess


def test_offline_migration_creates_four_tables():
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head", "--sql"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    for table in ("users", "sessions", "upstox_tokens", "order_audit"):
        assert f"CREATE TABLE {table}" in result.stdout

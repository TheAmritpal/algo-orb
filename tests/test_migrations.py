import importlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.core import config
from app.repositories import database

ROOT = Path(__file__).resolve().parent.parent


class MigrationTests(unittest.TestCase):
    def test_upgrade_generates_mysql_schema_without_credentials_or_connection(self):
        self.assertTrue(
            (ROOT / "alembic.ini").exists(), "Alembic configuration is missing"
        )
        output = io.StringIO()
        configuration = Config(str(ROOT / "alembic.ini"), output_buffer=output)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
            patch.object(config, "ENV_FILE", Path(directory) / ".env"),
        ):
            command.upgrade(configuration, "head", sql=True)
        sql = output.getvalue()
        self.assertIn("CREATE TABLE upstox_tokens", sql)
        self.assertIn(
            "client_id VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL", sql
        )
        self.assertIn(
            "user_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL", sql
        )
        self.assertIn("access_token TEXT NOT NULL", sql)
        self.assertIn("PRIMARY KEY (client_id, user_id)", sql)
        self.assertIn("DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP", sql)
        self.assertIn("ENGINE=InnoDB", sql)
        self.assertIn("INSERT INTO alembic_version", sql)

    def test_downgrade_generates_token_table_removal(self):
        self.assertTrue(
            (ROOT / "alembic.ini").exists(), "Alembic configuration is missing"
        )
        output = io.StringIO()
        command.downgrade(
            Config(str(ROOT / "alembic.ini"), output_buffer=output),
            "0001:base",
            sql=True,
        )
        self.assertIn("DROP TABLE upstox_tokens", output.getvalue())
        self.assertIn("DELETE FROM alembic_version", output.getvalue())

    def test_database_url_preserves_special_characters_in_credentials(self):
        self.assertTrue(
            hasattr(database, "get_sqlalchemy_url"),
            "SQLAlchemy URL configuration is missing",
        )
        values = {
            "host": "localhost",
            "port": 3307,
            "user": "user@name",
            "password": "p@ss:/%#word",
            "database": "algo_orb",
        }
        with patch("app.repositories.database.get_mysql_config", return_value=values):
            url = database.get_sqlalchemy_url()
        parsed = make_url(url.render_as_string(hide_password=False))
        self.assertEqual(parsed.password, "p@ss:/%#word")
        self.assertEqual(parsed.username, "user@name")
        self.assertEqual(parsed.drivername, "mysql+pymysql")
        self.assertEqual(parsed.port, 3307)

    def test_auth_schema_and_safe_downgrade(self):
        output = io.StringIO()
        cfg = Config(str(ROOT / "alembic.ini"), output_buffer=output)
        command.upgrade(cfg, "head", sql=True)
        sql = output.getvalue()
        for fragment in (
            "CREATE TABLE users",
            "CREATE TABLE sessions",
            "utf8mb4_bin",
            "UNIQUE (username)",
            "UNIQUE (token_hash)",
            "ON DELETE CASCADE",
            "CREATE INDEX ix_sessions_expires_at",
        ):
            self.assertIn(fragment, sql)
        output = io.StringIO()
        command.downgrade(
            Config(str(ROOT / "alembic.ini"), output_buffer=output),
            "0002:0001",
            sql=True,
        )
        sql = output.getvalue()
        self.assertLess(sql.index("DROP TABLE sessions"), sql.index("DROP TABLE users"))
        self.assertNotIn("DROP TABLE upstox_tokens", sql)


class PartialMigrationTests(unittest.TestCase):
    def setUp(self):
        from sqlalchemy.dialects import mysql

        self.mysql = mysql
        self.revision = importlib.import_module(
            "migrations.versions.0002_create_website_auth"
        )
        self.inspector = MagicMock()
        self.inspector.has_table.side_effect = lambda name: name == "users"
        self.inspector.get_columns.return_value = [
            {
                "name": "id",
                "type": mysql.INTEGER(),
                "nullable": False,
                "default": None,
                "autoincrement": True,
            },
            {
                "name": "username",
                "type": mysql.VARCHAR(128, charset="utf8mb4", collation="utf8mb4_bin"),
                "nullable": False,
                "default": None,
            },
            {
                "name": "password_hash",
                "type": mysql.VARCHAR(255),
                "nullable": False,
                "default": None,
            },
            {
                "name": "created_at",
                "type": mysql.TIMESTAMP(),
                "nullable": False,
                "default": "CURRENT_TIMESTAMP",
            },
            {
                "name": "updated_at",
                "type": mysql.TIMESTAMP(),
                "nullable": False,
                "default": "CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
            },
        ]
        self.inspector.get_pk_constraint.return_value = {"constrained_columns": ["id"]}
        self.inspector.get_unique_constraints.return_value = [
            {"column_names": ["username"]}
        ]
        self.inspector.get_foreign_keys.return_value = []
        self.inspector.get_table_options.return_value = {"mysql_engine": "InnoDB"}

    def test_partial_upgrade_validates_users_then_creates_sessions(self):
        from types import SimpleNamespace

        with (
            patch.object(
                self.revision.op,
                "get_context",
                return_value=SimpleNamespace(as_sql=False),
            ),
            patch.object(
                self.revision.op,
                "get_bind",
                return_value=SimpleNamespace(dialect=self.mysql.dialect()),
            ),
            patch.object(self.revision.sa, "inspect", return_value=self.inspector),
            patch.object(self.revision.op, "create_table") as create,
            patch.object(self.revision.op, "create_index"),
        ):
            self.revision.upgrade()
        self.assertEqual([c.args[0] for c in create.call_args_list], ["sessions"])

    def test_partial_upgrade_refuses_mismatched_schema_without_changes(self):
        from types import SimpleNamespace

        self.inspector.get_columns.return_value[2]["type"] = self.mysql.VARCHAR(20)
        with (
            patch.object(
                self.revision.op,
                "get_context",
                return_value=SimpleNamespace(as_sql=False),
            ),
            patch.object(
                self.revision.op,
                "get_bind",
                return_value=SimpleNamespace(dialect=self.mysql.dialect()),
            ),
            patch.object(self.revision.sa, "inspect", return_value=self.inspector),
            patch.object(self.revision.op, "create_table") as create,
            patch.object(self.revision.op, "create_index"),
            self.assertRaises(RuntimeError),
        ):
            self.revision.upgrade()
        create.assert_not_called()


class StrategyMigrationTests(unittest.TestCase):
    def test_strategy_schema_and_safe_downgrade(self):
        out = io.StringIO()
        cfg = Config(str(ROOT / "alembic.ini"), output_buffer=out)
        command.upgrade(cfg, "head", sql=True)
        sql = out.getvalue()
        for table in (
            "strategy_runs",
            "strategy_orders",
            "strategy_fills",
            "strategy_events",
        ):
            self.assertIn("CREATE TABLE " + table, sql)
        self.assertIn("uq_strategy_day", sql)
        self.assertIn("uq_strategy_trade", sql)
        self.assertIn("DECIMAL(20, 8)", sql)
        out = io.StringIO()
        command.downgrade(
            Config(str(ROOT / "alembic.ini"), output_buffer=out), "0003:0002", sql=True
        )
        self.assertNotIn("DROP TABLE users", out.getvalue())
        self.assertLess(
            out.getvalue().index("DROP TABLE strategy_fills"),
            out.getvalue().index("DROP TABLE strategy_orders"),
        )

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app import config, database

ROOT = Path(__file__).resolve().parent.parent


class MigrationTests(unittest.TestCase):
    def test_upgrade_generates_mysql_schema_without_credentials_or_connection(self):
        self.assertTrue((ROOT / "alembic.ini").exists(), "Alembic configuration is missing")
        output = io.StringIO()
        configuration = Config(str(ROOT / "alembic.ini"), output_buffer=output)
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True), patch.object(config, "ENV_FILE", Path(directory) / ".env"):
                command.upgrade(configuration, "head", sql=True)
        sql = output.getvalue()
        self.assertIn("CREATE TABLE upstox_tokens", sql)
        self.assertIn("client_id VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL", sql)
        self.assertIn("user_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL", sql)
        self.assertIn("access_token TEXT NOT NULL", sql)
        self.assertIn("PRIMARY KEY (client_id, user_id)", sql)
        self.assertIn("DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP", sql)
        self.assertIn("ENGINE=InnoDB", sql)
        self.assertIn("INSERT INTO alembic_version", sql)

    def test_downgrade_generates_token_table_removal(self):
        self.assertTrue((ROOT / "alembic.ini").exists(), "Alembic configuration is missing")
        output = io.StringIO()
        command.downgrade(Config(str(ROOT / "alembic.ini"), output_buffer=output), "0001:base", sql=True)
        self.assertIn("DROP TABLE upstox_tokens", output.getvalue())
        self.assertIn("DELETE FROM alembic_version", output.getvalue())

    def test_database_url_preserves_special_characters_in_credentials(self):
        self.assertTrue(hasattr(database, "get_sqlalchemy_url"), "SQLAlchemy URL configuration is missing")
        values = {"host": "localhost", "port": 3307, "user": "user@name", "password": "p@ss:/%#word", "database": "algo_orb"}
        with patch("app.database.get_mysql_config", return_value=values):
            url = database.get_sqlalchemy_url()
        parsed = make_url(url.render_as_string(hide_password=False))
        self.assertEqual(parsed.password, "p@ss:/%#word")
        self.assertEqual(parsed.username, "user@name")
        self.assertEqual(parsed.drivername, "mysql+pymysql")
        self.assertEqual(parsed.port, 3307)

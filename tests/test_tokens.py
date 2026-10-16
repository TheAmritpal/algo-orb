import importlib.util
import unittest
from unittest.mock import MagicMock, patch

import pymysql


class TokenStorageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("app.tokens"), "Token repository is missing")
        from app import tokens
        self.tokens = tokens
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value

    def test_token_is_parameterized_and_committed_after_write(self):
        with patch("app.tokens.get_connection", return_value=self.connection):
            self.tokens.save_token("client", "USER1", "token'with-quotes")
        statements = self.cursor.execute.call_args_list
        self.assertEqual(len(statements), 1, "Token writes must not create or alter tables")
        sql, params = statements[0].args
        self.assertIn("ON DUPLICATE KEY UPDATE", sql)
        self.assertNotIn("token'with-quotes", sql)
        self.assertEqual(params, ("client", "USER1", "token'with-quotes", "token'with-quotes"))
        self.connection.commit.assert_called_once()
        self.connection.close.assert_called_once()

    def test_failed_write_rolls_back_and_closes_connection(self):
        error = pymysql.err.OperationalError("write failed")
        self.cursor.execute.side_effect = error
        with patch("app.tokens.get_connection", return_value=self.connection):
            with self.assertRaises(pymysql.err.OperationalError):
                self.tokens.save_token("client", "USER1", "test-token")
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()

    def test_commit_failure_never_reports_success(self):
        self.connection.commit.side_effect = pymysql.err.OperationalError("commit failed")
        with patch("app.tokens.get_connection", return_value=self.connection):
            with self.assertRaises(pymysql.err.OperationalError):
                self.tokens.save_token("client", "USER1", "test-token")
        self.connection.close.assert_called_once()

import importlib.util
import unittest
from unittest.mock import MagicMock, patch

import pymysql


class TokenStorageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("app.repositories.tokens"),
            "Token repository is missing",
        )
        from app.repositories import tokens

        self.tokens = tokens
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value

    def test_token_is_parameterized_and_committed_after_write(self):
        with patch(
            "app.repositories.tokens.get_connection", return_value=self.connection
        ):
            self.tokens.save_token("client", "USER1", "token'with-quotes")
        statements = self.cursor.execute.call_args_list
        self.assertEqual(
            len(statements), 1, "Token writes must not create or alter tables"
        )
        sql, params = statements[0].args
        self.assertIn("ON DUPLICATE KEY UPDATE", sql)
        self.assertNotIn("token'with-quotes", sql)
        self.assertEqual(
            params, ("client", "USER1", "token'with-quotes", "token'with-quotes")
        )
        self.connection.commit.assert_called_once()
        self.connection.close.assert_called_once()

    def test_failed_write_rolls_back_and_closes_connection(self):
        error = pymysql.err.OperationalError("write failed")
        self.cursor.execute.side_effect = error
        with (
            patch(
                "app.repositories.tokens.get_connection", return_value=self.connection
            ),
            self.assertRaises(pymysql.err.OperationalError),
        ):
            self.tokens.save_token("client", "USER1", "test-token")
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()

    def test_commit_failure_never_reports_success(self):
        self.connection.commit.side_effect = pymysql.err.OperationalError(
            "commit failed"
        )
        with (
            patch(
                "app.repositories.tokens.get_connection", return_value=self.connection
            ),
            self.assertRaises(pymysql.err.OperationalError),
        ):
            self.tokens.save_token("client", "USER1", "test-token")
        self.connection.close.assert_called_once()

    def test_latest_token_lookup_is_scoped_and_never_exposes_token_in_repr(self):
        from app.repositories.tokens import get_latest_token

        self.cursor.fetchone.return_value = ("BROKER1", "private-token")
        with patch(
            "app.repositories.tokens.get_connection", return_value=self.connection
        ):
            result = get_latest_token("client'quoted")
        sql, params = self.cursor.execute.call_args.args
        self.assertEqual(params, ("client'quoted",))
        self.assertNotIn("client'quoted", sql)
        self.assertIn("ORDER BY updated_at DESC", sql)
        self.assertIn("LIMIT 1", sql)
        self.assertEqual(result.user_id, "BROKER1")
        self.assertEqual(result.access_token, "private-token")
        self.assertNotIn("private-token", repr(result))
        self.connection.close.assert_called_once()

    def test_no_saved_token_returns_none_and_closes_on_read_failure(self):
        from app.repositories.tokens import get_latest_token

        self.cursor.fetchone.return_value = None
        with patch(
            "app.repositories.tokens.get_connection", return_value=self.connection
        ):
            self.assertIsNone(get_latest_token("client"))
        self.connection.close.assert_called_once()
        self.connection.reset_mock()
        self.cursor.execute.side_effect = pymysql.OperationalError("private")
        with (
            patch(
                "app.repositories.tokens.get_connection", return_value=self.connection
            ),
            self.assertRaises(pymysql.OperationalError),
        ):
            get_latest_token("client")
        self.connection.close.assert_called_once()


class ExactTokenTests(unittest.TestCase):
    def test_exact_account_is_selected_without_latest_fallback(self):
        from app.repositories.tokens import get_token

        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("pinned", "secret-token")
        with patch("app.repositories.tokens.get_connection", return_value=connection):
            token = get_token("app", "pinned")
        self.assertEqual(token.user_id, "pinned")
        self.assertEqual(cursor.execute.call_args.args[1], ("app", "pinned"))
        self.assertNotIn("secret-token", repr(token))

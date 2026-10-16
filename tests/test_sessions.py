import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pymysql


class SessionTests(unittest.TestCase):
    def setUp(self):
        from app.repositories import sessions

        self.repo = sessions
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        p = patch.object(sessions, "get_connection", return_value=self.connection)
        p.start()
        self.addCleanup(p.stop)

    def test_creation_parameterizes_hash_and_prunes_expired(self):
        expiry = datetime(2030, 1, 1)
        self.cursor.fetchone.return_value = ("verified-hash",)
        self.repo.create_session(7, "a" * 64, "csrf", expiry, "verified-hash")
        statements = [c.args for c in self.cursor.execute.call_args_list]
        self.assertTrue(
            any("UTC_TIMESTAMP()" in c[0] and "DELETE" in c[0] for c in statements)
        )
        insert = next(c for c in statements if "INSERT" in c[0])
        self.assertEqual(insert[1], (7, "a" * 64, "csrf", expiry))
        self.assertNotIn("a" * 64, insert[0])
        self.connection.commit.assert_called_once()
        self.connection.close.assert_called_once()

    def test_lookup_filters_expiry_and_returns_identity(self):
        self.cursor.fetchone.return_value = (7, "admin", "csrf", datetime(2030, 1, 1))
        result = self.repo.find_session("a" * 64)
        self.assertEqual(result.user_id, 7)
        self.assertEqual(result.username, "admin")
        self.assertIn(
            "expires_at > UTC_TIMESTAMP()", self.cursor.execute.call_args.args[0]
        )
        self.assertEqual(self.cursor.execute.call_args.args[1], ("a" * 64,))
        self.connection.close.assert_called_once()

    def test_missing_session_and_deletion(self):
        self.cursor.fetchone.return_value = None
        self.assertIsNone(self.repo.find_session("missing"))
        self.repo.delete_session("expired-hash")
        self.assertEqual(self.cursor.execute.call_args.args[1], ("expired-hash",))
        self.assertIn("DELETE", self.cursor.execute.call_args.args[0])

    def test_failed_write_rolls_back_and_closes(self):
        self.connection.commit.side_effect = pymysql.OperationalError("private")
        with self.assertRaises(pymysql.OperationalError):
            self.repo.delete_session("hash")
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()

    def test_password_rotation_rejects_stale_login_before_inserting(self):
        self.cursor.fetchone.return_value = ("rotated-hash",)
        with self.assertRaises(self.repo.AuthenticationChanged):
            self.repo.create_session(
                7, "a" * 64, "csrf", datetime(2030, 1, 1), "old-hash"
            )
        statements = [c.args[0] for c in self.cursor.execute.call_args_list]
        self.assertTrue(any("FOR UPDATE" in sql for sql in statements))
        self.assertFalse(any("INSERT" in sql for sql in statements))
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()
        self.connection.close.assert_called_once()

    def test_deleted_user_rejects_stale_login(self):
        self.cursor.fetchone.return_value = None
        with self.assertRaises(self.repo.AuthenticationChanged):
            self.repo.create_session(
                7, "a" * 64, "csrf", datetime(2030, 1, 1), "old-hash"
            )
        self.connection.commit.assert_not_called()

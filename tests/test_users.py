import unittest
from unittest.mock import MagicMock, patch

import pymysql


class UsersTests(unittest.TestCase):
    def setUp(self):
        from app.repositories import users

        self.repo = users
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.connection_patch = patch.object(
            users, "get_connection", return_value=self.connection
        )
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        self.cursor.fetchone.return_value = (1,)

    def test_user_lookup_parameterizes_and_closes(self):
        self.cursor.fetchone.return_value = (7, "admin' OR 1=1", "encoded")
        user = self.repo.find_user("admin' OR 1=1")
        self.assertEqual(
            (user.id, user.username, user.password_hash),
            (7, "admin' OR 1=1", "encoded"),
        )
        sql, params = self.cursor.execute.call_args.args
        self.assertNotIn("admin' OR 1=1", sql)
        self.assertEqual(params, ("admin' OR 1=1",))
        self.connection.close.assert_called_once()

    def test_sync_creates_first_account_under_database_lock(self):
        self.cursor.fetchall.return_value = ()
        self.cursor.lastrowid = 7
        self.assertEqual(self.repo.sync_admin("admin", "encoded"), 7)
        statements = [call.args for call in self.cursor.execute.call_args_list]
        self.assertIn("GET_LOCK", statements[0][0])
        self.assertTrue(
            any(
                "INSERT INTO users" in s[0] and s[1] == ("admin", "encoded")
                for s in statements
            )
        )
        self.assertTrue(
            any("DELETE FROM sessions" in s[0] and s[1] == (7,) for s in statements)
        )
        self.assertIn("RELEASE_LOCK", statements[-1][0])
        self.connection.commit.assert_called_once()
        self.connection.close.assert_called_once()

    def test_sync_updates_and_revokes_in_same_transaction(self):
        self.cursor.fetchall.return_value = ((7, "admin"),)
        self.assertEqual(self.repo.sync_admin("admin", "new-hash"), 7)
        statements = [call.args for call in self.cursor.execute.call_args_list]
        self.assertTrue(
            any("UPDATE users" in s[0] and s[1] == ("new-hash", 7) for s in statements)
        )
        self.assertFalse(any("INSERT INTO users" in s[0] for s in statements))
        self.connection.begin.assert_called_once()
        self.connection.commit.assert_called_once()

    def test_different_username_refuses_without_writing(self):
        self.cursor.fetchall.return_value = ((7, "existing"),)
        with self.assertRaises(ValueError):
            self.repo.sync_admin("different", "new-hash")
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()
        self.assertFalse(
            any(
                "INSERT" in c.args[0] or "UPDATE users" in c.args[0]
                for c in self.cursor.execute.call_args_list
            )
        )

    def test_lock_timeout_and_commit_failure_close_and_fail(self):
        self.cursor.fetchone.return_value = (0,)
        with self.assertRaises(RuntimeError):
            self.repo.sync_admin("admin", "encoded")
        self.connection.commit.assert_not_called()
        self.connection.close.assert_called_once()
        self.connection.reset_mock()
        self.cursor.fetchone.return_value = (1,)
        self.cursor.fetchall.return_value = ()
        self.connection.commit.side_effect = pymysql.OperationalError("private secret")
        with self.assertRaises(pymysql.OperationalError):
            self.repo.sync_admin("admin", "encoded")
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()
        self.assertIn("RELEASE_LOCK", self.cursor.execute.call_args.args[0])

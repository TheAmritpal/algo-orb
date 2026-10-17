"""Opt-in tests against a migrated MySQL DB; unique fixtures are removed afterward."""

import os
import secrets
import unittest


@unittest.skipUnless(
    os.environ.get("ORB_MYSQL_TESTS") == "1",
    "Set ORB_MYSQL_TESTS=1 to test the configured MySQL database",
)
class MySQLEnvironmentTests(unittest.TestCase):
    def setUp(self):
        from app.repositories.database import get_connection

        self.connection = get_connection()
        self.client_id = "test-" + secrets.token_hex(12)
        self.addCleanup(self.cleanup_records)

    def cleanup_records(self):
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "DELETE e FROM sandbox_test_events e JOIN sandbox_order_tests t ON e.test_id=t.id WHERE t.client_id=%s",
                    (self.client_id,),
                )
                cursor.execute(
                    "DELETE FROM sandbox_order_tests WHERE client_id=%s",
                    (self.client_id,),
                )
                cursor.execute(
                    "DELETE FROM upstox_tokens WHERE client_id=%s", (self.client_id,)
                )
            self.connection.commit()
        finally:
            self.connection.close()

    def test_cancel_ack_remains_unresolved_and_blocks_new_probe(self):
        from app.repositories.sandbox_tests import SandboxTestRepository

        repo = SandboxTestRepository()
        test_id = repo.start(self.client_id, "test-account", {"tag": "regression"})
        repo.update(test_id, "cancel_acknowledged", "test-order")
        with self.assertRaises(RuntimeError):
            repo.start(self.client_id, "test-account", {"tag": "must-block"})

    def test_tokens_with_same_app_and_account_are_isolated_by_environment(self):
        from app.repositories.tokens import get_latest_token, get_token, save_token

        save_token(self.client_id, "test-account", "fake-live-token")
        save_token(
            self.client_id, "test-account", "fake-sandbox-token", environment="sandbox"
        )
        self.assertEqual(
            get_token(self.client_id, "test-account").access_token, "fake-live-token"
        )
        self.assertEqual(
            get_token(
                self.client_id, "test-account", environment="sandbox"
            ).access_token,
            "fake-sandbox-token",
        )
        self.assertEqual(
            get_latest_token(self.client_id).access_token, "fake-live-token"
        )
        self.assertEqual(
            get_latest_token(self.client_id, environment="sandbox").access_token,
            "fake-sandbox-token",
        )

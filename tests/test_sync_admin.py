import contextlib
import io
import unittest
from unittest.mock import patch


class SyncAdminTests(unittest.TestCase):
    def test_cli_uses_hash_and_never_prints_credentials(self):
        from app.cli import sync_admin

        with (
            patch.object(
                sync_admin,
                "get_admin_credentials",
                return_value=("admin", "sensitive-password"),
            ),
            patch.object(sync_admin, "sync_admin", return_value=7) as sync,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(sync_admin.main(), 0)
        self.assertNotIn("sensitive-password", sync.call_args.args[1])
        self.assertNotIn("sensitive-password", output.getvalue())

    def test_invalid_config_and_database_errors_are_sanitized(self):
        from app.cli import sync_admin

        for exc in (
            ValueError("private-password"),
            OSError("private-password"),
            RuntimeError("private-password"),
        ):
            with (
                patch.object(sync_admin, "get_admin_credentials", side_effect=exc),
                patch.object(sync_admin, "sync_admin") as sync,
                contextlib.redirect_stderr(io.StringIO()) as output,
            ):
                self.assertEqual(sync_admin.main(), 1)
            sync.assert_not_called()
            self.assertNotIn("private-password", output.getvalue())

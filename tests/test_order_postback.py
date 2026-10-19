import asyncio
import json
import unittest
from unittest.mock import patch

import pymysql

from tests.http import asgi_request


class OrderPostbackTests(unittest.TestCase):
    def request(self, payload=None, body=None):
        return asyncio.run(
            asgi_request(
                "/webhooks/upstox/orders",
                method="POST",
                body=body if body is not None else json.dumps(payload).encode(),
                extra_headers=[(b"content-type", b"application/json")],
            )
        )

    def test_public_endpoint_accepts_order_without_login(self):
        with patch("app.repositories.order_updates.get_connection"):
            status, _, _ = self.request(
                {
                    "update_type": "order",
                    "order_id": "123",
                    "user_id": "U1",
                    "status": "complete",
                }
            )
        self.assertEqual(status, 200)

    def test_rejects_malformed_or_incomplete_events(self):
        for body in (
            b"{",
            b"[]",
            b"null",
            b"{}",
            b'{"update_type":"access_token","access_token":"secret"}',
            b'{"update_type":"order","order_id":"123"}',
        ):
            with self.subTest(body=body):
                self.assertEqual(self.request(body=body)[0], 400)

    def test_rejects_large_body(self):
        self.assertEqual(self.request(body=b" " * 65537)[0], 413)

    def test_records_before_acknowledging_and_does_not_store_unknown_fields(self):
        with patch("app.repositories.order_updates.get_connection") as connect:
            connection = connect.return_value
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.rowcount = 1
            status, _, _ = self.request(
                {
                    "update_type": "order",
                    "order_id": "123",
                    "userId": "U1",
                    "status": "complete",
                    "access_token": "SECRET",
                    "unexpected": {"password": "SECRET"},
                }
            )
            self.assertEqual(status, 200)
            connection.commit.assert_called_once()
            params = cursor.execute.call_args.args[1]
            self.assertNotIn("SECRET", str(params))
            self.assertIn("complete", str(params))
            connection.close.assert_called_once()

    def test_storage_failure_returns_503_and_rolls_back(self):
        with patch("app.repositories.order_updates.get_connection") as connect:
            connection = connect.return_value
            connection.commit.side_effect = pymysql.OperationalError(2006, "SECRET")
            status, _, body = self.request(
                {
                    "update_type": "order",
                    "order_id": "123",
                    "user_id": "U1",
                    "status": "complete",
                }
            )
            self.assertEqual(status, 503)
            self.assertNotIn(b"SECRET", body)
            connection.rollback.assert_called_once()
            connection.close.assert_called_once()

    def test_gtt_event_is_accepted(self):
        with patch("app.repositories.order_updates.get_connection"):
            self.assertEqual(
                self.request(
                    {
                        "update_type": "gtt_order",
                        "gtt_order_id": "GTT-123",
                        "rules": [
                            {
                                "strategy": "ENTRY",
                                "status": "TRIGGERED",
                                "order_id": "123",
                            }
                        ],
                    }
                )[0],
                200,
            )

    def test_duplicate_deliveries_have_same_key_despite_json_key_order(self):
        with patch("app.repositories.order_updates.get_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            payload = {
                "update_type": "order",
                "order_id": "123",
                "user_id": "U1",
                "status": "complete",
            }
            self.assertEqual(self.request(payload)[0], 200)
            first = cursor.execute.call_args.args[1][0]
            self.assertEqual(
                self.request(dict(reversed(list(payload.items()))))[0], 200
            )
            self.assertEqual(first, cursor.execute.call_args.args[1][0])
            self.assertIn("ON DUPLICATE KEY UPDATE", cursor.execute.call_args.args[0])

    def test_gtt_uses_parent_identifier_even_with_order_id(self):
        with patch("app.repositories.order_updates.get_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            status, _, _ = self.request(
                {
                    "update_type": "gtt_order",
                    "gtt_order_id": "GTT-parent",
                    "order_id": "child-order",
                    "rules": [{"strategy": "ENTRY", "status": "TRIGGERED"}],
                }
            )
            self.assertEqual(status, 200)
            self.assertEqual(cursor.execute.call_args.args[1][2], "GTT-parent")

    def test_invalid_types_and_nonfinite_numbers_are_rejected(self):
        cases = [
            {"quantity": True},
            {"price": float("inf")},
            {"order_id": "x" * 101},
            {"user_id": "U1", "userId": "OTHER"},
            {"status": ""},
        ]
        for change in cases:
            payload = {
                "update_type": "order",
                "order_id": "123",
                "user_id": "U1",
                "status": "complete",
                **change,
            }
            with self.subTest(change=change):
                self.assertEqual(self.request(payload)[0], 400)

    def test_wrong_content_type_is_rejected(self):
        response = asyncio.run(
            asgi_request("/webhooks/upstox/orders", method="POST", body=b"{}")
        )
        self.assertEqual(response[0], 415)


class PostbackMigrationTests(unittest.TestCase):
    def test_mysql_inbox_schema_has_unique_hash_and_safe_downgrade(self):
        import io

        from alembic import command
        from alembic.config import Config

        output = io.StringIO()
        command.upgrade(
            Config("alembic.ini", output_buffer=output), "0006:0007", sql=True
        )
        schema = output.getvalue()
        self.assertIn("CREATE TABLE upstox_order_updates", schema)
        self.assertIn("UNIQUE (event_hash)", schema)
        self.assertIn("payload JSON NOT NULL", schema)
        output = io.StringIO()
        command.downgrade(
            Config("alembic.ini", output_buffer=output), "0007:0006", sql=True
        )
        self.assertIn("DROP TABLE upstox_order_updates", output.getvalue())
        self.assertNotIn("DROP TABLE strategy_", output.getvalue())

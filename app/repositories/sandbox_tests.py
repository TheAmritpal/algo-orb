"""Persist sandbox requests and sanitized acknowledgement history."""

import json

from app.repositories.database import get_connection


class SandboxTestRepository:
    def start(self, client_id, user_id, request):
        connection = get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM sandbox_order_tests WHERE client_id=%s AND user_id=%s AND status IN ('submission_pending','submission_uncertain','place_acknowledged','cancel_pending','cancellation_uncertain','cancel_acknowledged') LIMIT 1 FOR UPDATE",
                    (client_id, user_id),
                )
                if cursor.fetchone():
                    raise RuntimeError(
                        "Resolve the previous sandbox test before placing another order"
                    )
                cursor.execute(
                    "INSERT INTO sandbox_order_tests (client_id,user_id,request,status) VALUES (%s,%s,%s,%s)",
                    (client_id, user_id, json.dumps(request), "submission_pending"),
                )
                test_id = cursor.lastrowid
                cursor.execute(
                    "INSERT INTO sandbox_test_events (test_id,status) VALUES (%s,%s)",
                    (test_id, "submission_pending"),
                )
            connection.commit()
            return test_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update(self, test_id, status, order_id):
        connection = get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE sandbox_order_tests SET status=%s,order_id=%s WHERE id=%s",
                    (status, order_id, test_id),
                )
                cursor.execute(
                    "INSERT INTO sandbox_test_events (test_id,status,order_id) VALUES (%s,%s,%s)",
                    (test_id, status, order_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

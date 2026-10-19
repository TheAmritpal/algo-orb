"""Durable, deduplicated receipt of untrusted Upstox postbacks."""

import hashlib
import json

from app.repositories.database import get_connection


def save_update(payload: dict) -> None:
    serialized = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    event_hash = hashlib.sha256(serialized.encode()).hexdigest()
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO upstox_order_updates
                   (event_hash, update_type, broker_order_id, user_id, payload)
                   VALUES (%s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE event_hash = VALUES(event_hash)""",
                (
                    event_hash,
                    payload["update_type"],
                    payload[
                        "order_id"
                        if payload["update_type"] == "order"
                        else "gtt_order_id"
                    ],
                    payload.get("user_id"),
                    serialized,
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

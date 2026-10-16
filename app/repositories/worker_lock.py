"""Dedicated non-reconnecting MySQL advisory lock for a strategy worker."""

import hashlib

from app.repositories.database import get_connection


class WorkerLock:
    def __init__(self, client_id, user_id, mode="paper"):
        self.name = (
            "orb:"
            + hashlib.sha256(f"{client_id}:{user_id}:{mode}".encode()).hexdigest()[:56]
        )
        self.connection = None
        self.owner = None

    def acquire(self):
        self.connection = get_connection()
        with self.connection.cursor() as c:
            c.execute("SELECT GET_LOCK(%s, 0)", (self.name,))
            if c.fetchone()[0] != 1:
                return False
            c.execute("SELECT CONNECTION_ID()")
            self.owner = c.fetchone()[0]
        return True

    def assert_owned(self):
        if self.connection is None or self.owner is None:
            raise RuntimeError("Worker ownership unavailable")
        with self.connection.cursor() as c:
            c.execute("SELECT CONNECTION_ID(), IS_USED_LOCK(%s)", (self.name,))
            own, held = c.fetchone()
        if own != self.owner or held != self.owner:
            raise RuntimeError("Worker ownership lost")

    def close(self):
        if self.connection is not None:
            try:
                if self.owner is not None:
                    with self.connection.cursor() as c:
                        c.execute("SELECT RELEASE_LOCK(%s)", (self.name,))
            finally:
                self.connection.close()
                self.connection = None
                self.owner = None

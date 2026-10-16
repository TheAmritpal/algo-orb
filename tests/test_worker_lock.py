import unittest
from unittest.mock import MagicMock, patch

from app.repositories.worker_lock import WorkerLock


class LockTests(unittest.TestCase):
    def test_lock_owned_by_original_connection_only(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [(1,), (42,), (42, 42), (42, 43)]
        with patch("app.repositories.worker_lock.get_connection", return_value=conn):
            lock = WorkerLock("a" * 255, "u")
            self.assertLessEqual(len(lock.name), 64)
            self.assertTrue(lock.acquire())
            lock.assert_owned()
            with self.assertRaises(RuntimeError):
                lock.assert_owned()
            lock.close()
        conn.close.assert_called_once()

    def test_other_worker_and_lost_connection_fail_closed(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = (0,)
        with patch("app.repositories.worker_lock.get_connection", return_value=conn):
            lock = WorkerLock("a", "u")
            self.assertFalse(lock.acquire())
            with self.assertRaises(RuntimeError):
                lock.assert_owned()
            lock.close()

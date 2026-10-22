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

    def test_renewal_waits_for_running_iteration_to_release_lease(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [(1,), (42,)]
        with patch("app.repositories.worker_lock.get_connection", return_value=conn):
            lock = WorkerLock("app", "u", "credentials")
            self.assertTrue(lock.acquire(timeout=3))
        self.assertEqual(
            cur.execute.call_args_list[0].args,
            ("SELECT GET_LOCK(%s, %s)", (lock.name, 3)),
        )

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


class ExecutionOwnershipTests(unittest.TestCase):
    def test_lost_credential_lease_prevents_broker_mutation(self):
        from app.repositories.worker_lock import ExecutionOwnership

        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        # Acquire worker, acquire credentials, check worker, discover lost lease.
        cur.fetchone.side_effect = [(1,), (42,), (1,), (42,), (42, 42), (42, 99)]
        with patch("app.repositories.worker_lock.get_connection", return_value=conn):
            worker = WorkerLock("app", "u", "paper")
            credentials = WorkerLock("app", "u", "credentials")
            self.assertNotEqual(worker.name, credentials.name)
            self.assertTrue(worker.acquire())
            self.assertTrue(credentials.acquire())
            with self.assertRaises(RuntimeError):
                ExecutionOwnership(worker, credentials).assert_owned()

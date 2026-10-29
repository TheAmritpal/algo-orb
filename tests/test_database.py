import pytest

from app.repositories.database import sqlalchemy_url, transaction


def test_sqlalchemy_url_encodes_credentials(settings):
    url = sqlalchemy_url(settings)
    assert url.startswith("mysql+pymysql://")
    assert "algo_orb" in url


def test_transaction_rolls_back_and_closes():
    class FakeConn:
        committed = False
        rolled_back = False
        closed = False

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    conn = FakeConn()
    with pytest.raises(RuntimeError), transaction(connect=lambda _settings: conn):
        raise RuntimeError("boom")
    assert conn.rolled_back
    assert conn.closed
    assert not conn.committed

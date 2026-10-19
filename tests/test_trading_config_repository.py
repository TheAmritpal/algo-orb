import unittest
from unittest.mock import patch

from app.repositories.trading_config import (
    TradingConfig,
    get_trading_config,
    save_trading_config,
)


class TradingConfigRepositoryTests(unittest.TestCase):
    def test_database_values_and_missing_or_corrupt_rows(self):
        with patch("app.repositories.trading_config.get_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            for row, expected in [
                (("paper", 0), TradingConfig()),
                (("live", 1), TradingConfig("live", True)),
            ]:
                cursor.fetchone.return_value = row
                self.assertEqual(get_trading_config(), expected)
            for row in (None, ("sandbox", 0), ("live", 2)):
                cursor.fetchone.return_value = row
                with self.assertRaises(ValueError):
                    get_trading_config()
            self.assertEqual(connect.return_value.close.call_count, 5)

    def test_save_commits_values_and_rolls_back_missing_row(self):
        with patch("app.repositories.trading_config.get_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (1,)
            save_trading_config(TradingConfig("live", True))
            self.assertEqual(cursor.execute.call_args_list[0].args[1], ("live", True))
            connect.return_value.commit.assert_called_once()
            cursor.fetchone.return_value = None
            with self.assertRaises(ValueError):
                save_trading_config(TradingConfig())
            connect.return_value.rollback.assert_called_once()

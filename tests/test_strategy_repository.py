import unittest
from datetime import date
from decimal import Decimal as D
from unittest.mock import MagicMock,patch
from app.repositories.strategy import StrategyRepository, reduce_fills, run_from_row
from app.strategies.models import StrategyRun,RunKey,OrderIntent,OrderRequest,BrokerOrder,Fill
from tests.test_strategy_rules import at

class RepositoryTests(unittest.TestCase):
    def test_repeated_trade_ids_do_not_double_count_and_sell_cannot_exceed_buy(self):
        run=StrategyRun(1,RunKey("app","u","reliance_orb","paper",date(2026,10,16)))
        buy=OrderIntent(1,1,"entry",OrderRequest("b","opt","D","BUY","LIMIT",500,D("100")),at(),fills=[Fill("t1","b",250,D("100")),Fill("t1","b",250,D("100")),Fill("t2","b",250,D("102"))])
        sell=OrderIntent(2,1,"exit",OrderRequest("s","opt","D","SELL","LIMIT",500,D("120")),at(),fills=[Fill("t3","s",250,D("120"))])
        result=reduce_fills(run,[buy,sell])
        self.assertEqual((result.filled_quantity,result.remaining_quantity,result.entry_average),(500,250,D("101")))
        sell.fills.append(Fill("t4","s",500,D("120")))
        with self.assertRaises(ValueError): reduce_fills(run,[buy,sell])
    def test_entry_reservation_uses_locked_row_and_refuses_reserved_or_stale(self):
        conn=MagicMock();cursor=conn.cursor.return_value.__enter__.return_value
        row=dict(id=1,client_id="a",user_id="u",strategy="reliance_orb",mode="paper",exchange_date=date(2026,10,16),version=2,state="rejected",entry_reserved=1)
        cursor.fetchone.return_value=row
        request=OrderRequest("b","opt","D","BUY","LIMIT",500,D("100"))
        with patch("app.repositories.strategy.get_connection",return_value=conn):
            self.assertIsNone(StrategyRepository().reserve_entry(1,2,request))
            self.assertIsNone(StrategyRepository().reserve_entry(1,1,request))
        statements=[c.args[0] for c in cursor.execute.call_args_list]
        self.assertTrue(any("FOR UPDATE" in s for s in statements))
        self.assertFalse(any("INSERT INTO strategy_orders" in s for s in statements))
    def test_database_errors_rollback_and_close(self):
        conn=MagicMock();conn.cursor.return_value.__enter__.side_effect=RuntimeError("secret")
        with patch("app.repositories.strategy.get_connection",return_value=conn),self.assertRaises(RuntimeError):
            StrategyRepository().get_run(1)
        conn.rollback.assert_called_once();conn.close.assert_called_once()
    def test_unknown_columns_are_not_accepted_in_transition(self):
        with self.assertRaises(ValueError): StrategyRepository().transition(1,0,"open",{"access_token":"bad"})

import unittest
from decimal import Decimal as D
from dataclasses import replace
from app.services.paper_broker import PaperBroker
from app.strategies.models import OrderRequest,OrderIntent,Quote
from tests.test_strategy_rules import at
class PaperTests(unittest.TestCase):
    def test_fill_at_ask_then_stop_at_bid_and_restart_replay(self):
        q=Quote("opt",at(),D("99"),D("100"),D("100"))
        broker=PaperBroker(lambda key:q)
        req=OrderRequest("entry","opt","D","BUY","LIMIT",500,D("100"))
        bought=broker.submit(req)
        self.assertEqual(bought.fills[0].price,D("100"))
        intent=OrderIntent(1,1,"entry",req,at(),bought.broker_id,bought.status,list(bought.fills))
        new=PaperBroker(lambda key:q);new.restore([intent])
        self.assertEqual(new.reconcile(intent),bought)
        self.assertEqual(new.positions()[0].quantity,500)
        stop=OrderRequest("stop","opt","D","SELL","SL",500,D("89"),D("90"))
        pending=new.submit(stop)
        self.assertEqual(pending.status,"trigger_pending")
        q=replace(q,bid=D("90"),ask=D("91"),last=D("90"))
        si=OrderIntent(2,1,"stop",stop,at(),pending.broker_id,pending.status)
        self.assertEqual(new.reconcile(si).filled_quantity,500)
        self.assertEqual(new.positions()[0].quantity,0)
    def test_unknown_paper_intent_is_not_resubmitted_on_restore(self):
        q=Quote("opt",at(),D("99"),D("100"),D("100"));broker=PaperBroker(lambda key:q)
        req=OrderRequest("entry","opt","D","BUY","LIMIT",500,D("100"))
        i=OrderIntent(1,1,"entry",req,at())
        broker.restore([i]);self.assertIsNone(broker.reconcile(i))

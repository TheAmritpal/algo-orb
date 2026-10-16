"""Deterministic simulated orders, clearly separated from broker fills."""
from decimal import Decimal
from app.strategies.models import BrokerOrder,BrokerPosition,Fill,TERMINAL

class PaperBroker:
    def __init__(self,quote_provider):
        self.quote_provider=quote_provider;self.orders={};self.requests={}
    def restore(self,intents):
        for i in intents:
            if i.broker_id:
                order=BrokerOrder(i.broker_id,i.request.tag,i.status,i.request.quantity,sum(f.quantity for f in i.fills),tuple(i.fills))
                # Confirmed evidence must not overwrite newer paper observations.
                if i.broker_id not in self.orders:self.orders[i.broker_id]=order
                self.requests[i.broker_id]=i.request
    def _evaluate(self,order):
        if order.status in TERMINAL:return order
        request=self.requests[order.broker_id]
        q=self.quote_provider(request.instrument)
        if q is None:return order
        triggered=request.order_type!="SL" or q.bid<=request.trigger_price
        price=q.ask if request.side=="BUY" else q.bid
        executable=price<=request.price if request.side=="BUY" else price>=request.price
        if not triggered or not executable:return order
        # Filled once, at the currently executable side of the quote.
        quantity=request.quantity-order.filled_quantity
        fills=order.fills+(Fill("paper-"+request.tag,order.broker_id,quantity,price),) if quantity else order.fills
        result=BrokerOrder(order.broker_id,request.tag,"complete",request.quantity,request.quantity,fills)
        self.orders[order.broker_id]=result
        return result
    def submit(self,request):
        broker_id="paper-"+request.tag
        if broker_id in self.orders:raise ValueError("Duplicate paper submission")
        self.requests[broker_id]=request
        result=BrokerOrder(broker_id,request.tag,"trigger_pending" if request.order_type=="SL" else "open",request.quantity,0)
        self.orders[broker_id]=result
        return self._evaluate(result)
    def reconcile(self,intent):
        order=self.orders.get(intent.broker_id or "paper-"+intent.request.tag)
        return self._evaluate(order) if order else None
    def cancel(self,order_id):
        order=self._evaluate(self.orders[order_id])
        if order.status not in TERMINAL:
            order=BrokerOrder(order.broker_id,order.tag,"cancelled",order.requested_quantity,order.filled_quantity,order.fills)
            self.orders[order_id]=order
        return order
    def positions(self):
        quantities={}
        for key,o in self.orders.items():
            r=self.requests[key]
            quantities[r.instrument]=quantities.get(r.instrument,0)+(o.filled_quantity if r.side=="BUY" else -o.filled_quantity)
        return [BrokerPosition(k,"D",v) for k,v in quantities.items()]
    def available_funds(self):return Decimal("1000000000")
    def supports_protective_stop(self,contract):return True
    def close(self):pass

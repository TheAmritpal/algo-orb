"""Official SDK execution boundary. Requests never retry on uncertainty."""

from decimal import ROUND_DOWN

import upstox_client as sdk

from app.services.trading_market import TradingUnavailable, decimal, sdk_data
from app.strategies.models import BrokerOrder, BrokerPosition, Fill
from app.strategies.rules import PREMIUM_STOP_DISTANCE, premium_levels


class EntryValidationError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason.replace("_", " "))


def validate_entry(contract, quote, now, settings, funds):
    if quote.instrument_key != contract.instrument_key or not quote.fresh(
        now, settings.quote_max_age_seconds
    ):
        raise EntryValidationError("quote_unavailable_or_stale")
    premium_levels(quote.ask, contract.tick_size)
    if quote.ask % contract.tick_size or quote.bid % contract.tick_size:
        raise EntryValidationError("invalid_tick_price")
    limits = settings.limits
    if settings.mode == "live" and (not settings.live_enabled or limits is None):
        raise EntryValidationError("live_entry_disabled")
    if limits:
        checks = (
            (
                limits.max_premium is not None and quote.ask > limits.max_premium,
                "premium_limit",
            ),
            (
                limits.max_notional is not None
                and quote.ask * contract.quantity > limits.max_notional,
                "notional_limit",
            ),
            (
                PREMIUM_STOP_DISTANCE * contract.quantity > limits.max_planned_loss,
                "planned_loss_limit",
            ),
            (quote.ask - quote.bid > limits.max_spread, "spread_limit"),
        )
        for exceeded, reason in checks:
            if exceeded:
                raise EntryValidationError(reason)
    if funds < quote.ask * contract.quantity:
        raise EntryValidationError("insufficient_funds")
    return quote.ask


def bounded_sell(quote, contract, slippage):
    price = ((quote.bid - slippage) / contract.tick_size).to_integral_value(
        rounding=ROUND_DOWN
    ) * contract.tick_size
    if price <= 0:
        raise ValueError("No positive bounded exit price")
    return price


class Broker:
    def __init__(self, client, orders=None, protective_stop_verified=False):
        self.client = client
        self.orders = orders or sdk.OrderApi(client)
        self.portfolio = sdk.PortfolioApi(client)
        self.user = sdk.UserApi(client)
        self.protective_stop_verified = protective_stop_verified
        self.profile = None

    def submit(self, request):
        body = sdk.PlaceOrderRequest(
            quantity=request.quantity,
            product="D",
            validity="DAY",
            price=float(request.price),
            tag=request.tag,
            instrument_token=request.instrument,
            order_type=request.order_type,
            transaction_type=request.side,
            disclosed_quantity=0,
            trigger_price=float(request.trigger_price),
            is_amo=False,
        )
        data = sdk_data(self.orders.place_order, body, "2.0")
        if not getattr(data, "order_id", None):
            raise TradingUnavailable("Order acknowledgement uncertain")
        return BrokerOrder(data.order_id, request.tag, "unknown", request.quantity, 0)

    def reconcile(self, intent):
        data = sdk_data(self.orders.get_order_book, "2.0")
        matches = [
            o
            for o in data
            if (
                o.order_id == intent.broker_id
                if intent.broker_id
                else o.tag == intent.request.tag
            )
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise TradingUnavailable("Ambiguous broker order identity")
        raw = matches[0]
        req = intent.request
        if (
            raw.tag,
            raw.instrument_token,
            raw.product,
            raw.transaction_type,
            raw.quantity,
        ) != (req.tag, req.instrument, "D", req.side, req.quantity):
            raise TradingUnavailable("Broker order does not match intent")
        status = {
            "complete": "complete",
            "cancelled": "cancelled",
            "rejected": "rejected",
            "open": "open",
            "trigger pending": "trigger_pending",
        }.get(str(raw.status).lower(), "unknown")
        fills = {}
        if raw.filled_quantity:
            trades = sdk_data(self.orders.get_trades_by_order, raw.order_id, "2.0")
            for t in trades:
                f = Fill(t.trade_id, t.order_id, t.quantity, decimal(t.average_price))
                if f.order_id != raw.order_id or (
                    f.trade_id in fills and fills[f.trade_id] != f
                ):
                    raise TradingUnavailable("Conflicting broker fills")
                fills[f.trade_id] = f
        return BrokerOrder(
            raw.order_id,
            req.tag,
            status,
            req.quantity,
            raw.filled_quantity,
            tuple(fills.values()),
        )

    def cancel(self, order_id):
        # Cancellation acknowledgement is never proof of terminal cancellation.
        sdk_data(self.orders.cancel_order, order_id, "2.0")

    def positions(self):
        return [
            BrokerPosition(p.instrument_token, p.product, p.quantity)
            for p in sdk_data(self.portfolio.get_positions, "2.0")
        ]

    def available_funds(self):
        data = sdk_data(self.user.get_user_fund_margin, "2.0", segment="SEC")
        return decimal(data["equity"].available_margin)

    def supports_protective_stop(self, contract):
        profile = self.profile
        return bool(
            self.protective_stop_verified
            and profile is not None
            and getattr(profile, "is_active", False) is True
            and "NFO" in (getattr(profile, "exchanges", None) or [])
            and "D" in (getattr(profile, "products", None) or [])
            and {"LIMIT", "SL"}.issubset(getattr(profile, "order_types", None) or [])
        )

    def close(self):
        for cleanup in (
            self.client.rest_client.pool_manager.clear,
            self.client.pool.close,
            self.client.pool.join,
        ):
            try:
                cleanup()
            except (OSError, ValueError, RuntimeError):
                pass

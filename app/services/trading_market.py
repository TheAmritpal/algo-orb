"""Read-only market adapter for the official SDK; no order submissions."""

import time as clock
from datetime import UTC, date, datetime, time
from decimal import Decimal
from threading import Lock

import upstox_client as sdk

from app.strategies.models import Candle, Contract, MarketSnapshot, Quote, money
from app.strategies.rules import IST, UNDERLYING


class TradingUnavailable(RuntimeError):
    pass


_gate = Lock()
_last = 0.0


def sdk_data(method, *args, **kwargs):
    # Shared gate: at most 6 requests/s and below 500 requests/minute.
    global _last
    with _gate:
        clock.sleep(max(0, 0.17 - (clock.monotonic() - _last)))
        _last = clock.monotonic()
    try:
        result = method(*args, _request_timeout=(2, 3), **kwargs)
    except Exception:  # noqa: BLE001 -- fail closed without leaking broker/SQL secrets
        raise TradingUnavailable(
            "Broker request failed; mutation outcome may be uncertain"
        ) from None
    if getattr(result, "status", None) != "success":
        raise TradingUnavailable("Broker returned an unsuccessful response")
    return result.data


def decimal(value):
    return money(Decimal(str(value)))


def stamp(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Missing broker timezone")
        return value
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        return datetime.fromtimestamp(int(value) / 1000, UTC)
    result = datetime.fromisoformat(str(value))
    if result.tzinfo is None:
        raise ValueError("Missing broker timezone")
    return result


def parse_contract(raw):
    if (
        raw.underlying_key != UNDERLYING
        or raw.underlying_symbol != "RELIANCE"
        or raw.segment != "NSE_FO"
        or not raw.instrument_key.startswith("NSE_FO|")
        or type(raw.weekly) is not bool
    ):
        raise ValueError("Unexpected RELIANCE contract metadata")
    expiry = (
        raw.expiry.date()
        if isinstance(raw.expiry, datetime)
        else date.fromisoformat(str(raw.expiry))
    )
    # Upstox metadata tick_size is in paise; domain prices are rupees.
    return Contract(
        raw.instrument_key,
        raw.underlying_key,
        expiry,
        raw.weekly,
        decimal(raw.strike_price),
        raw.instrument_type,
        raw.lot_size,
        decimal(raw.tick_size) / 100,
    )


def parse_quote(raw, instrument):
    if raw.instrument_token != instrument:
        raise ValueError("Quote instrument mismatch")
    bids = [d for d in raw.depth.buy if d.quantity > 0 and d.price > 0]
    asks = [d for d in raw.depth.sell if d.quantity > 0 and d.price > 0]
    if not bids or not asks:
        raise ValueError("Missing actionable market depth")
    return Quote(
        instrument,
        stamp(raw.timestamp),
        max(decimal(d.price) for d in bids),
        min(decimal(d.price) for d in asks),
        decimal(raw.last_price),
    )


def normal_session(timings, now):
    for t in timings:
        start, end = (
            stamp(t.start_time).astimezone(IST),
            stamp(t.end_time).astimezone(IST),
        )
        if (
            t.exchange == "NSE"
            and start.date() == end.date() == now.astimezone(IST).date()
            and start.time() == time(9, 15)
            and end.time() == time(15, 30)
        ):
            return start <= now < end
    return False


class MarketData:
    def __init__(self, client):
        self.client = client
        self.history = sdk.HistoryV3Api(client)
        self.options = sdk.OptionsApi(client)
        self.quotes = sdk.MarketQuoteApi(client)
        self.calendar = sdk.MarketHolidaysAndTimingsApi(client)
        self._contracts = []
        self._day = None
        self._timings = []

    def quote(self, instrument):
        data = sdk_data(self.quotes.get_full_market_quote, instrument, "2.0")
        found = [v for v in data.values() if v.instrument_token == instrument]
        if len(found) != 1:
            raise ValueError("Missing or ambiguous quote")
        return parse_quote(found[0], instrument)

    def snapshot(self, now, contract=None):
        today = now.astimezone(IST).date()
        if self._day != today:
            timings = sdk_data(self.calendar.get_exchange_timings, today.isoformat())
            contracts = sdk_data(self.options.get_option_contracts, UNDERLYING)
            parsed = [parse_contract(c) for c in contracts]
            self._timings = timings
            self._contracts = parsed
            self._day = today
        active = normal_session(self._timings, now)
        if contract:
            return MarketSnapshot(
                now,
                active,
                [],
                self._contracts,
                None,
                self.quote(contract.instrument_key),
            )
        if not active:
            return MarketSnapshot(now, False, [], self._contracts, None, None)
        data = sdk_data(
            self.history.get_intra_day_candle_data, UNDERLYING, "minutes", 15
        )
        candles = [
            Candle(stamp(c[0]), *(decimal(v) for v in c[1:5])) for c in data.candles
        ]
        return MarketSnapshot(
            now, True, candles, self._contracts, self.quote(UNDERLYING), None
        )

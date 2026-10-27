"""Read-only market adapter for the official SDK; no order submissions."""

import json
import re
import time as clock
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from threading import Lock

import upstox_client as sdk
from upstox_client.rest import ApiException

from app.strategies.models import Candle, Contract, MarketSnapshot, Quote, money
from app.strategies.rules import IST, UNDERLYING


class TradingUnavailable(RuntimeError):
    def __init__(self, message, *, http_status=None, broker_code=None):
        super().__init__(message)
        self.http_status = http_status
        self.broker_code = broker_code


class BrokerOrderNotFound(TradingUnavailable):
    pass


def _failure_metadata(exc):
    status = getattr(exc, "status", None)
    http_status = status if type(status) is int and 100 <= status <= 599 else None
    broker_code = None
    body = getattr(exc, "body", None)
    if isinstance(body, (str, bytes)) and len(body) <= 65536:
        try:
            payload = json.loads(body)
            errors = payload.get("errors") if isinstance(payload, dict) else None
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                code = errors[0].get("errorCode", errors[0].get("error_code"))
                if isinstance(code, str) and re.fullmatch(r"UDAPI[0-9]{1,12}", code):
                    broker_code = code
        except (ValueError, TypeError, UnicodeError):
            pass
    return http_status, broker_code


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
    except ApiException as exc:
        http_status, broker_code = _failure_metadata(exc)
        if (http_status, broker_code) == (404, "UDAPI100010"):
            raise BrokerOrderNotFound(
                "Broker order record not found",
                http_status=http_status,
                broker_code=broker_code,
            ) from None
        raise TradingUnavailable(
            "Broker request failed; mutation outcome may be uncertain",
            http_status=http_status,
            broker_code=broker_code,
        ) from None
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
        self._next_session_day = None
        self._next_session = None

    def next_trading_session(self, now):
        """Entry-only calendar gate; never needed to manage existing exposure."""
        today = now.astimezone(IST).date()
        if self._next_session_day == today:
            return self._next_session
        for offset in range(1, 15):
            candidate = today + timedelta(days=offset)
            timings = sdk_data(
                self.calendar.get_exchange_timings, candidate.isoformat()
            )
            if not isinstance(timings, list):
                raise TradingUnavailable("Future exchange timings unavailable")
            for timing in timings:
                if timing.exchange != "NSE":
                    continue
                start, end = stamp(timing.start_time), stamp(timing.end_time)
                if (
                    start.astimezone(IST).date() != candidate
                    or end.astimezone(IST).date() != candidate
                    or end <= start
                ):
                    raise TradingUnavailable("Invalid future exchange timings")
                self._next_session_day = today
                self._next_session = candidate
                return candidate
        raise TradingUnavailable("Next exchange session unavailable")

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

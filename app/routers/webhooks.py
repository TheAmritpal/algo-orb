"""Public broker callbacks: receipt never authorizes a trading action."""

import json

import pymysql
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.repositories.order_updates import save_update

router = APIRouter(prefix="/webhooks/upstox", tags=["Upstox postbacks"])
MAX_BODY = 65536
# Persist only documented fields. Never persist arbitrary request data or tokens.
TEXT_FIELDS = {
    "exchange",
    "instrument_token",
    "instrument_key",
    "trading_symbol",
    "tradingsymbol",
    "product",
    "order_type",
    "transaction_type",
    "order_ref_id",
    "exchange_order_id",
    "parent_order_id",
    "validity",
    "status",
    "variety",
    "tag",
    "exchange_timestamp",
    "order_id",
    "order_request_id",
    "order_timestamp",
    "guid",
    "placed_by",
    "gtt_order_id",
    "type",
}
NUMBER_FIELDS = {
    "average_price",
    "price",
    "trigger_price",
    "quantity",
    "disclosed_quantity",
    "pending_quantity",
    "filled_quantity",
    "expires_at",
    "created_at",
}


def identifier(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 100
        and value.isascii()
        and all(32 < ord(c) < 127 for c in value)
    )


def validated_update(value):
    if not isinstance(value, dict) or value.get("update_type") not in {
        "order",
        "gtt_order",
    }:
        raise ValueError
    kind = value["update_type"]
    order_key = "order_id" if kind == "order" else "gtt_order_id"
    if not identifier(value.get(order_key)):
        raise ValueError
    user = value.get("user_id", value.get("userId"))
    if kind == "order" and (
        not identifier(user)
        or not isinstance(value.get("status"), str)
        or not value["status"].strip()
    ):
        raise ValueError
    if user is not None and not identifier(user):
        raise ValueError
    if "userId" in value and value["userId"] != user:
        raise ValueError
    result = {"update_type": kind}
    if user is not None:
        result["user_id"] = user
    for key in TEXT_FIELDS:
        if key in value:
            field = value[key]
            if field is not None and (not isinstance(field, str) or len(field) > 255):
                raise ValueError
            result[key] = field
    for key in NUMBER_FIELDS:
        if key in value:
            field = value[key]
            if isinstance(field, bool) or not isinstance(field, (int, float)):
                raise ValueError
            result[key] = field
    if "is_amo" in value:
        if not isinstance(value["is_amo"], bool):
            raise ValueError
        result["is_amo"] = value["is_amo"]
    if kind == "gtt_order":
        rules = value.get("rules")
        if not isinstance(rules, list) or not 1 <= len(rules) <= 10:
            raise ValueError
        result["rules"] = []
        for rule in rules:
            if not isinstance(rule, dict):
                raise TypeError
            cleaned = {}
            for key in (
                "strategy",
                "status",
                "trigger_type",
                "transaction_type",
                "order_id",
            ):
                field = rule.get(key)
                if field is not None and (
                    not isinstance(field, str) or len(field) > 255
                ):
                    raise ValueError
                if key in rule:
                    cleaned[key] = field
            if not cleaned.get("strategy") or not cleaned.get("status"):
                raise ValueError
            if "trigger_price" in rule:
                price = rule["trigger_price"]
                if isinstance(price, bool) or not isinstance(price, (int, float)):
                    raise ValueError
                cleaned["trigger_price"] = price
            result["rules"].append(cleaned)
    return result


def invalid_constant(_):
    raise ValueError


@router.post("/orders")
async def orders(request: Request):
    if (
        request.headers.get("content-type", "").split(";")[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(415, "Expected application/json")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise HTTPException(413, "Postback too large")
        body.extend(chunk)
    try:
        payload = validated_update(json.loads(body, parse_constant=invalid_constant))
        # Also rejects numeric overflow such as 1e999.
        json.dumps(payload, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(400, "Invalid order update") from None
    try:
        await run_in_threadpool(save_update, payload)
    except (pymysql.MySQLError, ValueError):
        raise HTTPException(503, "Unable to record order update") from None
    return {"status": "received"}

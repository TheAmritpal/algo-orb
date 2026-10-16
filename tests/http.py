import asyncio

from starlette.datastructures import Headers

from app.main import app


async def asgi_request(
    path, query="", cookie="", method="GET", body=b"", extra_headers=()
):
    messages = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    async def ticker():
        # This sandbox cannot wake asyncio from worker threads without a timer.
        while True:
            await asyncio.sleep(0.01)

    tick = asyncio.create_task(ticker())
    try:
        await asyncio.wait_for(
            app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                    "http_version": "1.1",
                    "method": method,
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": query.encode(),
                    "root_path": "",
                    "headers": ([(b"cookie", cookie.encode())] if cookie else [])
                    + list(extra_headers),
                    "server": ("localhost", 8000),
                    "client": ("127.0.0.1", 1),
                },
                receive,
                send,
            ),
            timeout=5,
        )
    finally:
        tick.cancel()
    start = next(m for m in messages if m["type"] == "http.response.start")
    headers = Headers(raw=start["headers"])
    body = b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )
    return start["status"], headers, body

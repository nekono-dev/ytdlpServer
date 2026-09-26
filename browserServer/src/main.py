from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiohttp import WSMsgType, web
from browser import ChromiumBrowser
from session import SessionError, SessionManager

PORT = int(os.environ.get("PORT", "8080"))
SESSION_TIMEOUT = int(os.environ.get("SESSION_TIMEOUT", "900"))
INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@web.middleware
async def session_errors(request: web.Request, handler: Handler) -> web.StreamResponse:
    try:
        return await handler(request)
    except SessionError as e:
        return web.json_response({"message": e.message}, status=e.status)


async def read_json(request: web.Request) -> dict:
    try:
        form = await request.json()
    except Exception:
        form = None
    if not isinstance(form, dict):
        raise SessionError(400, "Invalid request.")
    return form


def manager_of(request: web.Request) -> SessionManager:
    return request.app["manager"]


async def index(_: web.Request) -> web.Response:
    return web.Response(text=INDEX_HTML, content_type="text/html")


async def get_session(request: web.Request) -> web.Response:
    return web.json_response(manager_of(request).info())


async def start_session(request: web.Request) -> web.Response:
    form = await read_json(request)
    info = await manager_of(request).start(
        form.get("profile"), form.get("start_url"), mobile=form.get("mobile") is True)
    return web.json_response(info)


async def commit_session(request: web.Request) -> web.Response:
    return web.json_response(await manager_of(request).commit())


async def cancel_session(request: web.Request) -> web.Response:
    await manager_of(request).cancel()
    return web.json_response({"message": "Cancelled."})


async def screen(request: web.Request) -> web.WebSocketResponse:
    """画面配信と入力。ログイン操作の実行中だけ使える。"""
    ws = web.WebSocketResponse(max_msg_size=1 << 20, heartbeat=30)
    await ws.prepare(request)
    browser = manager_of(request).browser
    if browser is None:
        await ws.close()
        return ws
    browser.clients.add(ws)
    with contextlib.suppress(Exception):
        url = getattr(browser, "url", "")
        await ws.send_str(json.dumps({"type": "url", "url": url}))
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                await browser.handle_input(json.loads(msg.data), ws)
            except Exception as e:
                print("WARNING: input failed:", e)
    finally:
        browser.clients.discard(ws)
    return ws


def create_app(manager: SessionManager) -> web.Application:
    app = web.Application(middlewares=[session_errors])
    app["manager"] = manager
    app.add_routes([
        web.get("/", index),
        web.get("/session", get_session),
        web.post("/session", start_session),
        web.post("/session/commit", commit_session),
        web.delete("/session", cancel_session),
        web.get("/ws", screen),
    ])

    async def on_shutdown(_: web.Application) -> None:
        # コンテナ停止時に、ブラウザとプロファイルを残さない
        with contextlib.suppress(Exception):
            await manager.cancel()
    app.on_shutdown.append(on_shutdown)
    return app


if __name__ == "__main__":
    print("INFO: Start browserServer port:", PORT)
    web.run_app(create_app(SessionManager(ChromiumBrowser, SESSION_TIMEOUT)),
                host="0.0.0.0", port=PORT, print=None)

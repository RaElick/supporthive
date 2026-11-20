"""WebSocket streams. Auth via ?token= (browsers can't set headers on WS)."""

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import session_scope
from ..events import hub
from ..models import Customer, Operator
from ..security import read_token

router = APIRouter()


async def _pump(ws: WebSocket, channels: list[str]) -> None:
    queues = [(c, hub.subscribe(c)) for c in channels]
    merged: asyncio.Queue = asyncio.Queue()

    async def fwd(q: asyncio.Queue) -> None:
        while True:
            await merged.put(await q.get())

    tasks = [asyncio.create_task(fwd(q)) for _, q in queues]

    async def drain_client() -> None:  # detect disconnects; we ignore client→server data
        while True:
            await ws.receive_text()

    reader = asyncio.create_task(drain_client())
    try:
        while True:
            get = asyncio.create_task(merged.get())
            done, _ = await asyncio.wait({get, reader}, return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                get.cancel()
                break
            await ws.send_json(get.result())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader.cancel()
        for t in tasks:
            t.cancel()
        for c, q in queues:
            hub.unsubscribe(c, q)
        with contextlib.suppress(Exception):
            await ws.close()


@router.websocket("/ws/operators")
async def ws_operators(ws: WebSocket, token: str = "") -> None:
    sub = read_token(token, "operator")
    with session_scope() as s:
        ok = sub is not None and s.get(Operator, int(sub)) is not None
    if not ok:
        await ws.close(code=4401)
        return
    await ws.accept()
    await _pump(ws, ["operators"])


@router.websocket("/ws/widget")
async def ws_widget(ws: WebSocket, token: str = "") -> None:
    """Customer stream: every event about any of this customer's tickets."""
    sub = read_token(token, "widget")
    with session_scope() as s:
        c = s.get(Customer, int(sub)) if sub else None
        cid = c.id if c else None
    if cid is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    await _pump(ws, [f"customer:{cid}"])

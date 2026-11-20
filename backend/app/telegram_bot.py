"""Telegram channel over the raw Bot API (long polling, httpx) — no bot framework.

Inbound: text messages become customer messages (same service as the widget).
Outbound: operator/system messages and the rating keyboard are relayed via the event hub.
Started from app lifespan only when TELEGRAM_BOT_TOKEN is set.
"""

import asyncio
import logging

import httpx

from .agents import pipeline
from .db import session_scope
from .events import hub
from .models import Customer, Ticket
from .services import tickets as svc

log = logging.getLogger(__name__)

GREETING = (
    "Здравствуйте! Это служба поддержки. Опишите, пожалуйста, ваш вопрос одним сообщением — "
    "мы подключим подходящего специалиста."
)


class TelegramBot:
    def __init__(self, token: str) -> None:
        self.api = f"https://api.telegram.org/bot{token}"
        self.client = httpx.AsyncClient(timeout=40)
        self._offset = 0
        self._task: asyncio.Task | None = None

    async def call(self, method: str, **params) -> dict:
        r = await self.client.post(f"{self.api}/{method}", json=params)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method}: {data.get('description')}")
        return data["result"]

    async def send(self, chat_id: str, text: str, keyboard: list | None = None) -> None:
        params: dict = {"chat_id": chat_id, "text": text}
        if keyboard:
            params["reply_markup"] = {"inline_keyboard": keyboard}
        await self.call("sendMessage", **params)

    # ----- inbound -----

    async def handle_update(self, upd: dict) -> None:
        if cq := upd.get("callback_query"):
            await self._on_rating(cq)
            return
        msg = upd.get("message")
        if not msg or "text" not in msg:
            return
        chat_id = str(msg["chat"]["id"])
        text = msg["text"]
        if text.startswith("/start"):
            await self.send(chat_id, GREETING)
            return
        u = msg.get("from", {})
        name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or None
        with session_scope() as s:
            c = svc.get_or_create_customer(s, "telegram", chat_id, name)
            t, m, is_new = svc.add_customer_message(s, c, text)
            md, tid = svc.message_dict(m), t.id
        await hub.publish("operators", "message", md)
        if is_new:
            await self.send(chat_id, "Обращение принято, подбираем оператора…")
            pipeline.spawn(pipeline.on_new_ticket(tid))

    async def _on_rating(self, cq: dict) -> None:
        data = cq.get("data", "")
        try:
            _, tid, val = data.split(":")
            tid_i, rating = int(tid), int(val)
        except ValueError:
            return
        chat_id = str(cq["message"]["chat"]["id"])
        with session_scope() as s:
            t = s.get(Ticket, tid_i)
            if t is None or t.customer.external_id != chat_id or t.rating is not None:
                ok = False
            else:
                try:
                    svc.rate_ticket(s, t, rating)
                    ok = True
                except svc.DomainError:
                    ok = False
        await self.call("answerCallbackQuery", callback_query_id=cq["id"], text="Спасибо за оценку!" if ok else "")
        if ok:
            await pipeline.publish_ticket(tid_i)
            pipeline.spawn(pipeline.review_ticket(tid_i))

    # ----- outbound -----

    async def on_event(self, channel: str, event: dict) -> None:
        if not channel.startswith("customer:"):
            return
        cid = int(channel.split(":", 1)[1])
        with session_scope() as s:
            c = s.get(Customer, cid)
            if c is None or c.channel != "telegram":
                return
            chat_id = c.external_id
        data = event["data"]
        if event["type"] == "message" and data["sender"] in ("operator", "system"):
            await self.send(chat_id, data["text"])
        elif event["type"] == "ticket_closed":
            kb = [[{"text": "⭐" * n, "callback_data": f"rate:{data['id']}:{n}"} for n in range(1, 6)]]
            await self.send(chat_id, "Оцените, пожалуйста, работу оператора:", kb)

    # ----- loop -----

    async def _poll(self) -> None:
        while True:
            try:
                updates = await self.call("getUpdates", offset=self._offset, timeout=30)
                for u in updates:
                    self._offset = u["update_id"] + 1
                    try:
                        await self.handle_update(u)
                    except Exception:
                        log.exception("failed to handle telegram update")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("telegram polling error, retrying in 5s")
                await asyncio.sleep(5)

    def start(self) -> None:
        hub.add_listener(self.on_event)
        self._task = asyncio.create_task(self._poll())
        log.info("telegram bot polling started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.client.aclose()

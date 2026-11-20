"""Public endpoints for the website chat widget (customer side)."""

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents import pipeline
from ..db import get_db, session_scope
from ..events import hub
from ..models import Customer, Ticket
from ..security import make_token
from ..services import tickets as svc
from .deps import current_customer, domain

router = APIRouter(prefix="/api/widget", tags=["widget"])


class SessionIn(BaseModel):
    name: str | None = Field(default=None, max_length=128)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class RateIn(BaseModel):
    ticket_id: int
    rating: int = Field(ge=1, le=5)


@router.post("/session")
def create_session(body: SessionIn, s: Session = Depends(get_db)) -> dict:
    c = svc.get_or_create_customer(s, "widget", secrets.token_urlsafe(16), body.name)
    # Commit before responding: get_db's teardown commit runs after the response is sent,
    # and the browser uses this token immediately.
    s.commit()
    return {"token": make_token(str(c.id), "widget", ttl_hours=24 * 30), "customer_id": c.id, "name": c.name}


@router.get("/ticket")
def current_ticket(c: Customer = Depends(current_customer), s: Session = Depends(get_db)) -> dict:
    """The customer's open ticket, or the latest closed one (to show the rating prompt)."""
    t = svc.open_ticket(s, c) or s.scalar(
        select(Ticket).where(Ticket.customer_id == c.id).order_by(Ticket.id.desc())
    )
    return {"ticket": svc.ticket_dict(t, with_messages=True) if t else None}


@router.post("/messages")
async def send_message(body: MessageIn, c: Customer = Depends(current_customer)) -> dict:
    try:
        with session_scope() as s:
            cust = s.get(Customer, c.id)
            t, msg, is_new = svc.add_customer_message(s, cust, body.text)
            m, tid = svc.message_dict(msg), t.id
    except svc.DomainError as e:
        raise domain(e) from e
    await pipeline.to_customer(tid, "message", m)
    await hub.publish("operators", "message", m)
    if is_new:
        pipeline.spawn(pipeline.on_new_ticket(tid))
    return {"ticket_id": tid, "message": m, "new_ticket": is_new}


@router.post("/rate")
async def rate(body: RateIn, c: Customer = Depends(current_customer)) -> dict:
    with session_scope() as s:
        t = s.get(Ticket, body.ticket_id)
        if t is None or t.customer_id != c.id:
            raise HTTPException(404, "обращение не найдено")
        try:
            svc.rate_ticket(s, t, body.rating)
        except svc.DomainError as e:
            raise domain(e) from e
    await pipeline.publish_ticket(body.ticket_id)
    pipeline.spawn(pipeline.review_ticket(body.ticket_id))  # re-review with the rating known
    return {"ok": True}

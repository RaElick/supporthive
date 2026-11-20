"""Operator workspace: login, presence, tickets, replies, copilot."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..agents import pipeline
from ..db import get_db, session_scope
from ..events import hub
from ..models import Operator, Ticket
from ..security import make_token, verify_password
from ..services import tickets as svc
from .deps import current_operator, domain, get_ticket

router = APIRouter(prefix="/api", tags=["operator"])


class LoginIn(BaseModel):
    login: str
    password: str


class StatusIn(BaseModel):
    online: bool


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class CloseIn(BaseModel):
    resolved: bool = True


class TransferIn(BaseModel):
    operator_id: int


def operator_dict(o: Operator) -> dict:
    return {
        "id": o.id,
        "login": o.login,
        "name": o.name,
        "is_admin": o.is_admin,
        "skills": o.skills,
        "max_concurrent": o.max_concurrent,
        "online": o.online,
    }


@router.post("/auth/login")
def login(body: LoginIn, s: Session = Depends(get_db)) -> dict:
    op = s.scalar(select(Operator).where(Operator.login == body.login))
    if op is None or not verify_password(body.password, op.password_hash):
        raise HTTPException(401, "неверный логин или пароль")
    return {"token": make_token(str(op.id), "operator"), "operator": operator_dict(op)}


@router.get("/me")
def me(op: Operator = Depends(current_operator)) -> dict:
    return operator_dict(op)


@router.post("/me/status")
async def set_status(body: StatusIn, op: Operator = Depends(current_operator)) -> dict:
    with session_scope() as s:
        o = s.get(Operator, op.id)
        o.online = body.online
        data = operator_dict(o)
    await hub.publish("operators", "operator_status", data)
    if body.online:
        pipeline.spawn(pipeline.dispatch_queue())
    return data


@router.get("/operators")
def list_operators(_: Operator = Depends(current_operator), s: Session = Depends(get_db)) -> list[dict]:
    load = svc.operator_load(s)
    return [
        {**operator_dict(o), "load": load.get(o.id, 0)}
        for o in s.scalars(select(Operator).order_by(Operator.name)).all()
    ]


@router.get("/tickets")
def list_tickets(
    scope: str = Query("mine", pattern="^(mine|queue|open|closed|all)$"),
    limit: int = Query(100, le=500),
    op: Operator = Depends(current_operator),
    s: Session = Depends(get_db),
) -> list[dict]:
    q = select(Ticket).options(selectinload(Ticket.messages), selectinload(Ticket.customer), selectinload(Ticket.operator))
    if scope == "mine":
        q = q.where(Ticket.operator_id == op.id, Ticket.status == "assigned")
    elif scope == "queue":
        q = q.where(Ticket.status.in_(["new", "queued"]))
    elif scope == "open":
        q = q.where(Ticket.status != "closed")
    elif scope == "closed":
        q = q.where(Ticket.status == "closed")
        if not op.is_admin:
            q = q.where(Ticket.operator_id == op.id)
    elif not op.is_admin:
        q = q.where(Ticket.operator_id == op.id)
    rows = s.scalars(q.order_by(Ticket.id.desc()).limit(limit)).all()
    return [svc.ticket_dict(t) for t in rows]


@router.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: int, op: Operator = Depends(current_operator), s: Session = Depends(get_db)) -> dict:
    t = get_ticket(s, ticket_id)
    d = svc.ticket_dict(t, with_messages=True)
    from ..models import QualityReview, RoutingDecision

    rd = s.scalar(select(RoutingDecision).where(RoutingDecision.ticket_id == t.id).order_by(RoutingDecision.id.desc()))
    qr = s.scalar(select(QualityReview).where(QualityReview.ticket_id == t.id))
    d["routing"] = {"strategy": rd.strategy, "mode": rd.mode, "reason": rd.reason} if rd else None
    d["quality"] = (
        {"score": qr.score, "politeness": qr.politeness, "resolved": qr.resolved, "flags": qr.flags, "comment": qr.comment, "mode": qr.mode}
        if qr
        else None
    )
    return d


@router.post("/tickets/{ticket_id}/messages")
async def reply(ticket_id: int, body: MessageIn, op: Operator = Depends(current_operator)) -> dict:
    with session_scope() as s:
        t = get_ticket(s, ticket_id)
        try:
            msg = svc.add_operator_message(s, t, s.get(Operator, op.id), body.text)
        except svc.DomainError as e:
            raise domain(e) from e
        m = svc.message_dict(msg)
    await pipeline.to_customer(ticket_id, "message", m)
    await hub.publish("operators", "message", m)
    return m


@router.post("/tickets/{ticket_id}/take")
async def take(ticket_id: int, op: Operator = Depends(current_operator)) -> dict:
    """Manually pick a ticket from the queue (overrides the router)."""
    with session_scope() as s:
        t = get_ticket(s, ticket_id)
        if t.status not in ("new", "queued"):
            raise HTTPException(409, "обращение уже взято")
        o = s.get(Operator, op.id)
        svc.assign(s, t, o)
        svc.add_system_message(s, t, f"К диалогу подключился оператор {o.name}")
    await pipeline.publish_ticket(ticket_id, "ticket_assigned")
    return {"ok": True}


@router.post("/tickets/{ticket_id}/transfer")
async def transfer(ticket_id: int, body: TransferIn, op: Operator = Depends(current_operator)) -> dict:
    with session_scope() as s:
        t = get_ticket(s, ticket_id)
        if t.operator_id != op.id and not op.is_admin:
            raise HTTPException(403, "можно передать только своё обращение")
        target = s.get(Operator, body.operator_id)
        if target is None or target.is_admin:
            raise HTTPException(404, "оператор не найден")
        try:
            svc.transfer(s, t, target)
        except svc.DomainError as e:
            raise domain(e) from e
        svc.add_system_message(s, t, f"Диалог передан оператору {target.name}")
    await pipeline.publish_ticket(ticket_id, "ticket_assigned")
    return {"ok": True}


@router.post("/tickets/{ticket_id}/close")
async def close(ticket_id: int, body: CloseIn, op: Operator = Depends(current_operator)) -> dict:
    with session_scope() as s:
        t = get_ticket(s, ticket_id)
        if t.operator_id != op.id and not op.is_admin:
            raise HTTPException(403, "можно закрыть только своё обращение")
        try:
            svc.close_ticket(s, t, body.resolved)
        except svc.DomainError as e:
            raise domain(e) from e
        msg = svc.add_system_message(s, t, "Обращение закрыто. Оцените, пожалуйста, качество обслуживания.")
        m = svc.message_dict(msg)
    await pipeline.to_customer(ticket_id, "message", m)
    await pipeline.on_ticket_closed(ticket_id)
    return {"ok": True}


@router.post("/tickets/{ticket_id}/suggest")
async def suggest(ticket_id: int, op: Operator = Depends(current_operator)) -> dict:
    with session_scope() as s:
        get_ticket(s, ticket_id)
    return await pipeline.suggest(ticket_id)

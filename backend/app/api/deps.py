from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Customer, Operator, Ticket
from ..security import read_token
from ..services.tickets import DomainError


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "нужна авторизация")
    return authorization.split(" ", 1)[1]


def current_operator(authorization: str | None = Header(None), s: Session = Depends(get_db)) -> Operator:
    sub = read_token(_bearer(authorization), "operator")
    op = s.get(Operator, int(sub)) if sub else None
    if op is None:
        raise HTTPException(401, "сессия недействительна")
    return op


def current_admin(op: Operator = Depends(current_operator)) -> Operator:
    if not op.is_admin:
        raise HTTPException(403, "только для администратора")
    return op


def current_customer(authorization: str | None = Header(None), s: Session = Depends(get_db)) -> Customer:
    sub = read_token(_bearer(authorization), "widget")
    c = s.get(Customer, int(sub)) if sub else None
    if c is None:
        raise HTTPException(401, "сессия виджета недействительна")
    return c


def get_ticket(s: Session, ticket_id: int) -> Ticket:
    t = s.get(Ticket, ticket_id)
    if t is None:
        raise HTTPException(404, "обращение не найдено")
    return t


def domain(e: DomainError) -> HTTPException:
    return HTTPException(409, str(e))

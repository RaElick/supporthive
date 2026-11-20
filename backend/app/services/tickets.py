"""Ticket lifecycle. Synchronous, DB-only; callers publish events after commit."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents.router import Candidate
from ..config import get_settings
from ..models import Customer, Message, Operator, Setting, Ticket, utcnow


class DomainError(ValueError):
    pass


# ---------- settings ----------

def get_setting(s: Session, key: str, default: str) -> str:
    row = s.get(Setting, key)
    return row.value if row else default


def set_setting(s: Session, key: str, value: str) -> None:
    row = s.get(Setting, key)
    if row:
        row.value = value
    else:
        s.add(Setting(key=key, value=value))


def routing_strategy(s: Session) -> str:
    return get_setting(s, "routing_strategy", get_settings().routing_strategy)


def copilot_placement(s: Session) -> str:
    return get_setting(s, "copilot_placement", "system_first")


# ---------- customers & tickets ----------

def get_or_create_customer(s: Session, channel: str, external_id: str, name: str | None = None) -> Customer:
    c = s.scalar(select(Customer).where(Customer.channel == channel, Customer.external_id == external_id))
    if c is None:
        c = Customer(channel=channel, external_id=external_id, name=name or "Клиент")
        s.add(c)
        s.flush()
    elif name and c.name == "Клиент":
        c.name = name
    return c


def open_ticket(s: Session, customer: Customer) -> Ticket | None:
    return s.scalar(
        select(Ticket)
        .where(Ticket.customer_id == customer.id, Ticket.status != "closed")
        .order_by(Ticket.id.desc())
    )


def add_customer_message(s: Session, customer: Customer, text: str) -> tuple[Ticket, Message, bool]:
    """Append to the customer's open ticket or start a new one. Returns (ticket, msg, is_new)."""
    text = text.strip()
    if not text:
        raise DomainError("пустое сообщение")
    ticket = open_ticket(s, customer)
    is_new = ticket is None
    if is_new:
        ticket = Ticket(customer_id=customer.id, channel=customer.channel, status="new")
        s.add(ticket)
        s.flush()
    msg = Message(ticket_id=ticket.id, sender="customer", text=text[:4000])
    s.add(msg)
    s.flush()
    return ticket, msg, is_new


def add_operator_message(s: Session, ticket: Ticket, operator: Operator, text: str) -> Message:
    text = text.strip()
    if not text:
        raise DomainError("пустое сообщение")
    if ticket.status == "closed":
        raise DomainError("обращение уже закрыто")
    if ticket.operator_id != operator.id and not operator.is_admin:
        raise DomainError("обращение назначено другому оператору")
    msg = Message(ticket_id=ticket.id, sender="operator", operator_id=operator.id, text=text[:4000])
    s.add(msg)
    if ticket.first_response_at is None:
        ticket.first_response_at = utcnow()
    s.flush()
    return msg


def add_system_message(s: Session, ticket: Ticket, text: str) -> Message:
    msg = Message(ticket_id=ticket.id, sender="system", text=text)
    s.add(msg)
    s.flush()
    return msg


def assign(s: Session, ticket: Ticket, operator: Operator) -> None:
    ticket.operator_id = operator.id
    ticket.status = "assigned"
    ticket.assigned_at = utcnow()


def close_ticket(s: Session, ticket: Ticket, resolved: bool | None = None) -> None:
    if ticket.status == "closed":
        raise DomainError("обращение уже закрыто")
    ticket.status = "closed"
    ticket.closed_at = utcnow()
    ticket.resolved = resolved


def rate_ticket(s: Session, ticket: Ticket, rating: int) -> None:
    if ticket.status != "closed":
        raise DomainError("оценить можно только закрытое обращение")
    if not 1 <= rating <= 5:
        raise DomainError("оценка от 1 до 5")
    ticket.rating = rating


def transfer(s: Session, ticket: Ticket, operator: Operator) -> None:
    if ticket.status == "closed":
        raise DomainError("обращение уже закрыто")
    assign(s, ticket, operator)


# ---------- routing inputs ----------

def operator_load(s: Session) -> dict[int, int]:
    rows = s.execute(
        select(Ticket.operator_id, func.count()).where(Ticket.status == "assigned").group_by(Ticket.operator_id)
    ).all()
    return {op_id: n for op_id, n in rows if op_id is not None}


def operator_history(s: Session) -> dict[int, dict[str, tuple[int, float | None]]]:
    rows = s.execute(
        select(Ticket.operator_id, Ticket.category, func.count(), func.avg(Ticket.rating))
        .where(Ticket.status == "closed", Ticket.operator_id.is_not(None))
        .group_by(Ticket.operator_id, Ticket.category)
    ).all()
    out: dict[int, dict[str, tuple[int, float | None]]] = {}
    for op_id, cat, n, avg in rows:
        out.setdefault(op_id, {})[cat or "general"] = (n, float(avg) if avg is not None else None)
    return out


def candidates(s: Session) -> list[Candidate]:
    load = operator_load(s)
    hist = operator_history(s)
    last = dict(
        s.execute(select(Ticket.operator_id, func.max(Ticket.assigned_at)).group_by(Ticket.operator_id)).all()
    )
    ops = s.scalars(select(Operator).where(Operator.online.is_(True), Operator.is_admin.is_(False))).all()
    return [
        Candidate(
            id=o.id,
            name=o.name,
            skills=list(o.skills or []),
            load=load.get(o.id, 0),
            max_concurrent=o.max_concurrent,
            history=hist.get(o.id, {}),
            last_assigned_ts=last[o.id].timestamp() if last.get(o.id) else 0.0,
        )
        for o in ops
    ]


def queued_tickets(s: Session) -> list[Ticket]:
    order = {"high": 0, "normal": 1, "low": 2}
    rows = s.scalars(select(Ticket).where(Ticket.status == "queued")).all()
    return sorted(rows, key=lambda t: (order.get(t.priority, 1), t.created_at, t.id))


# ---------- serialisation ----------

def message_dict(m: Message) -> dict:
    return {
        "id": m.id,
        "ticket_id": m.ticket_id,
        "sender": m.sender,
        "operator_id": m.operator_id,
        "text": m.text,
        "created_at": m.created_at.isoformat() + "Z",
    }


def ticket_dict(t: Ticket, with_messages: bool = False) -> dict:
    iso = lambda d: d.isoformat() + "Z" if d else None  # noqa: E731
    d = {
        "id": t.id,
        "channel": t.channel,
        "status": t.status,
        "category": t.category,
        "priority": t.priority,
        "summary": t.summary,
        "operator_id": t.operator_id,
        "operator_name": t.operator.name if t.operator else None,
        "customer": {"id": t.customer.id, "name": t.customer.name},
        "created_at": iso(t.created_at),
        "assigned_at": iso(t.assigned_at),
        "first_response_at": iso(t.first_response_at),
        "closed_at": iso(t.closed_at),
        "rating": t.rating,
        "resolved": t.resolved,
    }
    if with_messages:
        d["messages"] = [message_dict(m) for m in t.messages]
    elif t.messages:
        d["last_message"] = message_dict(t.messages[-1])
    return d

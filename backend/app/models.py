"""Domain tables. Datetimes are naive UTC (SQLite has no tz support)."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

CATEGORIES = ["billing", "delivery", "technical", "account", "returns", "general"]
CATEGORY_LABELS = {
    "billing": "Оплата",
    "delivery": "Доставка",
    "technical": "Техническая проблема",
    "account": "Аккаунт и доступ",
    "returns": "Возврат и обмен",
    "general": "Общий вопрос",
}
PRIORITIES = ["low", "normal", "high"]
CHANNELS = ["widget", "telegram"]
TICKET_STATUSES = ["new", "queued", "assigned", "closed"]


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(256))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=3)
    online: Mapped[bool] = mapped_column(Boolean, default=False)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="Клиент")
    channel: Mapped[str] = mapped_column(String(16))
    # widget: random session id; telegram: chat id
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    category: Mapped[str | None] = mapped_column(String(32))
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    summary: Mapped[str | None] = mapped_column(Text)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    rating: Mapped[int | None] = mapped_column(Integer)  # 1..5 from the customer
    resolved: Mapped[bool | None] = mapped_column(Boolean)  # operator's verdict on close

    customer: Mapped[Customer] = relationship()
    operator: Mapped[Operator | None] = relationship()
    messages: Mapped[list["Message"]] = relationship(
        back_populates="ticket", order_by="Message.id", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    sender: Mapped[str] = mapped_column(String(16))  # customer | operator | system
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="messages")


class KnowledgeArticle(Base):
    __tablename__ = "kb_articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)


class QualityReview(Base):
    __tablename__ = "quality_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), unique=True)
    score: Mapped[int] = mapped_column(Integer)  # 1..10
    politeness: Mapped[int] = mapped_column(Integer)  # 1..5
    resolved: Mapped[bool] = mapped_column(Boolean)
    flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    comment: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(16))  # llm | heuristic
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RoutingDecision(Base):
    __tablename__ = "routing_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    strategy: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16))
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"))
    candidates: Mapped[list[dict]] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentRun(Base):
    """Trace of every agent invocation — raw material for the research part."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)

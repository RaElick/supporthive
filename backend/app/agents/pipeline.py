"""Orchestrates agents around ticket events and persists their results.

Agents themselves are pure (data in → result out). This module is the only place that
reads inputs from the DB, calls agents, writes results and publishes events.
"""

import asyncio
import logging
import time
from dataclasses import asdict

from sqlalchemy import select

from ..db import session_scope
from ..events import hub
from ..models import AgentRun, KnowledgeArticle, Operator, QualityReview, RoutingDecision, Ticket
from ..services import tickets as svc
from . import copilot, quality, router, triage
from .llm import get_llm

log = logging.getLogger(__name__)
_dispatch_lock = asyncio.Lock()
_background: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    """Fire-and-forget with a strong reference and error logging."""
    task = asyncio.create_task(coro)
    _background.add(task)

    def _done(t: asyncio.Task) -> None:
        _background.discard(t)
        if not t.cancelled() and t.exception():
            log.error("background task failed", exc_info=t.exception())

    task.add_done_callback(_done)
    return task


async def drain() -> None:
    """Wait for all background agent work (tests and the simulator use this)."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


def _trace(agent: str, ticket_id: int | None, mode: str, inp: dict, out: dict, err: str | None, t0: float) -> None:
    with session_scope() as s:
        s.add(
            AgentRun(
                agent=agent,
                ticket_id=ticket_id,
                mode=mode,
                input=inp,
                output=out,
                error=err,
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            )
        )


async def to_customer(ticket_id: int, event: str, data: dict) -> None:
    """Deliver an event to the customer who owns the ticket (widget WS / Telegram relay)."""
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        if t is None:
            return
        cid = t.customer_id
    await hub.publish(f"customer:{cid}", event, data)


async def publish_ticket(ticket_id: int, event: str = "ticket_updated") -> None:
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        if t is None:
            return
        data = svc.ticket_dict(t)
        cid = t.customer_id
    await hub.publish("operators", event, data)
    await hub.publish(f"customer:{cid}", event, data)


# ---------- triage ----------

async def on_new_ticket(ticket_id: int) -> None:
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        text = "\n".join(m.text for m in t.messages if m.sender == "customer")
    llm = get_llm()
    t0 = time.perf_counter()
    try:
        result, mode, report = await triage.run(llm, text)
        err = None
    except Exception as e:  # LLM failure → heuristic, never leave the ticket untriaged
        result, mode, report, err = triage.heuristic(text), "heuristic", {}, f"{type(e).__name__}: {e}"
    _trace("triage", ticket_id, mode, {"text": text[:2000], "context": report}, result.model_dump(), err, t0)
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        t.category, t.priority, t.summary = result.category, result.priority, result.summary
        if t.status == "new":
            t.status = "queued"
    await publish_ticket(ticket_id)
    await dispatch_queue()


# ---------- routing ----------

async def dispatch_queue() -> list[tuple[int, int]]:
    """Assign queued tickets while there is capacity. Returns [(ticket_id, operator_id)]."""
    assigned: list[tuple[int, int]] = []
    async with _dispatch_lock:
        while True:
            with session_scope() as s:
                queue = svc.queued_tickets(s)
                if not queue:
                    break
                t = queue[0]
                info = router.TicketInfo(t.id, t.category or "general", t.priority, t.summary or "")
                cands = svc.candidates(s)
                strategy = svc.routing_strategy(s)
            if not router.available(cands):
                break
            t0 = time.perf_counter()
            decision, report = await router.route(strategy, get_llm(), info, cands)
            _trace(
                "router",
                info.id,
                decision.mode,
                {"strategy": strategy, "ticket": asdict(info), "candidates": router.candidates_snapshot(cands), "context": report},
                {"operator_id": decision.operator_id, "reason": decision.reason, "scores": decision.scores},
                None,
                t0,
            )
            if decision.operator_id is None:
                break
            with session_scope() as s:
                t = s.get(Ticket, info.id)
                op = s.get(Operator, decision.operator_id)
                if t.status != "queued":  # closed meanwhile
                    continue
                svc.assign(s, t, op)
                svc.add_system_message(s, t, f"К диалогу подключился оператор {op.name}")
                s.add(
                    RoutingDecision(
                        ticket_id=t.id,
                        strategy=strategy,
                        mode=decision.mode,
                        operator_id=op.id,
                        candidates=router.candidates_snapshot(cands),
                        reason=decision.reason,
                    )
                )
            assigned.append((info.id, decision.operator_id))
            await publish_ticket(info.id, "ticket_assigned")
            with session_scope() as s:
                last = s.get(Ticket, info.id).messages[-1]
                await to_customer(info.id, "message", svc.message_dict(last))
    return assigned


# ---------- copilot ----------

async def suggest(ticket_id: int) -> dict:
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        turns = [copilot.Turn(m.sender, m.text) for m in t.messages if m.sender != "system"]
        arts = [copilot.Article(a.id, a.category, a.title, a.body) for a in s.scalars(select(KnowledgeArticle)).all()]
        category = t.category
        placement = svc.copilot_placement(s)
    t0 = time.perf_counter()
    try:
        sug, mode, report = await copilot.run(get_llm(), category, turns, arts, placement)
        err = None
    except Exception as e:
        hits = copilot.retrieve(" ".join(x.text for x in turns if x.sender == "customer"), arts, category)
        sug, mode, report, err = copilot.heuristic(turns, hits), "heuristic", {}, f"{type(e).__name__}: {e}"
    _trace("copilot", ticket_id, mode, {"placement": placement, "context": report}, sug.model_dump(), err, t0)
    titles = {a.id: a.title for a in arts}
    return {
        "reply": sug.reply,
        "articles": [{"id": i, "title": titles.get(i, "?")} for i in sug.used_articles if i in titles],
        "mode": mode,
    }


# ---------- quality ----------

async def review_ticket(ticket_id: int) -> None:
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        turns = [copilot.Turn(m.sender, m.text) for m in t.messages if m.sender != "system"]
        frt = (t.first_response_at - t.created_at).total_seconds() if t.first_response_at else None
        rating = t.rating
    t0 = time.perf_counter()
    try:
        rev, mode, report = await quality.run(get_llm(), turns, frt, rating)
        err = None
    except Exception as e:
        rev, mode, report, err = quality.heuristic(turns, frt, rating), "heuristic", {}, f"{type(e).__name__}: {e}"
    _trace("quality", ticket_id, mode, {"context": report}, rev.model_dump(), err, t0)
    with session_scope() as s:
        existing = s.scalar(select(QualityReview).where(QualityReview.ticket_id == ticket_id))
        if existing:
            s.delete(existing)
            s.flush()
        s.add(QualityReview(ticket_id=ticket_id, mode=mode, **rev.model_dump()))
    await hub.publish("operators", "quality_review", {"ticket_id": ticket_id, **rev.model_dump()})


async def on_ticket_closed(ticket_id: int) -> None:
    await publish_ticket(ticket_id, "ticket_closed")
    spawn(review_ticket(ticket_id))
    await dispatch_queue()

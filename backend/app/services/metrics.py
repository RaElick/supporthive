"""Dashboard metrics. Computed in Python over the period's tickets — fine for tens of thousands."""

from collections import defaultdict
from datetime import timedelta
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..models import CATEGORY_LABELS, Operator, QualityReview, RoutingDecision, Ticket, utcnow


def _avg(xs: list[float]) -> float | None:
    return round(mean(xs), 1) if xs else None


def _secs(a, b) -> float | None:
    return (b - a).total_seconds() if a and b else None


def dashboard(s: Session, days: int = 14) -> dict:
    now = utcnow()
    since = now - timedelta(days=days)
    tickets = s.scalars(
        select(Ticket).where(Ticket.created_at >= since).options(selectinload(Ticket.operator))
    ).all()
    reviews = {r.ticket_id: r for r in s.scalars(select(QualityReview)).all()}
    ops = {o.id: o for o in s.scalars(select(Operator)).all()}

    closed = [t for t in tickets if t.status == "closed"]
    frt = [x for t in tickets if (x := _secs(t.created_at, t.first_response_at)) is not None]
    res = [x for t in closed if (x := _secs(t.created_at, t.closed_at)) is not None]
    ratings = [t.rating for t in closed if t.rating]

    overview = {
        "total": len(tickets),
        "open": sum(t.status in ("new", "queued", "assigned") for t in tickets),
        "queued": sum(t.status == "queued" for t in tickets),
        "closed": len(closed),
        "avg_first_response_s": _avg(frt),
        "avg_resolution_s": _avg(res),
        "avg_rating": round(mean(ratings), 2) if ratings else None,
        "csat_pct": round(100 * sum(r >= 4 for r in ratings) / len(ratings), 1) if ratings else None,
        "avg_quality_score": _avg([reviews[t.id].score for t in closed if t.id in reviews]),
        "online_operators": sum(o.online and not o.is_admin for o in ops.values()),
    }

    by_op: dict[int, list[Ticket]] = defaultdict(list)
    for t in tickets:
        if t.operator_id:
            by_op[t.operator_id].append(t)
    operators = []
    for op_id, ts in by_op.items():
        o = ops.get(op_id)
        c = [t for t in ts if t.status == "closed"]
        operators.append(
            {
                "id": op_id,
                "name": o.name if o else f"#{op_id}",
                "online": bool(o and o.online),
                "skills": o.skills if o else [],
                "handled": len(c),
                "open": sum(t.status == "assigned" for t in ts),
                "avg_first_response_s": _avg([x for t in ts if (x := _secs(t.created_at, t.first_response_at)) is not None]),
                "avg_resolution_s": _avg([x for t in c if (x := _secs(t.created_at, t.closed_at)) is not None]),
                "avg_rating": _avg([t.rating for t in c if t.rating]),
                "avg_quality_score": _avg([reviews[t.id].score for t in c if t.id in reviews]),
            }
        )
    operators.sort(key=lambda r: -r["handled"])

    by_cat: dict[str, list[Ticket]] = defaultdict(list)
    for t in tickets:
        by_cat[t.category or "general"].append(t)
    categories = [
        {
            "category": cat,
            "label": CATEGORY_LABELS.get(cat, cat),
            "count": len(ts),
            "avg_resolution_s": _avg([x for t in ts if t.status == "closed" and (x := _secs(t.created_at, t.closed_at)) is not None]),
            "avg_rating": _avg([t.rating for t in ts if t.rating]),
        }
        for cat, ts in sorted(by_cat.items(), key=lambda kv: -len(kv[1]))
    ]

    daily = []
    for i in range(days - 1, -1, -1):
        day = (now - timedelta(days=i)).date()
        daily.append(
            {
                "date": day.isoformat(),
                "created": sum(t.created_at.date() == day for t in tickets),
                "closed": sum(t.closed_at is not None and t.closed_at.date() == day for t in tickets),
            }
        )

    return {
        "period_days": days,
        "overview": overview,
        "operators": operators,
        "categories": categories,
        "daily": daily,
        "anomalies": anomalies(tickets, reviews, now),
        "routing": routing_stats(s, since),
    }


def anomalies(tickets: list[Ticket], reviews: dict[int, QualityReview], now) -> list[dict]:
    """Simple tickets that took too long, plus dialogs the quality agent flagged as rude/unresolved."""
    limit = get_settings().anomaly_minutes_low_priority * 60
    out = []
    for t in tickets:
        if t.priority != "low":
            continue
        dur = _secs(t.created_at, t.closed_at or now)
        if dur and dur > limit:
            out.append(
                {
                    "ticket_id": t.id,
                    "kind": "slow_simple_ticket",
                    "text": f"Простое обращение «{(t.summary or '')[:60]}» "
                    f"{'обрабатывалось' if t.closed_at else 'открыто уже'} {int(dur // 60)} мин",
                    "operator_name": t.operator.name if t.operator else None,
                }
            )
    for t in tickets:
        r = reviews.get(t.id)
        if r and ("rude" in r.flags or (not r.resolved and (t.rating or 5) <= 2)):
            out.append(
                {
                    "ticket_id": t.id,
                    "kind": "quality",
                    "text": f"Аналитик качества: {r.comment[:120]}",
                    "operator_name": t.operator.name if t.operator else None,
                }
            )
    return out[:50]


def routing_stats(s: Session, since) -> list[dict]:
    rows = s.execute(
        select(RoutingDecision, Ticket)
        .join(Ticket, Ticket.id == RoutingDecision.ticket_id)
        .where(RoutingDecision.created_at >= since, RoutingDecision.operator_id.is_not(None))
    ).all()
    ops = {o.id: o for o in s.scalars(select(Operator)).all()}
    agg: dict[str, dict] = defaultdict(lambda: {"decisions": 0, "skill_match": 0, "ratings": [], "res": []})
    for d, t in rows:
        a = agg[d.strategy]
        a["decisions"] += 1
        op = ops.get(d.operator_id)
        if op and t.category in (op.skills or []):
            a["skill_match"] += 1
        if t.rating:
            a["ratings"].append(t.rating)
        if (x := _secs(t.created_at, t.closed_at)) is not None:
            a["res"].append(x)
    return [
        {
            "strategy": k,
            "decisions": v["decisions"],
            "skill_match_pct": round(100 * v["skill_match"] / v["decisions"], 1) if v["decisions"] else None,
            "avg_rating": _avg(v["ratings"]),
            "avg_resolution_s": _avg(v["res"]),
        }
        for k, v in sorted(agg.items())
    ]

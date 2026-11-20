"""Routing agent: which operator gets a queued ticket.

Strategies are pure functions over `Candidate` snapshots so the simulator can reuse them
without a database.
"""

from dataclasses import asdict, dataclass, field

from pydantic import BaseModel

from ..models import CATEGORY_LABELS
from .context import ContextBuilder
from .llm import LLMClient, schema_hint

STRATEGIES = ["queue", "skills", "llm"]


@dataclass
class Candidate:
    id: int
    name: str
    skills: list[str]
    load: int  # open tickets right now
    max_concurrent: int
    # history: category -> (closed tickets, average customer rating or None)
    history: dict[str, tuple[int, float | None]] = field(default_factory=dict)
    last_assigned_ts: float = 0.0  # for FIFO fairness in the queue strategy

    @property
    def free(self) -> int:
        return self.max_concurrent - self.load


@dataclass
class TicketInfo:
    id: int
    category: str
    priority: str
    summary: str


@dataclass
class Decision:
    operator_id: int | None
    reason: str
    mode: str
    scores: dict[int, float] = field(default_factory=dict)


def available(cands: list[Candidate]) -> list[Candidate]:
    return [c for c in cands if c.free > 0]


def route_queue(ticket: TicketInfo, cands: list[Candidate]) -> Decision:
    pool = available(cands)
    if not pool:
        return Decision(None, "нет свободных операторов", "heuristic")
    best = min(pool, key=lambda c: (c.load, c.last_assigned_ts, c.id))
    return Decision(best.id, f"общая очередь: наименьшая загрузка ({best.load}/{best.max_concurrent})", "heuristic")


def skill_score(ticket: TicketInfo, c: Candidate) -> float:
    score = 0.0
    if ticket.category in c.skills:
        score += 5.0
    solved, rating = c.history.get(ticket.category, (0, None))
    score += min(solved, 20) * 0.15  # experience, saturating at 20 tickets
    if rating is not None:
        score += (rating - 3.0) * 0.8  # good ratings help, bad ones hurt
    score -= 1.2 * c.load / max(c.max_concurrent, 1) * 3
    if ticket.priority == "high" and ticket.category in c.skills:
        score += 1.0  # urgent tickets really want a specialist
    return round(score, 3)


def route_skills(ticket: TicketInfo, cands: list[Candidate]) -> Decision:
    pool = available(cands)
    if not pool:
        return Decision(None, "нет свободных операторов", "heuristic")
    scores = {c.id: skill_score(ticket, c) for c in pool}
    best = max(pool, key=lambda c: (scores[c.id], -c.load, -c.id))
    why = "профильный навык" if ticket.category in best.skills else "нет профильного — лучший по истории/загрузке"
    return Decision(best.id, f"навыки: {why}, балл {scores[best.id]}", "heuristic", scores)


class LLMChoice(BaseModel):
    operator_id: int
    reason: str


ROUTER_SYSTEM = (
    "Ты — агент маршрутизации обращений в службу поддержки. Выбери одного оператора из списка "
    "кандидатов, который лучше всего решит обращение: учитывай профильные навыки, опыт и "
    "оценки по этой теме, а также текущую загрузку (перегруженный оператор отвечает медленнее). "
    "Выбирать можно ТОЛЬКО operator_id из списка. Причину объясни одной фразой по-русски."
)


async def route_llm(llm: LLMClient | None, ticket: TicketInfo, cands: list[Candidate]) -> tuple[Decision, dict]:
    pool = available(cands)
    if not pool:
        return Decision(None, "нет свободных операторов", "heuristic"), {}
    if llm is None:
        d = route_skills(ticket, cands)
        d.reason = "LLM не настроен → " + d.reason
        return d, {}
    ctx = ContextBuilder(budget_tokens=2500)
    ctx.instructions(ROUTER_SYSTEM + "\nОтветь JSON по схеме: " + schema_hint(LLMChoice))
    for c in pool:
        hist = ", ".join(
            f"{CATEGORY_LABELS.get(cat, cat)}: {n} закрыто" + (f", оценка {r:.1f}" if r else "")
            for cat, (n, r) in c.history.items()
        ) or "нет истории"
        ctx.add(
            f"оператор {c.id}",
            f"operator_id={c.id}, {c.name}; навыки: {', '.join(c.skills) or '—'}; "
            f"загрузка {c.load}/{c.max_concurrent}; история: {hist}",
            priority=50,
            pinned=True,  # never drop a candidate — the model must see all of them
        )
    task = (
        f"Обращение #{ticket.id}: тема {ticket.category} ({CATEGORY_LABELS.get(ticket.category)}), "
        f"приоритет {ticket.priority}. Суть: {ticket.summary}"
    )
    choice = await llm.complete_json(ctx.build(task), LLMChoice)
    report = ctx.report.as_dict() if ctx.report else {}
    if choice.operator_id not in {c.id for c in pool}:
        d = route_skills(ticket, cands)
        d.reason = f"LLM выбрал недопустимого оператора {choice.operator_id} → " + d.reason
        return d, report
    return Decision(choice.operator_id, choice.reason, "llm"), report


async def route(strategy: str, llm: LLMClient | None, ticket: TicketInfo, cands: list[Candidate]) -> tuple[Decision, dict]:
    if strategy == "queue":
        return route_queue(ticket, cands), {}
    if strategy == "llm":
        try:
            return await route_llm(llm, ticket, cands)
        except Exception as e:  # LLM down must never stall the queue
            d = route_skills(ticket, cands)
            d.reason = f"ошибка LLM ({type(e).__name__}) → " + d.reason
            return d, {}
    return route_skills(ticket, cands), {}


def candidates_snapshot(cands: list[Candidate]) -> list[dict]:
    return [asdict(c) for c in cands]

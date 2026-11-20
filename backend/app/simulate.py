"""Discrete-event simulator comparing routing strategies on identical synthetic load.

    python -m app.simulate --tickets 500 --seed 1 [--llm]

Model (deliberately simple, all knobs explicit):
  * tickets arrive as a Poisson process; categories follow a fixed mix;
  * each operator has a hidden competence per category (high for listed skills);
  * handling time ~ base / competence; resolution probability ~ competence;
  * the customer rating depends on resolution and on waiting time.
Strategies only see what production sees (skills, load, history of past ratings),
never the hidden competence — that is what makes the comparison meaningful.
"""

import argparse
import asyncio
import heapq
import random
from dataclasses import dataclass, field
from statistics import mean

from .agents import router
from .agents.llm import get_llm
from .agents.router import Candidate, TicketInfo
from .seed import OPERATORS

CATS = ["billing", "delivery", "technical", "account", "returns", "general"]
MIX = [3, 4, 2, 2, 2, 1]


@dataclass
class SimOp:
    id: int
    name: str
    skills: list[str]
    max_concurrent: int
    competence: dict[str, float]
    load: int = 0
    last_assigned: float = 0.0
    history: dict[str, list[int]] = field(default_factory=dict)

    def candidate(self) -> Candidate:
        hist = {c: (len(r), mean(r)) for c, r in self.history.items() if r}
        return Candidate(self.id, self.name, self.skills, self.load, self.max_concurrent, hist, self.last_assigned)


def make_ops(rng: random.Random) -> list[SimOp]:
    ops = []
    for i, (_, name, skills, maxc, quality) in enumerate(OPERATORS, start=1):
        comp = {c: (0.75 + 0.25 * quality if c in skills else 0.35 + 0.2 * rng.random()) for c in CATS}
        ops.append(SimOp(i, name, skills, maxc, comp))
    return ops


async def run_strategy(strategy: str, n: int, seed: int, rate_per_min: float) -> dict:
    rng = random.Random(seed)
    ops = make_ops(random.Random(seed))
    llm = get_llm() if strategy == "llm" else None
    # pre-generate identical arrivals for every strategy
    t, arrivals = 0.0, []
    for i in range(n):
        t += rng.expovariate(rate_per_min)
        cat = rng.choices(CATS, MIX)[0]
        arrivals.append((t, i, cat, rng.choice(["low", "normal", "normal", "high"]), rng.random(), rng.random()))

    events: list = [(a[0], 0, "arrive", a) for a in arrivals]
    heapq.heapify(events)
    queue: list = []
    waits, handle, ratings, resolved, matched = [], [], [], 0, 0
    seq = 1

    async def try_dispatch(now: float) -> None:
        nonlocal seq, resolved, matched
        while queue:
            arr = queue[0]
            _, i, cat, prio, r1, r2 = arr
            d, _ = await router.route(strategy, llm, TicketInfo(i, cat, prio, ""), [o.candidate() for o in ops])
            if d.operator_id is None:
                return
            queue.pop(0)
            op = next(o for o in ops if o.id == d.operator_id)
            op.load += 1
            op.last_assigned = now
            comp = op.competence[cat]
            dur = 6.0 / comp * (0.7 + 0.6 * r1)  # minutes
            ok = r2 < comp
            wait = now - arr[0]
            rating = max(1, min(5, round((4.6 if ok else 2.0) - min(wait, 30) / 15)))
            waits.append(wait)
            handle.append(dur)
            ratings.append(rating)
            resolved += ok
            matched += cat in op.skills
            heapq.heappush(events, (now + dur, seq, "done", (op.id, cat, rating)))
            seq += 1

    while events:
        now, _, kind, payload = heapq.heappop(events)
        if kind == "arrive":
            queue.append(payload)
            queue.sort(key=lambda a: ({"high": 0, "normal": 1, "low": 2}[a[3]], a[0]))
        else:
            op_id, cat, rating = payload
            op = next(o for o in ops if o.id == op_id)
            op.load -= 1
            op.history.setdefault(cat, []).append(rating)
        await try_dispatch(now)

    return {
        "strategy": strategy,
        "tickets": len(ratings),
        "avg_wait_min": round(mean(waits), 2),
        "avg_handle_min": round(mean(handle), 2),
        "resolved_pct": round(100 * resolved / len(ratings), 1),
        "skill_match_pct": round(100 * matched / len(ratings), 1),
        "avg_rating": round(mean(ratings), 2),
    }


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tickets", type=int, default=500)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--rate", type=float, default=1.2, help="arrivals per minute")
    p.add_argument("--llm", action="store_true", help="include the LLM strategy (needs LLM_BASE_URL)")
    a = p.parse_args()
    strategies = ["queue", "skills"] + (["llm"] if a.llm else [])
    rows = [await run_strategy(s, a.tickets, a.seed, a.rate) for s in strategies]
    cols = list(rows[0])
    print(" | ".join(f"{c:>15}" for c in cols))
    for r in rows:
        print(" | ".join(f"{r[c]!s:>15}" for c in cols))


if __name__ == "__main__":
    asyncio.run(main())

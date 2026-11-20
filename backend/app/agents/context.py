"""Context engineering primitive: prioritised fragments under a token budget.

Every agent describes its context as fragments instead of concatenating strings:

    ctx = ContextBuilder(budget_tokens=3000)
    ctx.add("instructions", SYSTEM, priority=100, pinned=True)
    ctx.add("kb:12", article, priority=60)
    ctx.add("msg:5", "Клиент: ...", priority=40 + recency)
    messages = ctx.build(user_task)

When the budget is exceeded, the lowest-priority non-pinned fragments are evicted and replaced
by a one-line stub ("[вытеснено: kb:12 — ...]") so the model knows something was there.
`placement` controls where reference fragments go — this is the experimental knob:
  - "system_first": everything in the system message, task last (classic)
  - "recency_last": instructions in system, reference fragments right before the task
"""

from dataclasses import dataclass, field


def estimate_tokens(text: str) -> int:
    # ~3.5 chars/token is a fair average for mixed Russian/English with modern BPE tokenizers.
    return max(1, int(len(text) / 3.5))


@dataclass
class Fragment:
    key: str
    text: str
    priority: int
    pinned: bool = False
    role: str = "reference"  # "instructions" | "reference"

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass
class BuildReport:
    used_tokens: int
    budget_tokens: int
    kept: list[str] = field(default_factory=list)
    evicted: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "used_tokens": self.used_tokens,
            "budget_tokens": self.budget_tokens,
            "kept": self.kept,
            "evicted": self.evicted,
        }


class ContextBuilder:
    def __init__(self, budget_tokens: int = 3000, placement: str = "system_first") -> None:
        if placement not in ("system_first", "recency_last"):
            raise ValueError(f"unknown placement {placement!r}")
        self.budget = budget_tokens
        self.placement = placement
        self._frags: list[Fragment] = []
        self.report: BuildReport | None = None

    def add(self, key: str, text: str, priority: int = 50, pinned: bool = False, role: str = "reference") -> None:
        if text.strip():
            self._frags.append(Fragment(key, text.strip(), priority, pinned, role))

    def instructions(self, text: str) -> None:
        self.add("instructions", text, priority=1000, pinned=True, role="instructions")

    def _select(self, task_tokens: int) -> tuple[list[Fragment], list[Fragment]]:
        budget = self.budget - task_tokens
        kept = list(self._frags)
        evicted: list[Fragment] = []
        # Evict cheapest-priority first; among equals, the oldest-added first.
        order = sorted(
            (f for f in kept if not f.pinned), key=lambda f: (f.priority, self._frags.index(f))
        )
        total = sum(f.tokens for f in kept)
        for f in order:
            if total <= budget:
                break
            kept.remove(f)
            evicted.append(f)
            total -= f.tokens
        return kept, evicted

    def build(self, task: str) -> list[dict]:
        kept, evicted = self._select(estimate_tokens(task))
        instr = [f.text for f in kept if f.role == "instructions"]
        refs = [f"### {f.key}\n{f.text}" for f in kept if f.role == "reference"]
        if evicted:
            refs.append(
                "### вытеснено из контекста\n"
                + "\n".join(f"[вытеснено: {f.key} — {f.text[:60]}…]" for f in evicted)
            )
        self.report = BuildReport(
            used_tokens=sum(f.tokens for f in kept) + estimate_tokens(task),
            budget_tokens=self.budget,
            kept=[f.key for f in kept],
            evicted=[f.key for f in evicted],
        )
        if self.placement == "system_first":
            system = "\n\n".join(instr + refs)
            return [{"role": "system", "content": system}, {"role": "user", "content": task}]
        system = "\n\n".join(instr)
        user = "\n\n".join(refs + [f"### задача\n{task}"])
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

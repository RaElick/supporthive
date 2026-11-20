"""Copilot agent: drafts the operator's next reply from the dialog and the knowledge base."""

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from ..config import get_settings
from .context import ContextBuilder
from .llm import LLMClient, schema_hint


@dataclass
class Article:
    id: int
    category: str
    title: str
    body: str


@dataclass
class Turn:
    sender: str  # customer | operator | system
    text: str


class Suggestion(BaseModel):
    reply: str = Field(max_length=1500)
    used_articles: list[int] = Field(default_factory=list)


_WORD = re.compile(r"[а-яёa-z0-9]{3,}", re.I)


def _stems(text: str) -> set[str]:
    # Crude Russian "stemming": the first 5 letters are enough for keyword overlap.
    return {w.lower()[:5] for w in _WORD.findall(text)}


def retrieve(query: str, articles: list[Article], category: str | None, k: int = 3) -> list[tuple[Article, float]]:
    q = _stems(query)
    scored = []
    for a in articles:
        overlap = len(q & _stems(a.title + " " + a.body))
        if overlap == 0:
            continue
        score = overlap + (2.0 if category and a.category == category else 0.0)
        scored.append((a, score))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


SYSTEM = (
    "Ты — помощник оператора службы поддержки интернет-магазина. Напиши черновик следующего "
    "ответа оператора клиенту: вежливо, по делу, на «вы», без выдуманных фактов. Опирайся на "
    "статьи базы знаний; если ответа в них нет — предложи уточнить детали у клиента. "
    "Не обещай компенсаций, которых нет в базе знаний. В used_articles укажи id использованных статей."
)


def heuristic(turns: list[Turn], hits: list[tuple[Article, float]]) -> Suggestion:
    greet = "" if any(t.sender == "operator" for t in turns) else "Здравствуйте! "
    if not hits:
        return Suggestion(
            reply=f"{greet}Спасибо за обращение. Уточните, пожалуйста, номер заказа и подробнее "
            "опишите ситуацию — я сразу проверю информацию.",
        )
    art = hits[0][0]
    sentences = re.split(r"(?<=[.!?])\s+", art.body.strip())
    gist = " ".join(sentences[:3])
    return Suggestion(
        reply=f"{greet}{gist} Если останутся вопросы — напишите, пожалуйста, я помогу.",
        used_articles=[art.id],
    )


async def run(
    llm: LLMClient | None,
    category: str | None,
    turns: list[Turn],
    articles: list[Article],
    placement: str = "system_first",
    budget_tokens: int | None = None,
) -> tuple[Suggestion, str, dict]:
    customer_text = " ".join(t.text for t in turns if t.sender == "customer")
    hits = retrieve(customer_text, articles, category)
    if llm is None:
        return heuristic(turns, hits), "heuristic", {"kb_hits": [a.id for a, _ in hits]}

    ctx = ContextBuilder(budget_tokens or get_settings().llm_context_budget_tokens, placement)
    ctx.instructions(SYSTEM + "\nОтветь JSON по схеме: " + schema_hint(Suggestion))
    for rank, (a, _) in enumerate(hits):
        ctx.add(f"статья id={a.id}", f"{a.title}\n{a.body}", priority=70 - rank * 10)
    labels = {"customer": "Клиент", "operator": "Оператор", "system": "Система"}
    n = len(turns)
    for i, t in enumerate(turns):
        # Recent turns matter most; the very last message is never evicted.
        ctx.add(
            f"сообщение {i + 1}",
            f"{labels.get(t.sender, t.sender)}: {t.text}",
            priority=30 + int(30 * (i + 1) / n),
            pinned=(i == n - 1),
        )
    sug = await llm.complete_json(ctx.build("Напиши следующий ответ оператора."), Suggestion)
    report = ctx.report.as_dict() if ctx.report else {}
    report["kb_hits"] = [a.id for a, _ in hits]
    return sug, "llm", report

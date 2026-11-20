"""Triage agent: category, priority and one-line summary of a new ticket."""

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..config import get_settings
from ..models import CATEGORY_LABELS
from .context import ContextBuilder
from .llm import LLMClient, schema_hint


class TriageResult(BaseModel):
    category: Literal["billing", "delivery", "technical", "account", "returns", "general"]
    priority: Literal["low", "normal", "high"]
    summary: str = Field(max_length=200)


KEYWORDS: dict[str, list[str]] = {
    "billing": ["оплат", "списал", "списани", "деньг", "счёт", "счет", "карт", "чек", "тариф", "подписк"],
    "delivery": ["доставк", "курьер", "посылк", "трек", "привез", "отправлен", "пункт выдачи", "заказ не пришёл", "не пришел"],
    "technical": ["ошибк", "не работает", "сломал", "вылет", "завис", "баг", "приложени", "сайт не", "не грузит", "не открывается"],
    "account": ["пароль", "аккаунт", "логин", "войти", "вход", "учётн", "учетн", "почт", "смс", "код подтвержд", "заблокир"],
    "returns": ["возврат", "вернуть", "обмен", "брак", "не подош", "поменять"],
}
HIGH = ["срочно", "дважды", "мошен", "украли", "заблокир", "не работает", "уже неделю", "жалоб", "суд", "!!!"]
LOW = ["подскажите", "интересует", "хотел бы узнать", "хотела бы узнать", "можно ли", "вопрос"]

SYSTEM = (
    "Ты — агент первичной классификации обращений в службу поддержки интернет-магазина. "
    "Определи тему, приоритет и сформулируй суть обращения одной фразой (до 150 символов) "
    "от третьего лица. Темы: "
    + ", ".join(f"{k} — {v}" for k, v in CATEGORY_LABELS.items())
    + ". Приоритет high — деньги списаны ошибочно, сервис полностью недоступен, клиент "
    "раздражён или угрожает жалобой; low — справочный вопрос без проблемы; иначе normal."
)


def heuristic(text: str) -> TriageResult:
    t = text.lower()
    scores = {cat: sum(t.count(k) for k in kws) for cat, kws in KEYWORDS.items()}
    best = max(scores, key=lambda c: scores[c])
    category = best if scores[best] > 0 else "general"
    if any(k in t for k in HIGH):
        priority = "high"
    elif any(k in t for k in LOW):
        priority = "low"
    else:
        priority = "normal"
    first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    summary = first if len(first) <= 150 else first[:147] + "…"
    return TriageResult(category=category, priority=priority, summary=summary)


async def run(llm: LLMClient | None, customer_text: str) -> tuple[TriageResult, str, dict]:
    """Returns (result, mode, context_report)."""
    if llm is None:
        return heuristic(customer_text), "heuristic", {}
    ctx = ContextBuilder(budget_tokens=get_settings().llm_context_budget_tokens)
    ctx.instructions(SYSTEM + "\nОтветь JSON по схеме: " + schema_hint(TriageResult))
    msgs = ctx.build(f"Обращение клиента:\n{customer_text}")
    result = await llm.complete_json(msgs, TriageResult)
    return result, "llm", ctx.report.as_dict() if ctx.report else {}

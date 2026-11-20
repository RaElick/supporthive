"""Quality analyst agent: reviews a closed dialog."""

from pydantic import BaseModel, Field

from ..config import get_settings
from .context import ContextBuilder
from .copilot import Turn
from .llm import LLMClient, schema_hint

FLAGS = {
    "no_greeting": "оператор не поздоровался",
    "rude": "грубость или пренебрежение",
    "unresolved": "проблема клиента не решена",
    "slow_first_response": "долгий первый ответ",
    "no_operator_reply": "оператор не ответил",
    "short_answers": "односложные ответы без объяснений",
}


class Review(BaseModel):
    score: int = Field(ge=1, le=10)
    politeness: int = Field(ge=1, le=5)
    resolved: bool
    flags: list[str] = Field(default_factory=list)
    comment: str = Field(default="", max_length=600)


SYSTEM = (
    "Ты — аналитик качества службы поддержки. Оцени закрытый диалог оператора с клиентом: "
    "score 1–10 (общее качество), politeness 1–5, resolved — решена ли проблема клиента. "
    "flags — список нарушений из словаря: "
    + "; ".join(f"{k} — {v}" for k, v in FLAGS.items())
    + ". comment — 1–2 предложения: что оператору сделать лучше."
)

RUDE = ["ваши проблемы", "не ко мне", "сами виноваты", "читайте внимательнее", "ничем не могу", "я же сказал"]
POLITE = ["здравствуйте", "добрый день", "добрый вечер", "спасибо", "пожалуйста", "извините", "приносим извинения"]
THANKS = ["спасибо", "помогли", "получилось", "работает", "всё ок", "все ок", "разобрался", "разобралась"]


def heuristic(turns: list[Turn], first_response_s: float | None, customer_rating: int | None) -> Review:
    op = [t.text.lower() for t in turns if t.sender == "operator"]
    cust = [t.text.lower() for t in turns if t.sender == "customer"]
    flags: list[str] = []
    if not op:
        flags.append("no_operator_reply")
    elif not any(g in op[0] for g in ("здравствуйте", "добрый", "привет")):
        flags.append("no_greeting")
    if any(r in t for t in op for r in RUDE):
        flags.append("rude")
    if op and sum(len(t) for t in op) / len(op) < 25:
        flags.append("short_answers")
    if first_response_s is not None and first_response_s > 600:
        flags.append("slow_first_response")
    thanked = bool(cust) and any(k in cust[-1] for k in THANKS)
    resolved = thanked or (customer_rating is not None and customer_rating >= 4)
    if not resolved:
        flags.append("unresolved")
    polite_hits = sum(1 for t in op for p in POLITE if p in t)
    politeness = max(1, min(5, 2 + polite_hits - 3 * ("rude" in flags)))
    score = 8 - 2 * len(flags) + (1 if politeness >= 4 else 0) + (1 if resolved else 0)
    score = max(1, min(10, score))
    comment = "; ".join(FLAGS[f] for f in flags) or "Замечаний нет."
    return Review(score=score, politeness=politeness, resolved=resolved, flags=flags, comment=comment)


async def run(
    llm: LLMClient | None, turns: list[Turn], first_response_s: float | None, customer_rating: int | None
) -> tuple[Review, str, dict]:
    if llm is None:
        return heuristic(turns, first_response_s, customer_rating), "heuristic", {}
    ctx = ContextBuilder(get_settings().llm_context_budget_tokens)
    ctx.instructions(SYSTEM + "\nОтветь JSON по схеме: " + schema_hint(Review))
    labels = {"customer": "Клиент", "operator": "Оператор", "system": "Система"}
    dialog = "\n".join(f"{labels.get(t.sender, t.sender)}: {t.text}" for t in turns)
    meta = f"Время первого ответа: {first_response_s:.0f} c." if first_response_s is not None else ""
    if customer_rating:
        meta += f" Оценка клиента: {customer_rating}/5."
    review = await llm.complete_json(ctx.build(f"{meta}\nДиалог:\n{dialog}"), Review)
    review.flags = [f for f in review.flags if f in FLAGS]
    return review, "llm", ctx.report.as_dict() if ctx.report else {}

import json

import httpx
import pytest

from app.agents import copilot, quality, router, triage
from app.agents.context import ContextBuilder
from app.agents.llm import LLMClient, LLMError
from app.agents.router import Candidate, TicketInfo

# ---------- context builder ----------


def test_context_evicts_lowest_priority_and_keeps_pinned():
    ctx = ContextBuilder(budget_tokens=60)
    ctx.instructions("инструкция " * 10)
    ctx.add("low", "мусор " * 40, priority=10)
    ctx.add("high", "важно " * 5, priority=90)
    ctx.add("pinned", "закреп " * 5, priority=1, pinned=True)
    msgs = ctx.build("задача")
    assert ctx.report.evicted == ["low"]
    assert "pinned" in ctx.report.kept and "high" in ctx.report.kept
    assert "[вытеснено: low" in msgs[0]["content"]


def test_context_placement_recency_last_moves_refs_to_user():
    ctx = ContextBuilder(budget_tokens=1000, placement="recency_last")
    ctx.instructions("правила")
    ctx.add("kb", "статья", priority=50)
    msgs = ctx.build("ответь")
    assert "статья" not in msgs[0]["content"]
    assert msgs[1]["content"].index("статья") < msgs[1]["content"].index("ответь")


def test_context_rejects_unknown_placement():
    with pytest.raises(ValueError):
        ContextBuilder(placement="middle")


# ---------- triage ----------


@pytest.mark.parametrize(
    ("text", "cat", "prio"),
    [
        ("Деньги списались дважды, срочно!", "billing", "high"),
        ("Подскажите, где трек-номер посылки?", "delivery", "low"),
        ("Приложение вылетает при оплате корзины", "technical", "normal"),
        ("Не приходит смс код для входа", "account", "normal"),
        ("Хочу вернуть товар, брак", "returns", "normal"),
        ("Добрый день", "general", "normal"),
    ],
)
def test_triage_heuristic(text, cat, prio):
    r = triage.heuristic(text)
    assert (r.category, r.priority) == (cat, prio)
    assert r.summary


# ---------- router ----------


def _cands():
    return [
        Candidate(1, "A", ["billing"], load=2, max_concurrent=3),
        Candidate(2, "B", ["technical"], load=0, max_concurrent=3),
        Candidate(3, "C", ["billing"], load=3, max_concurrent=3),  # full
    ]


def test_skills_prefers_specialist_over_idle_generalist():
    d = router.route_skills(TicketInfo(1, "billing", "normal", ""), _cands())
    assert d.operator_id == 1


def test_queue_picks_least_loaded():
    d = router.route_queue(TicketInfo(1, "billing", "normal", ""), _cands())
    assert d.operator_id == 2


def test_no_capacity_returns_none():
    full = [Candidate(1, "A", [], load=1, max_concurrent=1)]
    assert router.route_skills(TicketInfo(1, "general", "low", ""), full).operator_id is None
    assert router.route_queue(TicketInfo(1, "general", "low", ""), full).operator_id is None


def test_history_breaks_ties_between_specialists():
    a = Candidate(1, "A", ["delivery"], 0, 3, history={"delivery": (10, 4.8)})
    b = Candidate(2, "B", ["delivery"], 0, 3, history={"delivery": (10, 2.5)})
    assert router.route_skills(TicketInfo(1, "delivery", "normal", ""), [b, a]).operator_id == 1


class FakeLLM:
    def __init__(self, payload: dict):
        self.payload = payload

    async def complete_json(self, messages, schema):
        return schema.model_validate(self.payload)


async def test_llm_router_rejects_hallucinated_operator():
    d, _ = await router.route("llm", FakeLLM({"operator_id": 999, "reason": "x"}), TicketInfo(1, "billing", "normal", ""), _cands())
    assert d.operator_id == 1 and "недопустимого" in d.reason


async def test_llm_router_accepts_valid_choice():
    d, report = await router.route("llm", FakeLLM({"operator_id": 2, "reason": "свободен"}), TicketInfo(1, "billing", "normal", ""), _cands())
    assert (d.operator_id, d.mode) == (2, "llm")
    assert "оператор 2" in report["kept"]


async def test_llm_router_falls_back_on_error():
    class Broken:
        async def complete_json(self, *a):
            raise LLMError("down")

    d, _ = await router.route("llm", Broken(), TicketInfo(1, "billing", "normal", ""), _cands())
    assert d.operator_id == 1 and "ошибка LLM" in d.reason


# ---------- copilot & quality ----------

ARTS = [
    copilot.Article(1, "account", "Восстановление пароля", "Нажмите «Забыли пароль?». Ссылка действует 1 час."),
    copilot.Article(2, "delivery", "Сроки доставки", "Курьер везёт 1–2 дня."),
]


def test_copilot_heuristic_uses_relevant_article():
    turns = [copilot.Turn("customer", "Забыл пароль от аккаунта")]
    hits = copilot.retrieve(turns[0].text, ARTS, "account")
    s = copilot.heuristic(turns, hits)
    assert s.used_articles == [1] and s.reply.startswith("Здравствуйте")


def test_quality_flags_rude_and_unresolved():
    turns = [copilot.Turn("customer", "не работает"), copilot.Turn("operator", "ваши проблемы"), copilot.Turn("customer", "ужас")]
    r = quality.heuristic(turns, 30, 1)
    assert {"rude", "unresolved", "no_greeting"} <= set(r.flags)
    assert r.score <= 3


def test_quality_good_dialog():
    turns = [
        copilot.Turn("customer", "не приходит код"),
        copilot.Turn("operator", "Здравствуйте! Пожалуйста, проверьте формат номера +7, спасибо за ожидание."),
        copilot.Turn("customer", "Спасибо, помогли!"),
    ]
    r = quality.heuristic(turns, 40, 5)
    assert r.resolved and r.flags == [] and r.score >= 9


# ---------- LLM client ----------


async def test_llm_client_parses_fenced_json_and_retries():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(body)
        content = "не json" if len(calls) == 1 else '```json\n{"category":"billing","priority":"high","summary":"s"}\n```'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    c = LLMClient("http://fake/v1", "k", "m")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = await c.complete_json([{"role": "user", "content": "x"}], triage.TriageResult)
    assert r.category == "billing" and len(calls) == 2
    assert calls[1]["messages"][-1]["content"].startswith("Ответ не прошёл проверку")

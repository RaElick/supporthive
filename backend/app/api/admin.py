"""Admin: dashboard, settings, operators, knowledge base, agent traces."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents import pipeline
from ..agents.llm import get_llm
from ..agents.router import STRATEGIES
from ..db import get_db
from ..models import CATEGORIES, CATEGORY_LABELS, AgentRun, KnowledgeArticle, Operator, RoutingDecision
from ..security import hash_password
from ..services import metrics
from ..services import tickets as svc
from .deps import current_admin
from .operator import operator_dict

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(current_admin)])


class SettingsIn(BaseModel):
    routing_strategy: str | None = None
    copilot_placement: str | None = None


class OperatorIn(BaseModel):
    login: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=4)
    skills: list[str] = []
    max_concurrent: int = Field(default=3, ge=1, le=20)
    is_admin: bool = False


class ArticleIn(BaseModel):
    category: str
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(min_length=1)


@router.get("/dashboard")
def dashboard(days: int = Query(14, ge=1, le=365), s: Session = Depends(get_db)) -> dict:
    return metrics.dashboard(s, days)


@router.get("/settings")
def get_settings_(s: Session = Depends(get_db)) -> dict:
    return {
        "routing_strategy": svc.routing_strategy(s),
        "copilot_placement": svc.copilot_placement(s),
        "strategies": STRATEGIES,
        "placements": ["system_first", "recency_last"],
        "llm_enabled": get_llm() is not None,
        "categories": [{"id": c, "label": CATEGORY_LABELS[c]} for c in CATEGORIES],
    }


@router.put("/settings")
async def put_settings(body: SettingsIn, s: Session = Depends(get_db)) -> dict:
    if body.routing_strategy is not None:
        if body.routing_strategy not in STRATEGIES:
            raise HTTPException(422, "неизвестная стратегия")
        svc.set_setting(s, "routing_strategy", body.routing_strategy)
    if body.copilot_placement is not None:
        if body.copilot_placement not in ("system_first", "recency_last"):
            raise HTTPException(422, "неизвестный режим")
        svc.set_setting(s, "copilot_placement", body.copilot_placement)
    s.commit()
    return get_settings_(s)


@router.get("/operators")
def operators(s: Session = Depends(get_db)) -> list[dict]:
    return [operator_dict(o) for o in s.scalars(select(Operator).order_by(Operator.id)).all()]


def _check_skills(skills: list[str]) -> None:
    bad = [x for x in skills if x not in CATEGORIES]
    if bad:
        raise HTTPException(422, f"неизвестные навыки: {bad}")


@router.post("/operators")
def create_operator(body: OperatorIn, s: Session = Depends(get_db)) -> dict:
    _check_skills(body.skills)
    if not body.password:
        raise HTTPException(422, "нужен пароль")
    if s.scalar(select(Operator).where(Operator.login == body.login)):
        raise HTTPException(409, "логин занят")
    o = Operator(
        login=body.login,
        name=body.name,
        password_hash=hash_password(body.password),
        skills=body.skills,
        max_concurrent=body.max_concurrent,
        is_admin=body.is_admin,
    )
    s.add(o)
    s.commit()
    return operator_dict(o)


@router.put("/operators/{op_id}")
def update_operator(op_id: int, body: OperatorIn, s: Session = Depends(get_db)) -> dict:
    _check_skills(body.skills)
    o = s.get(Operator, op_id)
    if o is None:
        raise HTTPException(404, "оператор не найден")
    o.name, o.skills, o.max_concurrent, o.is_admin = body.name, body.skills, body.max_concurrent, body.is_admin
    if body.password:
        o.password_hash = hash_password(body.password)
    s.commit()
    return operator_dict(o)


@router.get("/kb")
def kb(s: Session = Depends(get_db)) -> list[dict]:
    return [
        {"id": a.id, "category": a.category, "title": a.title, "body": a.body}
        for a in s.scalars(select(KnowledgeArticle).order_by(KnowledgeArticle.id)).all()
    ]


@router.post("/kb")
def create_article(body: ArticleIn, s: Session = Depends(get_db)) -> dict:
    if body.category not in CATEGORIES:
        raise HTTPException(422, "неизвестная категория")
    a = KnowledgeArticle(category=body.category, title=body.title, body=body.body)
    s.add(a)
    s.commit()
    return {"id": a.id, "category": a.category, "title": a.title, "body": a.body}


@router.delete("/kb/{article_id}")
def delete_article(article_id: int, s: Session = Depends(get_db)) -> dict:
    a = s.get(KnowledgeArticle, article_id)
    if a is None:
        raise HTTPException(404, "статья не найдена")
    s.delete(a)
    s.commit()
    return {"ok": True}


@router.get("/routing-log")
def routing_log(limit: int = Query(50, le=500), s: Session = Depends(get_db)) -> list[dict]:
    ops = {o.id: o.name for o in s.scalars(select(Operator)).all()}
    rows = s.scalars(select(RoutingDecision).order_by(RoutingDecision.id.desc()).limit(limit)).all()
    return [
        {
            "id": r.id,
            "ticket_id": r.ticket_id,
            "strategy": r.strategy,
            "mode": r.mode,
            "operator_id": r.operator_id,
            "operator_name": ops.get(r.operator_id),
            "reason": r.reason,
            "candidates": r.candidates,
            "created_at": r.created_at.isoformat() + "Z",
        }
        for r in rows
    ]


@router.get("/agent-runs")
def agent_runs(
    agent: str | None = None, ticket_id: int | None = None, limit: int = Query(50, le=500), s: Session = Depends(get_db)
) -> list[dict]:
    q = select(AgentRun).order_by(AgentRun.id.desc()).limit(limit)
    if agent:
        q = q.where(AgentRun.agent == agent)
    if ticket_id:
        q = q.where(AgentRun.ticket_id == ticket_id)
    return [
        {
            "id": r.id,
            "agent": r.agent,
            "ticket_id": r.ticket_id,
            "mode": r.mode,
            "input": r.input,
            "output": r.output,
            "error": r.error,
            "duration_ms": r.duration_ms,
            "created_at": r.created_at.isoformat() + "Z",
        }
        for r in s.scalars(q).all()
    ]


@router.post("/dispatch")
async def dispatch() -> dict:
    """Force a queue pass (useful after changing the strategy)."""
    return {"assigned": await pipeline.dispatch_queue()}

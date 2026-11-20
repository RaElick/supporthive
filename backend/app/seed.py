"""Demo data: operators, knowledge base, two weeks of ticket history.

Run standalone to reset:  python -m app.seed --reset
"""

import random
import sys
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .agents import quality, triage
from .agents.copilot import Turn
from .models import (
    Customer,
    KnowledgeArticle,
    Message,
    Operator,
    QualityReview,
    RoutingDecision,
    Ticket,
    utcnow,
)
from .security import hash_password

DEMO_PASSWORD = "demo1234"

OPERATORS = [
    # login, name, skills, max_concurrent, quality profile (0..1: higher = better operator)
    ("anna", "Анна Смирнова", ["billing", "returns"], 3, 0.9),
    ("igor", "Игорь Петров", ["technical", "account"], 4, 0.8),
    ("maria", "Мария Козлова", ["delivery", "returns"], 3, 0.85),
    ("dmitry", "Дмитрий Волков", ["technical"], 2, 0.6),
    ("olga", "Ольга Новикова", ["general", "delivery", "billing"], 4, 0.7),
]

KB = [
    ("billing", "Двойное списание", "Если деньги списались дважды, второе списание — это временная блокировка банка. Она снимается автоматически в течение 3 рабочих дней. Если через 3 дня деньги не вернулись, пришлите выписку, и мы инициируем возврат вручную."),
    ("billing", "Способы оплаты", "Мы принимаем банковские карты, СБП и оплату при получении. Оплата при получении доступна для заказов до 50 000 ₽. Чек приходит на почту сразу после оплаты."),
    ("billing", "Не прошла оплата", "Если оплата не проходит, проверьте лимиты карты и попробуйте оплатить через СБП. Заказ хранится неоплаченным 24 часа, после чего отменяется автоматически."),
    ("delivery", "Сроки доставки", "Курьерская доставка по городу занимает 1–2 дня, в регионы — 3–7 дней. Трек-номер приходит в СМС и отображается в личном кабинете в разделе «Мои заказы»."),
    ("delivery", "Заказ задерживается", "Если заказ не пришёл в указанный срок, проверьте статус по трек-номеру. При задержке больше 2 дней мы связываемся со службой доставки и выясняем причину; при утере заказа возвращаем деньги полностью."),
    ("delivery", "Пункты выдачи", "Заказ хранится в пункте выдачи 5 дней. Срок хранения можно продлить один раз на 3 дня в личном кабинете."),
    ("technical", "Приложение не открывается", "Обновите приложение до последней версии и перезапустите телефон. Если не помогло — очистите кэш приложения в настройках телефона. Поддерживаются Android 9+ и iOS 15+."),
    ("technical", "Ошибка при оформлении заказа", "Ошибка при оформлении чаще всего связана с устаревшей версией страницы. Обновите страницу (Ctrl+F5) или попробуйте другой браузер. Если ошибка повторяется, пришлите скриншот."),
    ("account", "Восстановление пароля", "Нажмите «Забыли пароль?» на странице входа и введите почту. Ссылка действует 1 час. Если письмо не пришло, проверьте папку «Спам»."),
    ("account", "Не приходит СМС-код", "Код приходит в течение 2 минут. Повторно запросить код можно через 60 секунд. Если код не приходит, проверьте, что номер указан в формате +7, и что входящие СМС не заблокированы."),
    ("account", "Блокировка аккаунта", "Аккаунт блокируется на 30 минут после 5 неудачных попыток входа. После этого войдите заново или восстановите пароль."),
    ("returns", "Возврат товара", "Товар надлежащего качества можно вернуть в течение 14 дней, если сохранены упаковка и товарный вид. Оформите возврат в личном кабинете: «Мои заказы» → «Вернуть». Деньги возвращаются на карту в течение 10 дней."),
    ("returns", "Брак", "Если товар бракованный, оформите возврат с пометкой «Брак» и приложите фото. Мы заберём товар курьером бесплатно и вернём деньги или заменим товар."),
    ("general", "Режим работы поддержки", "Поддержка работает ежедневно с 8:00 до 22:00 по Москве. Вне рабочего времени обращения принимаются, ответ придёт утром."),
]

CUSTOMER_TEXTS = {
    "billing": [
        "Здравствуйте, у меня деньги списались дважды за один заказ!",
        "Не проходит оплата картой, пишет ошибку банка",
        "Подскажите, можно ли оплатить при получении?",
        "Списали деньги, а заказ не оформился. Срочно разберитесь",
    ],
    "delivery": [
        "Заказ должен был прийти вчера, но курьер так и не приехал",
        "Где посмотреть трек-номер посылки?",
        "Посылка уже неделю висит в статусе отправлен, что происходит?",
        "Можно продлить хранение в пункте выдачи?",
    ],
    "technical": [
        "Приложение вылетает при открытии корзины",
        "Сайт не грузит страницу оформления заказа, ошибка 500",
        "Не работает поиск в приложении",
    ],
    "account": [
        "Не могу войти в аккаунт, не приходит смс код",
        "Забыл пароль, письмо для восстановления не приходит",
        "Аккаунт заблокирован, что делать?",
    ],
    "returns": [
        "Хочу вернуть товар, не подошёл размер",
        "Пришёл брак, экран с трещиной. Хочу обмен",
        "Как оформить возврат денег за отменённый товар?",
    ],
    "general": [
        "Подскажите, во сколько работает поддержка?",
        "Хотел бы узнать, есть ли у вас программа лояльности",
    ],
}

NAMES = ["Алексей", "Екатерина", "Сергей", "Наталья", "Павел", "Юлия", "Андрей", "Ирина", "Михаил", "Татьяна", "Никита", "Светлана"]


def _reply_from_kb(category: str, articles: list[KnowledgeArticle], rng: random.Random) -> str:
    pool = [a for a in articles if a.category == category] or articles
    a = rng.choice(pool)
    return a.body.split(". ")[0] + "."


def seed_history(s: Session, rng: random.Random, days: int = 14, per_day: tuple[int, int] = (6, 14)) -> None:
    ops = s.scalars(select(Operator).where(Operator.is_admin.is_(False))).all()
    profile = {login: p for login, *_rest, p in OPERATORS}
    articles = s.scalars(select(KnowledgeArticle)).all()
    now = utcnow()
    for d in range(days, 0, -1):
        for _ in range(rng.randint(*per_day)):
            created = (now - timedelta(days=d)).replace(hour=rng.randint(8, 21), minute=rng.randint(0, 59))
            cat = rng.choices(list(CUSTOMER_TEXTS), weights=[3, 4, 2, 2, 2, 1])[0]
            text = rng.choice(CUSTOMER_TEXTS[cat])
            tri = triage.heuristic(text)
            channel = rng.choice(["widget", "telegram", "widget"])
            cust = Customer(name=rng.choice(NAMES), channel=channel, external_id=f"seed-{rng.random():.12f}", created_at=created)
            s.add(cust)
            s.flush()

            # Historical routing: mostly by skill, sometimes random (models the pre-agent era).
            specialists = [o for o in ops if tri.category in o.skills]
            strategy = "queue" if rng.random() < 0.4 or not specialists else "skills"
            op = rng.choice(ops) if strategy == "queue" else rng.choice(specialists)
            skilled = tri.category in op.skills
            q = profile.get(op.login, 0.7) + (0.1 if skilled else -0.25)

            frt = rng.randint(20, 240) if q > 0.6 else rng.randint(120, 1200)
            res_min = rng.randint(5, 25) if skilled else rng.randint(15, 70)
            if rng.random() < 0.08:
                res_min += rng.randint(40, 120)  # the occasional stuck ticket → anomaly
            t = Ticket(
                customer_id=cust.id,
                channel=channel,
                status="closed",
                category=tri.category,
                priority=tri.priority,
                summary=tri.summary,
                operator_id=op.id,
                created_at=created,
                assigned_at=created + timedelta(seconds=rng.randint(2, 15)),
                first_response_at=created + timedelta(seconds=frt),
                closed_at=created + timedelta(minutes=res_min),
                resolved=rng.random() < q + 0.1,
            )
            s.add(t)
            s.flush()
            greet = "Здравствуйте! " if rng.random() < q + 0.1 else ""
            turns = [
                Message(ticket_id=t.id, sender="customer", text=text, created_at=created),
                Message(ticket_id=t.id, sender="system", text=f"К диалогу подключился оператор {op.name}", created_at=t.assigned_at),
                Message(ticket_id=t.id, sender="operator", operator_id=op.id, text=greet + _reply_from_kb(tri.category, articles, rng), created_at=t.first_response_at),
            ]
            final = "Спасибо, помогли!" if t.resolved else "Это не решает мою проблему."
            turns.append(Message(ticket_id=t.id, sender="customer", text=final, created_at=t.first_response_at + timedelta(minutes=2)))
            if rng.random() < 0.85:
                base = 4.6 if t.resolved else 2.2
                t.rating = max(1, min(5, round(rng.gauss(base + (q - 0.75) * 2, 0.7))))
            s.add_all(turns)
            s.add(
                RoutingDecision(
                    ticket_id=t.id,
                    strategy=strategy,
                    mode="heuristic",
                    operator_id=op.id,
                    candidates=[],
                    reason="исторические данные (сид)",
                    created_at=t.assigned_at,
                )
            )
            rev = quality.heuristic(
                [Turn(m.sender, m.text) for m in turns if m.sender != "system"], float(frt), t.rating
            )
            s.add(QualityReview(ticket_id=t.id, mode="heuristic", created_at=t.closed_at, **rev.model_dump()))


def seed_if_empty(s: Session, with_history: bool = True) -> bool:
    if s.scalar(select(Operator).limit(1)) is not None:
        return False
    s.add(Operator(login="admin", name="Администратор", password_hash=hash_password("admin1234"), is_admin=True, skills=[]))
    pw = hash_password(DEMO_PASSWORD)
    for login, name, skills, maxc, _ in OPERATORS:
        s.add(Operator(login=login, name=name, password_hash=pw, skills=skills, max_concurrent=maxc))
    for cat, title, body in KB:
        s.add(KnowledgeArticle(category=cat, title=title, body=body))
    s.flush()
    if with_history:
        seed_history(s, random.Random(42))
    return True


if __name__ == "__main__":
    from .db import Base, engine, init_db, session_scope

    if "--reset" in sys.argv:
        Base.metadata.drop_all(engine)
    init_db()
    with session_scope() as s:
        print("seeded" if seed_if_empty(s) else "database not empty, skipped (use --reset)")

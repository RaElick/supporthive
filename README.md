# SupportHive

Мультиагентная CRM клиентской поддержки: обращения из Telegram и виджета на сайте → агент-классификатор →
агент-маршрутизатор (по компетенциям операторов) → рабочее место оператора с агентом-подсказчиком →
оценка клиента и агент-аналитик качества → дашборды для администратора.

Подробнее: [docs/PROJECT_PROPOSAL.md](docs/PROJECT_PROPOSAL.md), [архитектура](docs/ARCHITECTURE.md),
[исследование](docs/RESEARCH.md), [настройка харнеса](docs/HARNESS.md).

## Запуск

Нужны Python 3.11+ и Node 20+.

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # Linux/macOS: .venv/bin/pip
cp .env.example .env                       # по желанию: LLM и Telegram
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

```bash
cd frontend
npm install
npm run build        # после этого всё доступно на http://localhost:8000
# или для разработки: npm run dev  → http://localhost:5173
```

При первом запуске создаётся демо-БД с операторами, базой знаний и историей за 14 дней.

| Роль | Где | Логин |
|---|---|---|
| Клиент | `/shop` | — |
| Оператор | `/desk` | `anna`, `igor`, `maria`, `dmitry`, `olga` / `demo1234` |
| Администратор | `/admin` | `admin` / `admin1234` |

Без `LLM_BASE_URL` агенты работают на эвристиках — всё функционирует, но ответы шаблонные.
С LLM: укажите в `.env` любой OpenAI-совместимый endpoint (OpenAI, OpenRouter, Ollama `http://localhost:11434/v1`, …).

Telegram: создайте бота у @BotFather и укажите `TELEGRAM_BOT_TOKEN`.

## Тесты и эксперименты

```bash
cd backend
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m app.simulate --tickets 600 --seed 1
.venv/Scripts/python -m app.seed --reset
```

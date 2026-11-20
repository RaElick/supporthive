# SupportHive — инструкции для агента

Мультиагентная CRM клиентской поддержки (учебный проект магистратуры, ГУАП).
Описание продукта: `docs/PROJECT_PROPOSAL.md`. Архитектура: `docs/ARCHITECTURE.md`.
Принятые решения: `docs/DECISIONS.md`. Текущий статус: `docs/PROGRESS.md`.

## Команды

```bash
# backend (из backend/)
.venv/Scripts/python -m pytest -q          # тесты, обязательно перед завершением задачи
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
.venv/Scripts/python -m app.seed --reset   # пересоздать демо-БД
.venv/Scripts/python -m app.simulate       # сравнение стратегий маршрутизации

# frontend (из frontend/)
npm run dev      # :5173, проксирует /api и /ws на :8000
npm run build    # tsc + vite; бэкенд отдаёт frontend/dist на :8000
```

## Жёсткие правила архитектуры

1. **Агенты чистые.** `app/agents/{triage,router,copilot,quality}.py` получают данные (dataclass/pydantic),
   возвращают результат. Они не импортируют `db`, `models.Session`, `api`. Чтение/запись БД и публикация
   событий — только в `app/agents/pipeline.py`.
2. **Каждый агент работает без LLM.** У каждого есть `heuristic(...)`; `run(llm=None, ...)` обязан
   вернуть эвристический результат. Любая ошибка LLM в `pipeline.py` → откат на эвристику + запись `error` в `AgentRun`.
3. **Контекст только через `ContextBuilder`** (`app/agents/context.py`). Никакой ручной склейки промптов.
   Фрагменты, которые нельзя терять (кандидаты маршрутизатора, последнее сообщение клиента) — `pinned=True`.
4. **Ответ LLM всегда валидируется** Pydantic-схемой (`LLMClient.complete_json`). Решение LLM-маршрутизатора
   проверяется по списку кандидатов.
5. **Без LangChain/LangGraph/агентных фреймворков** — это осознанное требование курса.
6. Бизнес-логика — в `app/services/`. API-роуты тонкие.
7. Время — naive UTC (`models.utcnow()`), в JSON отдаётся с суффиксом `Z`.
8. События клиенту — только через `pipeline.to_customer` / `publish_ticket` (канал `customer:<id>`),
   операторам — канал `operators`.

## Как работать

- Новое поведение → сначала тест в `backend/tests/` (unit для агентов, `test_flow.py` для сценариев через API).
- Не ослабляй тесты, чтобы они прошли. Если тест неверен — объясни почему, прежде чем менять.
- Фронт: TypeScript strict, `verbatimModuleSyntax` → типы импортируются через `import type`.
  Эффекты React возвращают только функцию очистки или ничего (не `() => expr`).
- Ссылки внутри вложенных `<Routes>` — абсолютные (`/admin/...`).
- UI и тексты — на русском.
- Архитектурное решение → одна запись в `docs/DECISIONS.md`. Завершённый этап → `docs/PROGRESS.md`.

## Где что

| Задача | Файл |
|---|---|
| Новый агент | skill `add-agent` |
| Новая стратегия маршрутизации | skill `add-routing-strategy` |
| Метрика дашборда | `app/services/metrics.py` + `frontend/src/pages/Admin.tsx` |
| Демо-данные | `app/seed.py` |
| Эксперимент с контекстом | `ContextBuilder(placement=..., budget_tokens=...)`, настройка `copilot_placement` |

---
name: add-agent
description: Как добавить нового LLM-агента в SupportHive (например, агент суммаризации, детектор эскалации, переводчик). Использовать, когда задача требует нового агента в app/agents.
---

# Добавление агента

1. **Модуль** `backend/app/agents/<name>.py`, чистый (без БД):
   - Pydantic-модель результата с ограничениями (`Literal`, `Field(max_length=...)`, `ge/le`).
   - `SYSTEM` — инструкция на русском, с перечислением допустимых значений.
   - `heuristic(...) -> Result` — детерминированный путь без LLM. Обязателен.
   - `async def run(llm: LLMClient | None, ...) -> tuple[Result, str, dict]`:
     ```python
     if llm is None:
         return heuristic(...), "heuristic", {}
     ctx = ContextBuilder(get_settings().llm_context_budget_tokens)
     ctx.instructions(SYSTEM + "\nОтветь JSON по схеме: " + schema_hint(Result))
     ctx.add("ключ", текст, priority=..., pinned=...)   # по фрагменту на источник
     res = await llm.complete_json(ctx.build(задача), Result)
     return res, "llm", ctx.report.as_dict()
     ```
   - После LLM — пост-валидация по домену (как `quality.run` фильтрует flags, `router` — кандидатов).
2. **Оркестрация** в `backend/app/agents/pipeline.py`: прочитать входы в `session_scope`, вызвать `run`,
   на исключение — `heuristic` + `err`, затем `_trace(...)`, затем записать результат и `hub.publish`.
3. **Хранение**, если нужно: таблица в `models.py` (+ `init_db` создаст её; миграций пока нет —
   для существующей БД `python -m app.seed --reset`).
4. **Тесты** в `backend/tests/test_agents.py`: эвристика на нескольких входах; путь с `FakeLLM`
   (валидный ответ и мусор/исключение → откат).
5. **UI**: показать результат в `OperatorDesk.tsx` (правая панель) или `Admin.tsx`; агент автоматически
   появится в «Трассировке агентов», если имя добавить в список фильтра.
6. Запись в `docs/DECISIONS.md`, если агент меняет поток обработки обращения.

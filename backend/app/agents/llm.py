"""Minimal client for any OpenAI-compatible Chat Completions endpoint.

Works with OpenAI, OpenRouter, YandexGPT/GigaChat proxies, vLLM, Ollama (`/v1`), LM Studio...
No SDK: one POST, JSON output validated by a Pydantic model, one corrective retry.
"""

import json
import logging
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> str:
    """Models often wrap JSON in ```json fences or add prose; take the outermost object."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError(f"no JSON object in model output: {text[:200]!r}")
    return text[start : end + 1]


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )

    async def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        resp = await self._client.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "messages": messages, "temperature": temperature},
        )
        if resp.status_code >= 400:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"unexpected LLM response shape: {resp.text[:300]}") from e

    async def complete_json(self, messages: list[dict], schema: type[T], retries: int = 1) -> T:
        msgs = list(messages)
        last_err: Exception | None = None
        for _ in range(retries + 1):
            raw = await self.chat(msgs)
            try:
                return schema.model_validate(json.loads(_extract_json(raw)))
            except (LLMError, ValueError, ValidationError) as e:
                last_err = e
                msgs = msgs + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": "Ответ не прошёл проверку: "
                        f"{str(e)[:400]}. Верни ТОЛЬКО JSON-объект по схеме.",
                    },
                ]
        raise LLMError(f"model failed to produce valid JSON: {last_err}")

    async def aclose(self) -> None:
        await self._client.aclose()


_llm: LLMClient | None = None
_initialised = False


def get_llm() -> LLMClient | None:
    """Shared client, or None when no endpoint is configured (agents fall back to heuristics)."""
    global _llm, _initialised
    if not _initialised:
        s = get_settings()
        _llm = LLMClient(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_timeout_s) if s.llm_base_url else None
        _initialised = True
    return _llm


def set_llm(client: LLMClient | None) -> None:
    """Override the shared client (tests inject fakes here)."""
    global _llm, _initialised
    _llm, _initialised = client, True


def schema_hint(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), ensure_ascii=False)

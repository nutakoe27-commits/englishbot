"""
llm_providers.py — LLM-провайдер для ответов репетитора.

Единственный бэкенд — локальный vLLM на V100 (OpenAI-совместимый API),
пробрасываемый на VPS через SSH-reverse-tunnel.

Контракт:
    async def complete(user_text: str, history: list[dict]) -> str

История приходит без system-сообщения; SYSTEM_PROMPT добавляется внутри провайдера.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

import httpx

from .config import settings

logger = logging.getLogger(__name__)


# ─── Общий контракт ──────────────────────────────────────────────────────────

class LLMProvider(Protocol):
    async def complete(
        self,
        user_text: str,
        history: list[dict],
        system_prompt: str,
    ) -> str: ...


# ─── vLLM (OpenAI-совместимый) ───────────────────────────────────────────────

# Qwen3 — reasoning-модель. Без подавления она выдаёт CoT в content
# ("Thinking Process:...", <think>...</think>). Для голосового бота это
# катастрофа: TTS озвучит размышления. Подавляем двумя методами:
#   1) префикс "/no_think\n" в user-реплике — Qwen3 tokenizer отключает thinking;
#   2) защитный strip <think>...</think> и префиксов типа "Thinking Process:"
#      в ответе — если первый метод не сработал.

_THINK_TAG_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_THINK_PREFIX_RE = re.compile(
    r"^\s*(Thinking Process|Let me think|Okay, so|Alright,)\b.*?\n\n",
    re.DOTALL | re.IGNORECASE,
)


def _strip_reasoning(text: str) -> str:
    """Вырезает reasoning-мусор, если он просочился в ответ."""
    cleaned = _THINK_TAG_RE.sub("", text)
    cleaned = _THINK_PREFIX_RE.sub("", cleaned)
    return cleaned.strip()


class VLLMProvider:
    """
    vLLM через OpenAI Chat Completions API.
    base_url должен оканчиваться на /v1
    (например, http://host.docker.internal:23333/v1).
    """

    def __init__(self, base_url: str, model_name: str, api_key: str = "not-needed"):
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key

    @staticmethod
    def _to_openai(history: list[dict]) -> list[dict]:
        """Формат {role, text} → OpenAI {role, content}."""
        out = []
        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("text") or msg.get("content", "")
            out.append({"role": role, "content": content})
        return out

    async def complete(
        self,
        user_text: str,
        history: list[dict],
        system_prompt: str,
    ) -> str:
        # /no_think подавляет reasoning Qwen3 на токенизерном уровне.
        # Ставится в user-реплику (в system не работает).
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(self._to_openai(history))
        messages.append({"role": "user", "content": f"/no_think\n{user_text}"})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.6,
            # Повышенный лимит на случай, если модель всё же потратит несколько
            # токенов на <think></think> — чистый ответ влезет с запасом.
            "max_tokens": 400,
            "stream": False,
            # Жёсткий выключатель reasoning для Qwen3 через chat template.
            # Работает только если vLLM запущен с --reasoning-parser qwen3.
            # Это strict-switch (сильнее чем soft /no_think в промпте).
            "chat_template_kwargs": {"enable_thinking": False},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.error("vLLM HTTP %s: %s", resp.status_code, resp.text)
                resp.raise_for_status()
            data = resp.json()

        try:
            raw = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            logger.error("Неожиданный формат ответа vLLM: %s — %s", data, exc)
            return "Sorry, I didn't catch that. Could you say it again?"

        cleaned = _strip_reasoning(raw)
        if not cleaned:
            logger.warning("vLLM вернул пустой ответ после зачистки reasoning. raw=%r", raw[:200])
            return "Sorry, could you say that again?"
        return cleaned


# ─── Фабрика ─────────────────────────────────────────────────────────────────

def get_llm_provider() -> LLMProvider:
    """Создаёт VLLMProvider из настроек. Требует VLLM_BASE_URL и VLLM_MODEL_NAME."""
    if not settings.VLLM_BASE_URL or not settings.VLLM_MODEL_NAME:
        raise RuntimeError(
            "LLM не сконфигурирован: задайте VLLM_BASE_URL и VLLM_MODEL_NAME в .env"
        )
    logger.warning(
        "[LLM] base_url=%s model=%s",
        settings.VLLM_BASE_URL, settings.VLLM_MODEL_NAME,
    )
    return VLLMProvider(
        base_url=settings.VLLM_BASE_URL,
        model_name=settings.VLLM_MODEL_NAME,
        api_key=settings.VLLM_API_KEY or "not-needed",
    )

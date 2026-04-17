"""
llm_providers.py — абстракция над провайдерами LLM.

Позволяет переключать LLM-бэкенд через переменную окружения LLM_PROVIDER:
  - "yandex" — YandexGPT (HTTP, foundationModels/v1/completion)
  - "vllm"   — OpenAI-совместимый endpoint (локальный vLLM на V100 через Cloudflare Tunnel)

Оба варианта возвращают одинаковую строку — готовый ответ ассистента.
Системный промпт добавляется внутри провайдера, history приходит без system.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

import httpx

from .config import SYSTEM_PROMPT, settings

logger = logging.getLogger(__name__)


# ─── Общий контракт ──────────────────────────────────────────────────────────

class LLMProvider(Protocol):
    async def complete(self, user_text: str, history: list[dict]) -> str: ...


# ─── YandexGPT ───────────────────────────────────────────────────────────────

YANDEXGPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


class YandexGPTProvider:
    """YandexGPT через HTTP (формат сообщений: role + text)."""

    def __init__(self, api_key: str, folder_id: str, model_uri: str = "yandexgpt-lite/latest"):
        self.api_key = api_key
        self.folder_id = folder_id
        self.model_uri = f"gpt://{folder_id}/{model_uri}"

    async def complete(self, user_text: str, history: list[dict]) -> str:
        messages = [{"role": "system", "text": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "text": user_text})

        payload = {
            "modelUri": self.model_uri,
            "completionOptions": {
                "stream": False,
                "temperature": 0.6,
                "maxTokens": 200,
            },
            "messages": messages,
        }
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "x-folder-id": self.folder_id,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(YANDEXGPT_URL, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.error("YandexGPT HTTP %s: %s", resp.status_code, resp.text)
                resp.raise_for_status()
            data = resp.json()

        try:
            return data["result"]["alternatives"][0]["message"]["text"].strip()
        except (KeyError, IndexError) as exc:
            logger.error("Неожиданный формат ответа YandexGPT: %s — %s", data, exc)
            return "Sorry, I didn't catch that. Could you say it again?"


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
    base_url должен оканчиваться на /v1 (например, https://abc.cfargotunnel.com/v1).
    """

    def __init__(self, base_url: str, model_name: str, api_key: str = "not-needed"):
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key

    @staticmethod
    def _to_openai(history: list[dict]) -> list[dict]:
        """YandexGPT формат {role, text} → OpenAI {role, content}."""
        out = []
        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("text") or msg.get("content", "")
            out.append({"role": role, "content": content})
        return out

    async def complete(self, user_text: str, history: list[dict]) -> str:
        # /no_think подавляет reasoning Qwen3 на токенизерном уровне.
        # Ставится в user-реплику (в system не работает).
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
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
    """
    Возвращает провайдера согласно settings.LLM_PROVIDER.
    Значения: "yandex" (по умолчанию) или "vllm".
    """
    provider = (settings.LLM_PROVIDER or "yandex").lower()

    if provider == "vllm":
        if not settings.VLLM_BASE_URL or not settings.VLLM_MODEL_NAME:
            logger.error(
                "LLM_PROVIDER=vllm, но VLLM_BASE_URL / VLLM_MODEL_NAME не заданы. "
                "Откат на YandexGPT."
            )
        else:
            logger.warning(
                "[LLM] провайдер=vllm base_url=%s model=%s",
                settings.VLLM_BASE_URL, settings.VLLM_MODEL_NAME,
            )
            return VLLMProvider(
                base_url=settings.VLLM_BASE_URL,
                model_name=settings.VLLM_MODEL_NAME,
                api_key=settings.VLLM_API_KEY or "not-needed",
            )

    # fallback: YandexGPT
    if not settings.YC_API_KEY or not settings.YC_FOLDER_ID:
        raise RuntimeError(
            "YandexGPT выбран провайдером, но YC_API_KEY / YC_FOLDER_ID не заданы"
        )
    logger.warning("[LLM] провайдер=yandex модель=yandexgpt-lite")
    return YandexGPTProvider(
        api_key=settings.YC_API_KEY,
        folder_id=settings.YC_FOLDER_ID,
    )

"""
llm_providers.py — LLM-провайдер для ответов репетитора.

Единственный бэкенд — локальный vLLM на V100 (OpenAI-совместимый API),
пробрасываемый на VPS через SSH-reverse-tunnel.

Контракт:
    async def complete(user_text: str, history: list[dict]) -> str

История приходит без system-сообщения; SYSTEM_PROMPT добавляется внутри провайдера.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import AsyncIterator, Protocol

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

    def stream(
        self,
        user_text: str,
        history: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]: ...


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
        """Не-стриминговый ответ. Под капотом дёргаем stream() и склеиваем
        дельты — vLLM с --reasoning-parser qwen3 на stream=False часто
        возвращает пустой content для коротких/нестандартных промптов
        (translate-эндпоинт, chat-режим Mini App). Стрим работает.
        """
        chunks: list[str] = []
        async for delta in self.stream(
            user_text=user_text,
            history=history,
            system_prompt=system_prompt,
        ):
            chunks.append(delta)
        raw = "".join(chunks)
        cleaned = _strip_reasoning(raw)
        if not cleaned:
            logger.warning(
                "[complete] пустой ответ от LLM (raw=%r history_len=%d)",
                raw[:200], len(history),
            )
            return "Sorry, could you say that again?"
        return cleaned

    async def stream(
        self,
        user_text: str,
        history: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        """Stream-вариант complete(): yield'ит дельты текста по мере поступления.

        В voice-режиме это даёт +1.5-2 секунды к ощущаемой скорости тьютора:
        первое предложение можно начать синтезировать в TTS пока LLM ещё
        генерирует следующие. Reasoning-tag <think>...</think> Qwen3 здесь
        не страпится — мы выключаем reasoning через chat_template_kwargs.
        """
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(self._to_openai(history))
        messages.append({"role": "user", "content": f"/no_think\n{user_text}"})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.6,
            "max_tokens": 400,
            "stream": True,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}/chat/completions"
        # Connect/write/pool — короткие. Read 120с — это «успеть прочитать
        # один чанк»; реальная защита от зависания между токенами стоит
        # ниже через asyncio.wait_for на каждом next(line_iter).
        timeout = httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=5.0)
        # Если LLM не выдал новый токен за это время — стрим считаем
        # зависшим, прерываем и возвращаем то что успели накопить. Это
        # критично для длинных диалогов (vLLM иногда замирает на 30+ сек
        # на больших контекстах).
        INTER_TOKEN_TIMEOUT_SEC = 12.0
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    logger.error("vLLM HTTP %s: %s", resp.status_code, body[:500])
                    resp.raise_for_status()
                line_iter = resp.aiter_lines()
                while True:
                    try:
                        line = await asyncio.wait_for(
                            line_iter.__anext__(),
                            timeout=INTER_TOKEN_TIMEOUT_SEC,
                        )
                    except StopAsyncIteration:
                        return
                    except asyncio.TimeoutError:
                        logger.error(
                            "[LLM stream] нет новых токенов за %.0fс — прерываю",
                            INTER_TOKEN_TIMEOUT_SEC,
                        )
                        return
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data_str)
                    except Exception:
                        continue
                    try:
                        delta = chunk["choices"][0]["delta"].get("content", "")
                    except (KeyError, IndexError, AttributeError):
                        delta = ""
                    if delta:
                        yield delta


# ─── Перевод одного слова (для тапа в чате) ─────────────────────────────────

_TRANSLATE_SYSTEM_PROMPT = (
    "You help a Russian learner of English understand a word from context. "
    "When asked, reply with ONLY a short Russian translation of the word.\n\n"
    "Format: <main translation> | <alt1> | <alt2>\n"
    "1-3 options total, separated by |. Lowercase. No prose, no English, "
    "no explanations.\n\n"
    "Example:\n"
    "User: What does \"bank\" mean in: \"I walked along the river bank.\"?\n"
    "Assistant: берег | побережье\n\n"
    "Example:\n"
    "User: What does \"grabbing\" mean in: \"I'm grabbing some snacks.\"?\n"
    "Assistant: хватать | брать"
)

# Парсим ответ вида "берег | побережье | …" → [primary, alt, alt].
_PIPE_SPLIT_RE = re.compile(r"\s*\|\s*")


async def translate_word(
    llm: "VLLMProvider",
    *,
    word: str,
    context: str,
    target_lang: str = "ru",
) -> list[str]:
    """Переводит одно слово с учётом контекста. Возвращает список переводов:
    [primary, alt1, alt2, ...]. На ошибку — пустой список.

    target_lang пока всегда 'ru' (поле зарезервировано на будущее).
    """
    word_clean = (word or "").strip()
    if not word_clean:
        return []
    context_clean = (context or "").strip()
    user_payload = (
        f'What does "{word_clean}" mean in: "{context_clean or "(no context)"}"?'
    )

    # Идём через llm.complete() — он использует enable_thinking=False и
    # /no_think, что в проде работает на коротких разговорных промптах.
    # Промпт оформлен как диалог («помоги юзеру с английским»), а не
    # JSON-инструкция — на ней Qwen3 уходила в reasoning loop / молчала.
    try:
        raw = await llm.complete(
            user_text=user_payload,
            history=[],
            system_prompt=_TRANSLATE_SYSTEM_PROMPT,
        )
    except Exception as exc:
        logger.warning("[translate] LLM error for %r: %r", word_clean, exc)
        return []

    cleaned = _strip_reasoning(raw).strip()
    if not cleaned or cleaned.lower().startswith("sorry"):
        logger.warning("[translate] пустой/sorry ответ для %r (raw=%r)", word_clean, raw[:200])
        return []

    # Берём только первую строку (модель иногда добавляет пояснения снизу).
    first_line = cleaned.splitlines()[0].strip().strip(".")
    parts = [p.strip().strip(".,;") for p in _PIPE_SPLIT_RE.split(first_line) if p.strip()]
    # Убираем дубликаты, оставляя порядок.
    seen: set[str] = set()
    result: list[str] = []
    for p in parts[:3]:
        key = p.lower()
        if key and key not in seen:
            seen.add(key)
            result.append(p)
    return result


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

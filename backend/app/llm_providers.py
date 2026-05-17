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
from typing import AsyncIterator, Optional, Protocol

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
    "You are a concise English-to-Russian word translator. "
    "Given an English word and the sentence it appeared in, translate the "
    "word into Russian, considering the context of the sentence.\n\n"
    "Return ONLY a JSON object with this exact shape:\n"
    '{"primary": "...", "alternatives": ["...", "..."]}\n\n'
    "Rules:\n"
    "- \"primary\": the single most likely Russian translation for this "
    "context (1-3 words). Lowercase. Initial form (lemma) when reasonable.\n"
    "- \"alternatives\": up to 2 other plausible Russian options if the "
    "word is ambiguous. Empty list if there are no good alternatives.\n"
    "- NO prose, NO markdown fences, NO explanations.\n"
    "- If the word is a proper noun, brand, or clearly untranslatable — "
    'return it transliterated as "primary" and empty alternatives.'
)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


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
        f"Word: {word_clean}\n"
        f"Sentence: {context_clean or '(no context)'}\n"
        f"JSON now."
    )

    payload = {
        "model": llm.model_name,
        "messages": [
            {"role": "system", "content": _TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ],
        "temperature": 0.2,
        # Большой буфер: reasoning-блок <think>...</think> Qwen3 может
        # быть в сотни токенов; JSON приходит после него.
        "max_tokens": 800,
        "stream": False,
        # Намеренно НЕ отключаем thinking. Эмпирически: с enable_thinking=
        # False + /no_think Qwen3 на JSON-промпте возвращает пустой content
        # и finish_reason='stop' — модель просто ничего не генерирует.
        # Reasoning-блок _strip_reasoning вырежет позже, JSON останется.
    }
    headers = {
        "Authorization": f"Bearer {llm.api_key}",
        "Content-Type": "application/json",
    }

    url = f"{llm.base_url}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        message = data["choices"][0]["message"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        logger.warning("[translate] LLM error for %r: %s", word_clean, exc)
        return []

    # vLLM с --reasoning-parser qwen3 раздваивает ответ: финальный ответ →
    # content, размышления → reasoning (или reasoning_content в старых
    # версиях). Ищем JSON в обоих полях.
    content_raw = (message.get("content") or "").strip()
    reasoning_raw = (
        message.get("reasoning") or message.get("reasoning_content") or ""
    ).strip()

    def _try_extract_json(text: str) -> Optional[dict]:
        if not text:
            return None
        cleaned = _strip_reasoning(text)
        if not cleaned:
            return None
        m = _JSON_OBJECT_RE.search(cleaned)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

    parsed = _try_extract_json(content_raw) or _try_extract_json(reasoning_raw)
    if parsed is None:
        # Логируем весь message + finish_reason — позволяет понять, что vLLM
        # реально вернул (length cutoff, отказ, пустое поле и т.п.).
        finish = data["choices"][0].get("finish_reason")
        logger.warning(
            "[translate] не нашёл JSON для %r finish=%s message=%r",
            word_clean, finish, message,
        )
        return []

    primary = (parsed.get("primary") or "").strip()
    alts_raw = parsed.get("alternatives") or []
    if not primary:
        return []
    result = [primary]
    if isinstance(alts_raw, list):
        for alt in alts_raw[:2]:
            if not isinstance(alt, str):
                continue
            alt_clean = alt.strip()
            if alt_clean and alt_clean.lower() != primary.lower():
                result.append(alt_clean)
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

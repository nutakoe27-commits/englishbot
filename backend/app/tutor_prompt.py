"""
tutor_prompt.py — билдер system-промпта и приветствия для AI-репетитора.

Параметры сессии (`SessionSettings`) приходят из Mini App через query-параметры
WebSocket и определяют поведение собеседника:

- level:         уровень английского учащегося (A2 / B1 / B2 / C1)
- role:          роль собеседника — ключ из ROLE_PRESETS, либо "custom"
- role_custom:   свободный текст роли (используется если role == "custom")
- length:        желаемая длина ответов собеседника ("short" / "long")
- corrections:   включать ли мягкие исправления ошибок учащегося (True / False)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# ─── Типы и дефолты ──────────────────────────────────────────────────────────

Level = Literal["A2", "B1", "B2", "C1"]
Length = Literal["short", "long"]
# Режим сессии: voice — микрофон + TTS-ответ; chat — только текст в обе стороны.
Mode = Literal["voice", "chat"]

VALID_LEVELS: tuple[Level, ...] = ("A2", "B1", "B2", "C1")
VALID_LENGTHS: tuple[Length, ...] = ("short", "long")
VALID_MODES: tuple[Mode, ...] = ("voice", "chat")


# ─── Пресеты ролей ───────────────────────────────────────────────────────────
# Ключ роли → human-friendly описание для LLM.
# Пользователь может выбрать один из пресетов ИЛИ "custom" и ввести свою.

ROLE_PRESETS: dict[str, str] = {
    "friend": (
        "a close, easy-going friend chatting casually. "
        "Use a warm, informal tone with everyday expressions."
    ),
    "barista": (
        "a friendly barista at a cozy coffee shop. "
        "Talk about drinks, pastries, the weather, small talk typical in a café."
    ),
    "interviewer": (
        "a polite job interviewer conducting a practice interview. "
        "Ask realistic behavioral and technical questions, "
        "give gentle follow-ups based on the learner's answers."
    ),
    "travel_agent": (
        "a helpful travel agent planning a trip with the learner. "
        "Ask about destinations, dates, budget, preferences — like a real travel consultation."
    ),
    "doctor": (
        "a kind doctor during a routine check-up. "
        "Ask about symptoms, lifestyle, and give simple, reassuring advice. "
        "Do NOT give real medical diagnoses — keep it light and educational."
    ),
    "shopkeeper": (
        "a friendly shop assistant in a clothing store. "
        "Help the learner find items, ask about sizes and preferences, "
        "discuss prices and styles."
    ),
    "language_partner": (
        "a patient language exchange partner who genuinely wants to get to know the learner. "
        "Ask about hobbies, culture, daily life; share small things about yourself too."
    ),
}

DEFAULT_ROLE = "language_partner"


# ─── Настройки сессии ────────────────────────────────────────────────────────

@dataclass
class SessionSettings:
    level: Level = "B1"
    role: str = DEFAULT_ROLE
    role_custom: str = ""
    length: Length = "short"
    corrections: bool = True
    mode: Mode = "voice"

    @classmethod
    def from_query(cls, params: dict) -> "SessionSettings":
        """Парсит query-параметры WebSocket в SessionSettings с валидацией."""
        level = (params.get("level") or "B1").upper()
        if level not in VALID_LEVELS:
            level = "B1"

        role = (params.get("role") or DEFAULT_ROLE).strip().lower()
        role_custom = (params.get("role_custom") or "").strip()[:200]
        if role != "custom" and role not in ROLE_PRESETS:
            role = DEFAULT_ROLE

        length = (params.get("length") or "short").lower()
        if length not in VALID_LENGTHS:
            length = "short"

        corrections_raw = (params.get("corrections") or "on").lower()
        corrections = corrections_raw in ("on", "true", "1", "yes")

        mode = (params.get("mode") or "voice").lower()
        if mode not in VALID_MODES:
            mode = "voice"

        return cls(
            level=level,  # type: ignore[arg-type]
            role=role,
            role_custom=role_custom,
            length=length,  # type: ignore[arg-type]
            corrections=corrections,
            mode=mode,  # type: ignore[arg-type]
        )

    def role_description(self) -> str:
        """Возвращает описание роли для подстановки в промпт."""
        if self.role == "custom" and self.role_custom:
            return f"a conversation partner playing this role: {self.role_custom}"
        return ROLE_PRESETS.get(self.role, ROLE_PRESETS[DEFAULT_ROLE])


# ─── Билдер системного промпта ───────────────────────────────────────────────

_LEVEL_GUIDANCE: dict[str, str] = {
    "A2": (
        "The learner is at A2 (elementary). "
        "Use very simple vocabulary and short sentences (6-10 words). "
        "Avoid idioms, phrasal verbs, and complex grammar. "
        "Stick to present simple, past simple, and basic future with 'going to'."
    ),
    "B1": (
        "The learner is at B1 (intermediate). "
        "Use everyday vocabulary and clear, moderately complex sentences. "
        "You can use common phrasal verbs and simple idioms, but avoid rare or academic words."
    ),
    "B2": (
        "The learner is at B2 (upper-intermediate). "
        "You can use a wider vocabulary, natural phrasal verbs, and richer grammar "
        "(conditionals, passive voice, reported speech). Keep it conversational, not formal."
    ),
    "C1": (
        "The learner is at C1 (advanced). "
        "Speak naturally, as you would with another fluent speaker. "
        "Feel free to use idioms, nuanced vocabulary, and complex sentence structures."
    ),
}

_LENGTH_GUIDANCE: dict[str, str] = {
    "short": (
        "Keep your replies short and chat-like: 1-2 sentences. "
        "Ask one follow-up question to keep the conversation going."
    ),
    "long": (
        "Give fuller, more developed replies: 3-5 sentences. "
        "Share a relevant thought, example, or small story, then ask a follow-up question. "
        "Never write paragraphs — this is still a spoken conversation, not an essay."
    ),
}

_CORRECTION_ON = (
    "If the learner made a grammar, word-choice, or pronunciation mistake "
    "in their last message, begin your reply with one VERY short correction on "
    "its own line, in this exact format:\n"
    "  Correction: <the corrected version of what they tried to say>\n"
    "Then leave a blank line and continue naturally with your in-character reply. "
    "Do NOT explain the grammar rule — just show the corrected phrase. "
    "If there was no meaningful mistake, skip the Correction line entirely "
    "and reply normally."
)

_CORRECTION_OFF = (
    "Do NOT explicitly correct the learner's mistakes. "
    "If they make an error, gently model the correct phrase in your natural reply, "
    "but never call out the mistake."
)


def build_system_prompt(s: SessionSettings) -> str:
    """Собирает финальный system-промпт из настроек сессии."""
    parts: list[str] = []

    parts.append(
        f"You are {s.role_description()} "
        f"You are having a live spoken conversation with an English learner."
    )

    parts.append(_LEVEL_GUIDANCE[s.level])
    parts.append(_LENGTH_GUIDANCE[s.length])
    parts.append(_CORRECTION_ON if s.corrections else _CORRECTION_OFF)

    # Ученик может писать/говорить на любом языке (Whisper в auto, текстовый ввод
    # вообще без языковых ограничений). Но тьютор всегда отвечает по-английски.
    parts.append(
        "IMPORTANT — the learner may write or speak to you in another\n"
        "language (most often Russian). When that happens:\n"
        "- Understand exactly what they meant.\n"
        "- Reply ONLY in English, matching the level above.\n"
        "- You may briefly show the English version of their phrase as if\n"
        "  helping them translate, but never switch your whole reply to Russian.\n"
        "- Encourage them to try saying it in English next time, kindly."
    )

    parts.append(
        "Universal rules:\n"
        "- Always reply in English (even if the learner used another language).\n"
        "- Stay fully in character for your role.\n"
        "- Be warm, patient, and encouraging.\n"
        "- Never break character to talk about yourself as an AI."
    )

    return "\n\n".join(parts)


def build_greeting(s: SessionSettings) -> str:
    """Короткое приветствие в зависимости от выбранной роли."""
    role_greetings: dict[str, str] = {
        "friend": "Hey! Good to see you. What's been going on lately?",
        "barista": "Hi there, welcome in. What can I get started for you today?",
        "interviewer": (
            "Hello, thanks for coming in. Let's start simple — "
            "could you tell me a bit about yourself?"
        ),
        "travel_agent": (
            "Hi, welcome. So, where are you thinking of going? "
            "I'd love to help you plan the trip."
        ),
        "doctor": "Hello, come on in. So, what brings you in today?",
        "shopkeeper": "Hi, welcome in. Is there something in particular you're looking for?",
        "language_partner": (
            "Hi, nice to meet you. I'm excited to chat — "
            "what would you like to talk about today?"
        ),
    }
    if s.role == "custom" and s.role_custom:
        return "Hi, nice to meet you. What would you like to talk about today?"
    return role_greetings.get(s.role, role_greetings[DEFAULT_ROLE])

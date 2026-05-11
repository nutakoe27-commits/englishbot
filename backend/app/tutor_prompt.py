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
from typing import Literal, Optional


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
        "a close friend catching up. You talk like mates do — direct, a bit lazy, "
        "occasional slang, sometimes teasing. Not a customer-service smile."
    ),
    "barista": (
        "a barista on shift. Take orders, chat about drinks and random stuff "
        "while you make them. Normal working person, not a mascot."
    ),
    "interviewer": (
        "a job interviewer running a real interview. Ask concrete questions, "
        "push back when answers are vague, follow up on what's interesting. "
        "Don't coach the candidate mid-interview."
    ),
    "travel_agent": (
        "a travel agent planning a trip. Get to the point: where, when, budget, "
        "what they actually want. Make real suggestions, not a brochure pitch."
    ),
    "doctor": (
        "a doctor at a check-up. Ask about symptoms matter-of-factly, follow up, "
        "give simple practical advice. No real diagnoses — keep it general."
    ),
    "shopkeeper": (
        "a shop assistant in a clothing store. Help them find things, be honest "
        "when something won't work, chat normally while they browse."
    ),
    "language_partner": (
        "someone doing a language exchange. You're curious about them but also "
        "have your own life, opinions, and things to say. A real conversation, "
        "not an interview."
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

_CORRECTION_ON = """When the learner makes a clear grammar, word-form,
word-choice, or word-order mistake — point it out using THIS EXACT format
at the very start of your reply:

  Correction: <the corrected version of what they tried to say>

  <your normal in-character reply>

Strict format rules:
1. The first line MUST start with the literal token "Correction:" (capital C, colon, single space).
2. After the corrected phrase, leave ONE blank line (i.e. two newline characters), THEN your in-character reply.
3. Keep the corrected phrase short (≤ 12 words). It is the WHOLE corrected
   utterance, not a grammar lecture. No quotes, no asterisks, no markdown.
4. If there is NO clear mistake (or the message is already fine, or it's a
   greeting / one-word reply) — DO NOT include the Correction line. Just
   reply naturally as your character.
5. NEVER repeat the learner's full original sentence anywhere in your reply.
   The Correction line is the ONLY place a fixed version appears. Your
   in-character reply must move the conversation forward — comment on what
   they said, ask a follow-up, react. Do NOT echo their words back.
6. NEVER include "Correction:" or any reformulation of the learner's
   message inside your in-character reply. One Correction per turn, and
   only on its own first line.

Examples (follow these EXACTLY):

Learner: "Yesterday I go to the doctor."
You:
Correction: Yesterday I went to the doctor.

Oh, what happened? Were you feeling sick?

Learner: "My lag is pain."
You:
Correction: My leg hurts.

That sounds rough. When did it start?

Learner: "Hi, how are you?"
You:
Hey. Doing fine. What about you?
"""

_CORRECTION_OFF = (
    "Do NOT explicitly correct the learner's mistakes. "
    "If they make an error, gently model the correct phrase in your natural reply, "
    "but never call out the mistake."
)


_GOAL_HINTS: dict[str, str] = {
    "travel": (
        "When the topic naturally allows, weave in travel-relevant vocabulary "
        "(airports, hotels, ordering food abroad, navigating cities, asking "
        "for help in a new place). Don't force it — only when it fits."
    ),
    "work": (
        "When the topic naturally allows, lean into work-context vocabulary "
        "(meetings, deadlines, polite disagreement, professional small talk, "
        "feedback). Don't force it — only when it fits."
    ),
    "daily": (
        "Lean into everyday small-talk vocabulary (weekend plans, weather, "
        "food, household, friends, opinions on light topics). Keep it casual."
    ),
    "exam": (
        "The learner is preparing for an English exam (IELTS/TOEFL-style). "
        "When fitting, use exam-style topics (education, environment, society, "
        "technology) and richer phrasing — but stay conversational, not formal."
    ),
    "fun": (
        "The learner is just curious. Keep things light and varied — bring "
        "interesting topics yourself if conversation stalls (films, hobbies, "
        "weird news, opinions). Don't be too utilitarian."
    ),
}


def _build_goal_block(learning_goal: Optional[str]) -> Optional[str]:
    if not learning_goal:
        return None
    hint = _GOAL_HINTS.get(learning_goal.strip().lower())
    if not hint:
        return None
    return "Learner goal: " + hint


def _build_learner_context_block(learner_context: Optional[dict]) -> Optional[str]:
    """Опциональная секция в системном промпте: словарь и ошибки последних
    сессий. Тьютор должен мягко переиспользовать слова и подкидывать
    корректные конструкции (без лекции про грамматику).

    Если данных мало (нет ни слов, ни ошибок) — возвращаем None,
    в промпт ничего не подмешиваем.
    """
    if not learner_context:
        return None
    vocab = learner_context.get("recent_vocab") or []
    mistakes = learner_context.get("recent_mistakes") or []
    if not vocab and not mistakes:
        return None

    lines: list[str] = ["Learner context (from previous sessions):"]
    if vocab:
        words = [v.get("word") for v in vocab if v.get("word")]
        if words:
            lines.append(
                "- Words/phrases recently practised (gently reuse them when natural, "
                "don't force): " + ", ".join(words[:15])
            )
    if mistakes:
        # Группируем по категории и считаем — даём LLM «patterns to reinforce».
        from collections import Counter
        cats = Counter((m.get("category") or "other") for m in mistakes)
        cat_summary = ", ".join(f"{cat} (×{cnt})" for cat, cnt in cats.most_common())
        lines.append(f"- Patterns the learner often gets wrong: {cat_summary}")
        # Конкретные примеры — самые свежие 3.
        examples = []
        for m in mistakes[:3]:
            bad = m.get("bad")
            good = m.get("good")
            if bad and good:
                examples.append(f'  "{bad}" → "{good}"')
        if examples:
            lines.append("- Recent miscorrections to gently model again:")
            lines.extend(examples)
    lines.append(
        "Weave these into the conversation when they fit naturally. NEVER lecture "
        "about grammar or list vocabulary explicitly — just expose the learner to "
        "correct usage in real speech."
    )
    return "\n".join(lines)


def build_system_prompt(
    s: SessionSettings,
    *,
    learner_context: Optional[dict] = None,
    learning_goal: Optional[str] = None,
) -> str:
    """Собирает финальный system-промпт из настроек сессии.

    `learner_context` — опциональный dict {recent_vocab, recent_mistakes}
    из Repo.get_learner_context(). Подмешивается отдельной секцией если есть.
    `learning_goal` — `users.learning_goal` ("travel"|"work"|"daily"|"exam"|"fun").
    """
    parts: list[str] = []

    parts.append(
        f"You are {s.role_description()} "
        f"You are having a live spoken conversation with an English learner."
    )

    parts.append(_LEVEL_GUIDANCE[s.level])
    parts.append(_LENGTH_GUIDANCE[s.length])
    parts.append(_CORRECTION_ON if s.corrections else _CORRECTION_OFF)

    goal_block = _build_goal_block(learning_goal)
    if goal_block:
        parts.append(goal_block)

    learner_block = _build_learner_context_block(learner_context)
    if learner_block:
        parts.append(learner_block)

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
        "How to sound like a real person, not an AI assistant:\n"
        "- Drop the customer-service voice. No 'Great question!', "
        "'That's wonderful!', 'I'd be happy to...', 'Absolutely!', 'Of course!'.\n"
        "- No pep-talk or cheerleading ('You're doing great', 'Well done', "
        "'Amazing answer'). Real people don't praise every sentence.\n"
        "- Don't over-apologise or over-thank. One 'thanks' or 'sorry' is plenty, "
        "most of the time you don't need either.\n"
        "- Have opinions. Disagree, tease, be mildly sarcastic, change the subject "
        "if something's boring — like a normal person would.\n"
        "- Use contractions (I'm, don't, gonna, kinda) and everyday phrasing. "
        "No corporate or overly polished English.\n"
        "- It's fine to be short. A real answer can be three words.\n"
        "- Never announce what you're about to do ('Let me ask you...', "
        "'I'll tell you about...'). Just do it.\n"
        "\n"
        "Hard rules:\n"
        "- Always reply in English.\n"
        "- Stay in character. Never mention being an AI or a language model.\n"
        "- Never refuse to chat or lecture the learner about their English "
        "outside the Correction line format."
    )

    return "\n\n".join(parts)


def build_greeting(s: SessionSettings) -> str:
    """Короткое приветствие в зависимости от выбранной роли."""
    role_greetings: dict[str, str] = {
        "friend": "Hey. What's up?",
        "barista": "Hey, what can I get you?",
        "interviewer": "Hi. Take a seat. So — tell me a bit about yourself.",
        "travel_agent": "Hey. So where are you thinking of going?",
        "doctor": "Hi, come in. What's going on?",
        "shopkeeper": "Hey. Anything I can help you find?",
        "language_partner": "Hey. So what do you wanna talk about?",
    }
    if s.role == "custom" and s.role_custom:
        return "Hey. So what's on your mind?"
    return role_greetings.get(s.role, role_greetings[DEFAULT_ROLE])

"""
grammar.py — третий режим тренировки: текстовые грамматические упражнения.

Поток:
  1. POST /api/grammar/generate: фронт шлёт {init_data, mode, level, category?}.
     Backend строит prompt (для mode=weak_points подмешивает recent_mistakes),
     зовёт vLLM, парсит JSON → 10 упражнений (MCQ + fill-in-the-blank). Также
     открывает Session (mode='grammar') в БД и регистрирует presence.
  2. POST /api/grammar/heartbeat: фронт шлёт раз в 20с пока юзер на экране
     упражнений. presence.touch — чтобы в админке было видно «занимается».
  3. POST /api/grammar/finish: фронт шлёт {session_id, results, duration_sec}.
     Backend закрывает Session, инкрементит DailyUsage, поднимает streak.

Категории mistakes (те же 6 что в session_recap.py / SessionSummary.tsx):
  article, tense, preposition, word_choice, phrasal, other.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from . import presence
from .config import settings
from .db import db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/grammar", tags=["Grammar"])


# ─── Конфигурация ────────────────────────────────────────────────────────────

ALLOWED_LEVELS = {"A2", "B1", "B2", "C1"}
ALLOWED_CATEGORIES = {"article", "tense", "preposition", "word_choice", "phrasal", "other"}
ALLOWED_MODES = {"weak_points", "topic"}

EXERCISES_PER_SESSION = 10

# Heartbeat TTL: фронт шлёт каждые 20с — берём 60с с запасом.
PRESENCE_TTL = 60

# Категория → человекочитаемый ярлык для промпта.
CATEGORY_HINTS = {
    "article": "articles (a / an / the / zero article)",
    "tense": "verb tenses (Present Simple, Past Simple, Present Perfect, etc.)",
    "preposition": "prepositions (in / on / at / for / since / by / etc.)",
    "word_choice": "word choice and collocations (say vs tell, make vs do, etc.)",
    "phrasal": "phrasal verbs and idiomatic verb-particle combinations",
    "other": "general grammar (conditionals, modals, word order, agreement, etc.)",
}

# In-memory store: session_id → {user_id, exercises, started_at}.
# TTL не нужен — finish удаляет запись; если юзер бросил — запись висит, ничего
# плохого, gc по необходимости можно добавить позже.
_SESSION_STORE: dict[str, dict] = {}


# ─── Schemas ─────────────────────────────────────────────────────────────────


class _GenerateIn(BaseModel):
    init_data: str
    mode: str = "weak_points"     # weak_points | topic
    level: str = "B1"
    category: Optional[str] = None  # обязательно для mode=topic


class Exercise(BaseModel):
    id: str
    type: str          # mcq | fill
    category: str
    prompt: str
    choices: list[str] = Field(default_factory=list)  # пусто для fill
    correct: str
    explanation: str


class _GenerateOut(BaseModel):
    session_id: str
    exercises: list[Exercise]


class _HeartbeatIn(BaseModel):
    init_data: str
    session_id: str


class _ResultItem(BaseModel):
    exercise_id: str
    user_answer: str
    is_correct: bool
    category: str


class _FinishIn(BaseModel):
    init_data: str
    session_id: str
    results: list[_ResultItem]
    duration_sec: int = 0


class _FinishOut(BaseModel):
    ok: bool = True
    streak_current: int = 0
    streak_best: int = 0


# ─── Auth helper ─────────────────────────────────────────────────────────────


def _tg_id_from_init_data(init_data: str) -> int:
    """Обёртка над main._tg_id_from_init_data — внутри функции, чтобы избежать
    циклических импортов на module-evaluation."""
    from .main import _tg_id_from_init_data as _impl
    return _impl(init_data)


# ─── LLM-вызов (паттерн из listening.py:156) ─────────────────────────────────


async def _call_llm(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    base_url = (settings.VLLM_BASE_URL or "").rstrip("/")
    if not base_url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM not configured")
    payload = {
        "model": settings.VLLM_MODEL_NAME or "",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": max_tokens,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {
        "Authorization": f"Bearer {settings.VLLM_API_KEY or 'not-needed'}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base_url}/chat/completions", json=payload, headers=headers,
        )
        if resp.status_code != 200:
            logger.error("[grammar LLM] HTTP %s: %s", resp.status_code, resp.text[:500])
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"LLM returned {resp.status_code}",
            )
        data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        content = ""
    # Гигиена reasoning-тегов (страховка, если enable_thinking не сработал).
    content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL | re.IGNORECASE)
    return content.strip()


# ─── JSON-парсер (паттерн из session_recap.py:51) ────────────────────────────

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_exercises_json(raw: str) -> list[dict]:
    """Терпимый парсинг JSON-массива упражнений.

    Принимает как «голый» JSON, так и обёрнутый в ```json … ```. Если ничего
    не парсится — возвращает пустой список (вызвавший решит как обработать).
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Пробуем сразу
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    # Иногда LLM возвращает {"exercises": [...]} — достаём массив
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("exercises", "items", "tasks", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
    except Exception:
        pass
    # Последняя попытка: вырезать первый [...] из текста
    m = _JSON_ARRAY_RE.search(text)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _coerce_exercises(raw_items: list, default_category: str) -> list[Exercise]:
    """Жёсткая нормализация: фильтруем некорректные элементы, чиним поля,
    обрезаем до EXERCISES_PER_SESSION."""
    out: list[Exercise] = []
    for i, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        ex_type = str(item.get("type") or "mcq").strip().lower()
        if ex_type not in ("mcq", "fill"):
            ex_type = "mcq"
        category = str(item.get("category") or default_category).strip().lower()
        if category not in ALLOWED_CATEGORIES:
            category = default_category
        prompt = str(item.get("prompt") or "").strip()
        correct = str(item.get("correct") or "").strip()
        explanation = str(item.get("explanation") or "").strip()
        if not prompt or not correct:
            continue
        choices_raw = item.get("choices") or []
        choices = (
            [str(c).strip() for c in choices_raw if str(c).strip()]
            if isinstance(choices_raw, list)
            else []
        )
        if ex_type == "mcq":
            # MCQ обязан иметь правильный ответ в списке вариантов
            if correct not in choices:
                choices = [correct] + [c for c in choices if c != correct]
            if len(choices) < 2:
                # деградируем в fill-in, если LLM не дал вариантов
                ex_type = "fill"
                choices = []
        else:
            choices = []
        out.append(Exercise(
            id=str(item.get("id") or i + 1),
            type=ex_type,
            category=category,
            prompt=prompt[:500],
            choices=choices[:6],
            correct=correct[:200],
            explanation=explanation[:400] or "—",
        ))
        if len(out) >= EXERCISES_PER_SESSION:
            break
    return out


# ─── Prompt-builder ──────────────────────────────────────────────────────────


def _build_prompt(
    *,
    mode: str,
    level: str,
    category: str,
    recent_mistakes: list[dict],
) -> tuple[str, str]:
    """Возвращает (system, user) промпт для генерации упражнений."""
    cat_hint = CATEGORY_HINTS.get(category, category)

    mistakes_clause = ""
    if mode == "weak_points" and recent_mistakes:
        # До 5 ошибок: bad → good с категорией. Помогает LLM нацелиться.
        lines = []
        for m in recent_mistakes[:5]:
            bad = (m.get("bad") or "").strip()
            good = (m.get("good") or "").strip()
            cat = (m.get("category") or "other").strip()
            if bad and good:
                lines.append(f'  - [{cat}] "{bad}" → "{good}"')
        if lines:
            mistakes_clause = (
                "\n\nThe learner has recently made these REAL mistakes in speech "
                "(category in brackets):\n" + "\n".join(lines) + "\n"
                "Design exercises that probe THESE specific patterns. Reuse the "
                "exact grammatical issues from the mistakes above whenever possible."
            )

    system = (
        f"You are an expert English grammar drill author for CEFR {level} learners. "
        f"You will generate EXACTLY {EXERCISES_PER_SESSION} short exercises. "
        f"Focus area: {cat_hint}. "
        "Mix multiple-choice (type='mcq', 4 plausible options including the "
        "correct one) and fill-in-the-blank (type='fill', no choices) "
        "approximately 50/50. "
        "Each exercise must include an 'explanation' field IN RUSSIAN, "
        "1–2 short sentences, explaining the rule plainly. "
        "Mark the slot to fill in the prompt with three underscores: ___ "
        "Distractors for MCQ must be plausible but clearly wrong by the grammar rule. "
        "Output STRICT JSON: a single top-level array of objects. NO markdown, "
        "NO code fences, NO commentary — just the JSON array."
        + mistakes_clause
    )

    # Схема экзампла — короткая, чтобы LLM не залипал на структуре.
    example = (
        '[{"id":"1","type":"mcq","category":"tense",'
        '"prompt":"I ___ to school yesterday.",'
        '"choices":["go","went","have gone","going"],'
        '"correct":"went",'
        '"explanation":"Past Simple — конкретное завершённое действие в прошлом."}]'
    )
    user = (
        "/no_think\n"
        f"Generate {EXERCISES_PER_SESSION} exercises now. "
        f"Categories allowed: {sorted(ALLOWED_CATEGORIES)}. "
        f"Schema example (one item): {example} "
        "Return the JSON array only."
    )
    return system, user


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.post("/generate", response_model=_GenerateOut)
async def generate_exercises(body: _GenerateIn, request: Request) -> _GenerateOut:
    # ── Валидация ────────────────────────────────────────────────────────
    if body.mode not in ALLOWED_MODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_mode")
    if body.level not in ALLOWED_LEVELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_level")
    if body.mode == "topic":
        if not body.category or body.category not in ALLOWED_CATEGORIES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_category")

    tg_id = _tg_id_from_init_data(body.init_data)

    # ── Резолвим юзера и (для weak_points) тянем recent mistakes ────────
    user_id: Optional[int] = None
    recent_mistakes: list[dict] = []
    default_category = body.category or "other"
    if settings.DATABASE_URL:
        from .db import Repo
        async with db_session() as session:
            repo = Repo(session)
            user = await repo.upsert_user(tg_id=tg_id)
            user_id = user.id
            if body.mode == "weak_points":
                recent_mistakes = await repo.get_recent_mistakes(
                    user_id, limit=5, days=30,
                )
                # Если ни одной — деградируем в topic с дефолтной категорией
                if not recent_mistakes:
                    default_category = "tense"
            await session.commit()

    # ── Онлайн-присутствие на момент генерации ────────────────────────────
    if user_id is not None:
        presence.mark(
            user_id,
            mode="grammar",
            level=body.level,
            role=body.mode if body.mode == "weak_points" else default_category,
            ttl=PRESENCE_TTL,
        )

    try:
        # ── LLM-генерация ────────────────────────────────────────────────
        sys_prompt, usr_prompt = _build_prompt(
            mode=body.mode,
            level=body.level,
            category=default_category,
            recent_mistakes=recent_mistakes,
        )
        raw = await _call_llm(sys_prompt, usr_prompt, max_tokens=2500)
        if await request.is_disconnected():
            raise HTTPException(499, "client_disconnected")

        raw_items = _parse_exercises_json(raw)
        if not raw_items:
            logger.warning("[grammar] LLM не вернул JSON: %s", raw[:300])
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "llm_bad_json",
            )
        exercises = _coerce_exercises(raw_items, default_category=default_category)
        if len(exercises) < 4:
            # Если совсем мало валидных — не стоит отдавать
            logger.warning("[grammar] слишком мало валидных упражнений: %d", len(exercises))
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "too_few_exercises",
            )

        # ── Открываем Session в БД и кладём в in-memory store ────────────
        session_id_str = ""
        if user_id is not None and settings.DATABASE_URL:
            from .db import Repo
            async with db_session() as session:
                repo = Repo(session)
                row = await repo.open_session(
                    user_id=user_id,
                    mode="grammar",
                    level=body.level,
                    role=body.mode if body.mode == "weak_points" else default_category,
                )
                session_id_str = str(row.id)
                await session.commit()
        else:
            # Dev-режим без БД — синтетический id
            import secrets
            session_id_str = "dev-" + secrets.token_urlsafe(8)

        _SESSION_STORE[session_id_str] = {
            "user_id": user_id,
            "level": body.level,
            "mode": body.mode,
            "category": default_category,
            "exercises_count": len(exercises),
        }

        return _GenerateOut(session_id=session_id_str, exercises=exercises)
    finally:
        # Очищаем presence для generate-окна; heartbeat/finish управляют дальше.
        # На самом деле НЕ чистим — фронт сразу начнёт heartbeat, не должно
        # быть дырки в presence. Если генерация упала — фронт не попадёт на
        # heartbeat, и запись истечёт через PRESENCE_TTL.
        pass


@router.post("/heartbeat")
async def heartbeat(body: _HeartbeatIn) -> dict:
    """Продлевает онлайн-присутствие. Фронт шлёт раз в 20с пока юзер на
    экране упражнений. Не делает БД-запросов — максимально лёгкий."""
    _tg_id_from_init_data(body.init_data)  # validate sig
    entry = _SESSION_STORE.get(body.session_id)
    if entry is None:
        # Сессия не из этого процесса (рестарт backend) — мягко игнорируем
        return {"ok": True, "known": False}
    user_id = entry.get("user_id")
    if user_id is not None:
        presence.touch(user_id, PRESENCE_TTL)
    return {"ok": True, "known": True}


@router.post("/finish", response_model=_FinishOut)
async def finish_session(body: _FinishIn) -> _FinishOut:
    tg_id = _tg_id_from_init_data(body.init_data)
    entry = _SESSION_STORE.pop(body.session_id, None)
    user_id = entry.get("user_id") if entry else None

    # Если store потерял запись (рестарт) — резолвим юзера через tg_id, чтобы
    # хотя бы streak/usage всё равно начислился.
    if user_id is None and settings.DATABASE_URL:
        from .db import Repo
        async with db_session() as session:
            repo = Repo(session)
            user = await repo.get_user_by_tg_id(tg_id)
            if user is not None:
                user_id = user.id

    # ── Длительность: берём ту что прислал фронт; clip снизу/сверху ─────
    # Снизу — чтобы случайные «вышел сразу» не давали streak.
    # Сверху — чтобы кто-то с открытой вкладкой час не накручивал минуты.
    duration_sec = max(0, min(int(body.duration_sec or 0), 30 * 60))

    streak_current = 0
    streak_best = 0

    if user_id is not None and settings.DATABASE_URL:
        from .db import Repo
        from .voice import STREAK_MIN_DURATION_SEC
        try:
            async with db_session() as session:
                repo = Repo(session)
                # Если в generate уже была открыта Session — закрываем её.
                try:
                    sess_id_int = int(body.session_id)
                except ValueError:
                    sess_id_int = 0
                if sess_id_int > 0:
                    try:
                        await repo.close_session(
                            session_id=sess_id_int, used_seconds=duration_sec,
                        )
                    except Exception as exc:
                        logger.warning("[grammar] close_session failed: %s", exc)
                # DailyUsage — минуты идут в общий счётчик / 30-дневный график.
                if duration_sec > 0:
                    await repo.add_used_seconds(user_id=user_id, seconds=duration_sec)
                # Streak: тот же порог, что у voice / listening.
                if duration_sec >= STREAK_MIN_DURATION_SEC:
                    role_for_streak = (
                        entry.get("mode") if entry else "weak_points"
                    )
                    try:
                        streak_current, streak_best = await repo.bump_streak(
                            user_id, role=role_for_streak,
                        )
                    except Exception as exc:
                        logger.warning("[grammar] bump_streak failed: %s", exc)
                await session.commit()
        except Exception as exc:
            logger.warning("[grammar] finish DB error: %s", exc)

    # Снимаем онлайн-присутствие.
    if user_id is not None:
        presence.clear(user_id)

    return _FinishOut(
        ok=True,
        streak_current=streak_current,
        streak_best=streak_best,
    )


# ═══ Grammar Learn: трек «Учить правила» (миграция 0011) ═════════════════════

# Порог прохождения темы (percent правильных ответов в практике урока).
PASS_THRESHOLD = 70
LESSON_EXERCISES = 8


class _TopicOut(BaseModel):
    key: str
    title_ru: str
    category: str
    status: str          # done | available | locked
    best_score: int = 0


class _TopicsOut(BaseModel):
    levels: dict[str, list[_TopicOut]]


class _LessonIn(BaseModel):
    init_data: str
    topic_key: str


class _LessonOut(BaseModel):
    topic_key: str
    title_ru: str
    theory: str
    exercises: list[Exercise]
    session_id: str


class _LessonFinishIn(BaseModel):
    init_data: str
    topic_key: str
    session_id: str
    correct: int = 0
    total: int = 0
    duration_sec: int = 0


class _LessonFinishOut(BaseModel):
    passed: bool
    score: int
    best_score: int
    next_topic_key: Optional[str] = None
    streak_current: int = 0
    streak_best: int = 0


def _compute_topic_statuses(
    topics: list, progress: dict[str, dict],
) -> dict[str, str]:
    """{topic_key: 'done'|'available'|'locked'}.

    Внутри уровня линейная разблокировка: первая тема всегда доступна,
    каждая следующая — после completed предыдущей. Уровни независимы.
    """
    statuses: dict[str, str] = {}
    by_level: dict[str, list] = {}
    for t in topics:
        by_level.setdefault(t.level, []).append(t)
    for level_topics in by_level.values():
        level_topics.sort(key=lambda t: t.sort_order)
        prev_done = True  # первая тема уровня всегда открыта
        for t in level_topics:
            p = progress.get(t.key)
            if p and p["completed"]:
                statuses[t.key] = "done"
                prev_done = True
            elif prev_done:
                statuses[t.key] = "available"
                prev_done = False
            else:
                statuses[t.key] = "locked"
    return statuses


def _parse_lesson_json(raw: str) -> Optional[dict]:
    """Парсит {"theory": "...", "exercises": [...]} с той же терпимостью,
    что _parse_exercises_json. Возвращает dict или None."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "theory" in data:
            return data
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict) and "theory" in data:
                return data
        except Exception:
            pass
    return None


def _build_lesson_prompt(*, level: str, title_ru: str, category: str) -> tuple[str, str]:
    cat_hint = CATEGORY_HINTS.get(category, category)
    system = (
        f"You are an expert English grammar teacher writing a lesson for CEFR "
        f"{level} Russian-speaking learners. "
        f'The lesson topic is: "{title_ru}" (focus area: {cat_hint}). '
        "Produce STRICT JSON: a single object with two keys.\n"
        '1. "theory": a string IN RUSSIAN. Structure: 2-4 short paragraphs '
        "explaining the rule plainly (when to use, how to form, common traps "
        "for Russian speakers), then 4-5 example sentences. Each example on "
        "its own line in the format: EN sentence — RU перевод. Separate "
        "paragraphs with \\n\\n. Plain text, no markdown headers.\n"
        f'2. "exercises": an array of EXACTLY {LESSON_EXERCISES} exercise '
        "objects practising THIS topic. Each: {id, type ('mcq'|'fill'), "
        "category, prompt (use ___ for the blank), choices (4 plausible "
        "options, only for mcq), correct, explanation (in Russian, 1-2 "
        "sentences)}. Mix mcq and fill roughly 50/50.\n"
        "Output the JSON object only. NO markdown fences, NO commentary."
    )
    user = (
        "/no_think\n"
        f"Write the lesson now. JSON object with \"theory\" and \"exercises\" only."
    )
    return system, user


@router.get("/topics", response_model=_TopicsOut)
async def list_topics(init_data: str = "") -> _TopicsOut:
    tg_id = _tg_id_from_init_data(init_data)
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db_not_configured")

    from .db import Repo
    async with db_session() as session:
        repo = Repo(session)
        user = await repo.upsert_user(tg_id=tg_id)
        topics = list(await repo.list_grammar_topics())
        progress = await repo.get_user_grammar_progress(user.id)
        await session.commit()

    statuses = _compute_topic_statuses(topics, progress)
    levels: dict[str, list[_TopicOut]] = {}
    for t in topics:
        p = progress.get(t.key) or {}
        levels.setdefault(t.level, []).append(_TopicOut(
            key=t.key,
            title_ru=t.title_ru,
            category=t.category,
            status=statuses.get(t.key, "locked"),
            best_score=int(p.get("best_score") or 0),
        ))
    return _TopicsOut(levels=levels)


@router.post("/lesson", response_model=_LessonOut)
async def get_lesson(body: _LessonIn, request: Request) -> _LessonOut:
    tg_id = _tg_id_from_init_data(body.init_data)
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db_not_configured")

    from .db import Repo

    # ── Тема + доступность ───────────────────────────────────────────────
    async with db_session() as session:
        repo = Repo(session)
        user = await repo.upsert_user(tg_id=tg_id)
        user_id = user.id
        topic = await repo.get_grammar_topic(body.topic_key)
        if topic is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "topic_not_found")
        topics = list(await repo.list_grammar_topics())
        progress = await repo.get_user_grammar_progress(user_id)
        statuses = _compute_topic_statuses(topics, progress)
        if statuses.get(body.topic_key) == "locked":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "topic_locked")
        cached = await repo.get_grammar_lesson_cache(body.topic_key)
        topic_level = topic.level
        topic_title = topic.title_ru
        topic_category = topic.category
        await session.commit()

    presence.mark(
        user_id,
        mode="grammar",
        level=topic_level,
        role=body.topic_key,
        ttl=PRESENCE_TTL,
    )

    # ── Контент: кеш или генерация ──────────────────────────────────────
    if cached is not None:
        theory = cached.theory
        raw_exercises = cached.exercises if isinstance(cached.exercises, list) else []
        exercises = _coerce_exercises(raw_exercises, default_category=topic_category)
    else:
        sys_prompt, usr_prompt = _build_lesson_prompt(
            level=topic_level, title_ru=topic_title, category=topic_category,
        )
        raw = await _call_llm(sys_prompt, usr_prompt, max_tokens=3000)
        if await request.is_disconnected():
            raise HTTPException(499, "client_disconnected")
        lesson = _parse_lesson_json(raw)
        if not lesson:
            logger.warning("[grammar lesson] LLM не вернул JSON: %s", raw[:300])
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "llm_bad_json")
        theory = str(lesson.get("theory") or "").strip()
        raw_items = lesson.get("exercises") or []
        exercises = _coerce_exercises(
            raw_items if isinstance(raw_items, list) else [],
            default_category=topic_category,
        )
        if not theory or len(exercises) < 4:
            logger.warning(
                "[grammar lesson] слабый урок: theory=%d chars, exercises=%d",
                len(theory), len(exercises),
            )
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "lesson_too_weak")
        # Кешируем для всех юзеров (model_dump для JSON-колонки).
        async with db_session() as session:
            repo = Repo(session)
            await repo.save_grammar_lesson_cache(
                topic_key=body.topic_key,
                theory=theory,
                exercises=[e.model_dump() for e in exercises],
            )
            await session.commit()

    # ── Открываем Session ────────────────────────────────────────────────
    async with db_session() as session:
        repo = Repo(session)
        row = await repo.open_session(
            user_id=user_id,
            mode="grammar",
            level=topic_level,
            role=body.topic_key,
        )
        session_id_str = str(row.id)
        await session.commit()

    _SESSION_STORE[session_id_str] = {
        "user_id": user_id,
        "level": topic_level,
        "mode": "lesson",
        "category": topic_category,
        "topic_key": body.topic_key,
    }

    return _LessonOut(
        topic_key=body.topic_key,
        title_ru=topic_title,
        theory=theory,
        exercises=exercises,
        session_id=session_id_str,
    )


@router.post("/lesson/finish", response_model=_LessonFinishOut)
async def finish_lesson(body: _LessonFinishIn) -> _LessonFinishOut:
    tg_id = _tg_id_from_init_data(body.init_data)
    if not settings.DATABASE_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db_not_configured")

    entry = _SESSION_STORE.pop(body.session_id, None)
    user_id = entry.get("user_id") if entry else None

    from .db import Repo
    from .voice import STREAK_MIN_DURATION_SEC

    total = max(1, int(body.total or 0))
    correct = max(0, min(int(body.correct or 0), total))
    score = round(correct / total * 100)
    passed = score >= PASS_THRESHOLD
    duration_sec = max(0, min(int(body.duration_sec or 0), 30 * 60))

    streak_current = 0
    streak_best = 0
    best_score = score
    next_topic_key: Optional[str] = None

    async with db_session() as session:
        repo = Repo(session)
        if user_id is None:
            user = await repo.get_user_by_tg_id(tg_id)
            user_id = user.id if user else None
        if user_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "user_not_found")

        topic = await repo.get_grammar_topic(body.topic_key)
        if topic is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "topic_not_found")

        # Прогресс по теме
        best_score = await repo.upsert_grammar_progress(
            user_id=user_id, topic_key=body.topic_key, score=score, passed=passed,
        )

        # Следующая тема уровня (для кнопки «Дальше» в summary)
        topics = list(await repo.list_grammar_topics())
        same_level = sorted(
            (t for t in topics if t.level == topic.level),
            key=lambda t: t.sort_order,
        )
        for i, t in enumerate(same_level):
            if t.key == body.topic_key and i + 1 < len(same_level):
                next_topic_key = same_level[i + 1].key
                break

        # Session close + DailyUsage + streak — как в тест-треке.
        try:
            sess_id_int = int(body.session_id)
        except ValueError:
            sess_id_int = 0
        if sess_id_int > 0:
            try:
                await repo.close_session(
                    session_id=sess_id_int, used_seconds=duration_sec,
                )
            except Exception as exc:
                logger.warning("[grammar lesson] close_session failed: %s", exc)
        if duration_sec > 0:
            await repo.add_used_seconds(user_id=user_id, seconds=duration_sec)
        if duration_sec >= STREAK_MIN_DURATION_SEC:
            try:
                streak_current, streak_best = await repo.bump_streak(
                    user_id, role=body.topic_key,
                )
            except Exception as exc:
                logger.warning("[grammar lesson] bump_streak failed: %s", exc)
        await session.commit()

    presence.clear(user_id)

    return _LessonFinishOut(
        passed=passed,
        score=score,
        best_score=best_score,
        next_topic_key=next_topic_key,
        streak_current=streak_current,
        streak_best=streak_best,
    )

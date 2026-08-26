"""Тест уровня английского (CEFR A1–C1).

Устройство и почему так:

* **Во время прохождения LLM не вызывается вообще.** Задания берутся из
  калиброванного банка (`level_test_bank`), выбор следующего — арифметика
  на несколько миллисекунд. Человек не ждёт ни на одном шаге.
* **Адаптивная лестница.** Старт с B1 (середина). Верный ответ поднимает
  на уровень выше, неверный — опускает. За 12 вопросов ладдер сходится к
  реальному уровню, и это заметно точнее, чем фиксированный набор.
* **Результат считается локально и отдаётся мгновенно** вместе с последним
  ответом. AI-разбор запрашивается отдельным вызовом и подгружается уже
  поверх показанного результата — ради этого и разделены эндпоинты.

Тест состоит из трёх типов заданий, идущих **вперемешку**:

* грамматика и лексика — из банка, мгновенно, участвуют в лестнице;
* два блока аудирования — минутный подкаст плюс вопросы по нему.

Подкасты генерируются LLM и озвучиваются Kokoro, а это десятки секунд.
Поэтому они **готовятся в фоне**: первый запускается на старте теста,
второй — как только лестница нащупала уровень. Пока они генерируются,
человек отвечает на обычные вопросы, и к моменту, когда блок выпадает по
плану, аудио уже готово. Ждать не приходится.

Короткий режим (``mode="short"``) — для публичного лендинга /level.
Отличий три, и все три вынужденные:

* **подкастов нет** — короткий тест проходят анонимно, а генерация стоит
  GPU-времени; десять заданий из банка не стоят ничего;
* **можно без авторизации** — регистрации на входе нет, иначе лендинг
  теряет смысл;
* **анониму отдаётся только уровень** — разбивка по навыкам, AI-разбор и
  запись результата открываются после регистрации (``/claim``). Полный
  результат при этом не покидает сервер, пока человек не вошёл.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import secrets
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .config import settings
from .db import db_session
from .level_test_bank import (
    BANK, BY_ID, BY_LEVEL, LEVELS, VOCAB, VOCAB_BY_LEVEL,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/level-test", tags=["LevelTest"])

# Счётчики заданий. Меняются здесь — план и подсчёт подстроятся сами.
N_GRAMMAR = 10
N_VOCAB = 10
N_BLOCKS = 2                 # блоков аудирования
Q_PER_BLOCK = 2              # вопросов на блок
# Экранов всего: 10 + 10 + 2 * (1 аудио + 2 вопроса) = 26
TOTAL_SCREENS = N_GRAMMAR + N_VOCAB + N_BLOCKS * (1 + Q_PER_BLOCK)
# Сколько заданий засчитывается в оценку (аудио-экран — не задание).
TOTAL_QUESTIONS = N_GRAMMAR + N_VOCAB + N_BLOCKS * Q_PER_BLOCK

LISTEN_WORDS = 150           # ~1 минута речи
# Второй подкаст запускаем, когда лестница уже нащупала уровень.
BLOCK2_TRIGGER_ANSWER = 7

# Версия для лендинга: 26 заданий из банка, без подкастов. Столько же
# экранов, сколько в полном тесте, — просто вместо двух блоков аудирования
# идут задания из банка, которые ничего не стоят и не требуют ожидания.
#
# Почему 26, а не 10: на симуляции тест из 10 заданий попадал точно в
# уровень в 64% случаев, из 26 — в 80%. Сплит между грамматикой и словами
# при этом почти не влияет. Таблица замеров — в docs/level-test.md.
SHORT_GRAMMAR = 13
SHORT_VOCAB = 13
# Сколько анонимных тестов пускаем с одного IP в час. Генерации здесь нет,
# так что защищаемся не от расходов, а от засорения _TESTS — отсюда и
# щедрый лимит. Занижать нельзя: мобильные операторы держат десятки
# реальных людей за одним адресом (CGNAT), и жёсткий порог отрезал бы
# живой трафик раньше, чем ботов.
ANON_STARTS_PER_HOUR = 60

START_LEVEL_IDX = LEVELS.index("B1")
# Уровень засчитывается, если по нему было хотя бы столько попыток...
MIN_ATTEMPTS_PER_LEVEL = 2
# ...и доля верных не ниже этой.
PASS_RATIO = 0.6

# test_id → состояние. Живёт в памяти: тест короткий, переживать рестарт
# backend'а ему незачем (тот же приём, что в grammar._SESSION_STORE).
_TESTS: dict[str, dict] = {}
_TEST_TTL_SEC = 3600
# Лендинг добавляет анонимный трафик — запас, чтобы он не вытеснял
# тесты, идущие в приложении.
_TESTS_MAX = 2000


def _gc_tests() -> None:
    now = time.time()
    for tid in [t for t, st in _TESTS.items() if st["expires_at"] < now]:
        _TESTS.pop(tid, None)
    while len(_TESTS) >= _TESTS_MAX:
        try:
            _TESTS.pop(next(iter(_TESTS)), None)
        except StopIteration:
            break


def validate_bank() -> list[str]:
    """Инварианты банка. Зовётся тестом и на старте приложения."""
    problems: list[str] = []
    seen: set[str] = set()
    for q in BANK:
        qid = q.get("id", "?")
        if qid in seen:
            problems.append(f"{qid}: дубль id")
        seen.add(qid)
        if "___" not in q.get("prompt", ""):
            problems.append(f"{qid}: нет пропуска ___")
        ch = q.get("choices") or []
        if len(ch) != 4 or len(set(ch)) != 4:
            problems.append(f"{qid}: нужно ровно 4 разных варианта")
        if q.get("correct") not in ch:
            problems.append(f"{qid}: correct вне choices")
        if q.get("level") not in LEVELS:
            problems.append(f"{qid}: неизвестный уровень")
        if not (q.get("note") or "").strip():
            problems.append(f"{qid}: пустое пояснение")
    for lv in LEVELS:
        if len(BY_LEVEL.get(lv, [])) < 6:
            problems.append(f"{lv}: меньше 6 заданий — ладдер будет упираться")
    return problems


def _build_plan(
    *,
    n_grammar: int = N_GRAMMAR,
    n_vocab: int = N_VOCAB,
    n_blocks: int = N_BLOCKS,
) -> list[str]:
    """План экранов. Токены: grammar | vocab | audio:B | q:B:K.

    Грамматика и лексика перемешиваются полностью — тест не должен идти
    блоками «сначала все слова, потом вся грамматика». Блоки аудирования
    вставляются в фиксированные окна: первый не в начале (нужно время на
    фоновую генерацию), второй — во второй половине.

    ``n_blocks=0`` — короткий режим лендинга: остаются только банк-задания.
    """
    plan = ["grammar"] * n_grammar + ["vocab"] * n_vocab
    random.shuffle(plan)
    quiz_len = len(plan)
    # Окна подобраны так, чтобы блоки не слипались и не попадали в самое
    # начало/конец. Вставляем с конца — иначе индексы «поедут».
    windows = [(4, 8), (int(quiz_len * 0.65), quiz_len - 2)]
    slots = []
    for lo, hi in windows:
        lo, hi = max(1, lo), max(2, hi)
        slots.append(random.randint(min(lo, hi), hi))
    for b in reversed(range(n_blocks)):
        pos = min(slots[b], len(plan))
        plan[pos:pos] = [f"audio:{b}"] + [f"q:{b}:{k}" for k in range(Q_PER_BLOCK)]
    return plan


def _drop_block(state: dict, b: int) -> None:
    """Выкинуть блок из плана целиком — и аудио, и его вопросы.

    Если генерация не удалась (LLM или Kokoro недоступны), человек не
    должен упираться в мёртвый экран: тест продолжается без этого блока,
    а прогресс остаётся честным, потому что план укорачивается.
    """
    state["plan"] = [
        t for t in state["plan"]
        if t != f"audio:{b}" and not t.startswith(f"q:{b}:")
    ]


def _public(q: dict, index: int, subkind: str, total: int) -> dict:
    """Задание без правильного ответа — его клиент знать не должен."""
    choices = list(q["choices"])
    random.shuffle(choices)
    return {
        "kind": "quiz",
        "subkind": subkind,
        "id": q["id"],
        "index": index,
        "total": total,
        "level": q["level"],
        "skill": q.get("skill", subkind),
        "prompt": q["prompt"],
        "choices": choices,
    }


def _pick(state: dict, kind: str) -> Optional[dict]:
    """Задание нужного типа с текущей ступени лестницы.

    Если на ступени всё выдано — идём к ближайшему уровню, где ещё есть
    неиспользованные. Так тест не обрывается, даже если человек залип.
    """
    used: set[str] = state["used"]
    pool_by_level = VOCAB_BY_LEVEL if kind == "vocab" else BY_LEVEL
    idx = state["level_idx"]
    order = sorted(range(len(LEVELS)), key=lambda i: (abs(i - idx), i))
    for i in order:
        pool = [q for q in pool_by_level[LEVELS[i]] if q["id"] not in used]
        if pool:
            return random.choice(pool)
    return None


def _block_screen(state: dict, b: int, index: int) -> dict:
    """Экран с подкастом. ready=False — клиент показывает «готовим» и
    опрашивает /listen, пока фоновая генерация не закончится."""
    blk = state["blocks"].get(b) or {}
    ready = blk.get("status") == "ready"
    return {
        "kind": "listen_audio",
        "block": b,
        "index": index,
        "total": len(state.get("plan") or []) or TOTAL_SCREENS,
        "ready": ready,
        "failed": blk.get("status") == "failed",
        "level": blk.get("level"),
        "audio_url": (
            f"/api/listening/audio/{blk['audio_id']}.wav" if ready else None
        ),
        "seconds": blk.get("seconds"),
    }


def _next_screen(state: dict) -> Optional[dict]:
    """Следующий экран по плану. None — тест окончен."""
    while state["pos"] < len(state["plan"]):
        token = state["plan"][state["pos"]]
        index = state["pos"] + 1
        total = len(state["plan"])
        if token in ("grammar", "vocab"):
            q = _pick(state, token)
            if q is None:                     # банк исчерпан — выкидываем шаг
                state["plan"].pop(state["pos"])
                continue
            state["used"].add(q["id"])
            state["current"] = q["id"]
            state["current_kind"] = token
            return _public(q, index, token, total)
        if token.startswith("audio:"):
            b = int(token.split(":")[1])
            blk = state["blocks"].get(b) or {}
            if blk.get("status") == "failed":
                _drop_block(state, b)
                continue
            state["current"] = token
            state["current_kind"] = "audio"
            return _block_screen(state, b, index)
        if token.startswith("q:"):
            _, bs, ks = token.split(":")
            b, k = int(bs), int(ks)
            blk = state["blocks"].get(b) or {}
            qs = blk.get("questions") or []
            if k >= len(qs):                  # вопросов не хватило
                state["plan"].pop(state["pos"])
                continue
            q = qs[k]
            state["current"] = token
            state["current_kind"] = "listening"
            choices = list(q["choices"])
            random.shuffle(choices)
            return {
                "kind": "quiz",
                "subkind": "listening",
                "id": token,
                "index": index,
                "total": total,
                "level": blk.get("level"),
                "skill": "listening",
                "prompt": q["q"],
                "choices": choices,
            }
        state["plan"].pop(state["pos"])
    state["current"] = None
    return None


# ─── Фоновая генерация блоков аудирования ───────────────────────────────────
# Минутный подкаст — это вызов LLM (~7 с) плюс синтез Kokoro (~12 с) плюс
# генерация вопросов. Тридцать секунд ожидания посреди теста недопустимы,
# поэтому блоки готовятся заранее, пока человек отвечает на обычные вопросы.

_LISTEN_TOPICS = [
    "a morning routine in a big city", "an unusual job someone loves",
    "why people started drinking coffee", "a small habit that changes the day",
    "how a street market works", "a short trip that went wrong",
    "living with a noisy neighbour", "learning to cook one dish well",
    "a museum that almost closed", "why some shops stay open all night",
    "an animal that adapted to cities", "a hobby that became a business",
]

_LISTEN_SYSTEM = (
    "You write ultra-short podcast scripts for English learners and "
    "comprehension questions about them.\n"
    "Return STRICT JSON, one object, no markdown and no code fences:\n"
    '{"script": "...", "questions": [{"q": "...", "choices": ["...","...","...","..."], '
    '"correct": "..."}]}\n'
    "Rules for script: single-host monologue in natural spoken English, "
    "about %d words, CEFR %s vocabulary, no headings, no speaker tags, "
    "no stage directions — plain prose that reads aloud well.\n"
    "Rules for questions: EXACTLY %d questions. Both the question and all "
    "four options are IN RUSSIAN — мы проверяем понимание на слух, а не "
    "умение читать по-английски. Exactly one option is correct, the other "
    "three are plausible but contradict the script. Ask about facts stated "
    "in the script, not about opinions."
)


async def _generate_block(test_id: str, b: int, level: str) -> None:
    """Сгенерировать подкаст и вопросы, положить в state. Fire-and-forget:
    падение блока не должно ронять тест — пометим failed и пропустим."""
    from .listening import (
        _AUDIO_STORE, _AUDIO_TTL_SEC, _call_llm_for_script, _gc_audio_store,
        _synthesize_full, _wrap_pcm_to_wav, OUTPUT_SAMPLE_RATE,
    )
    state = _TESTS.get(test_id)
    if state is None:
        return
    blk = state["blocks"].setdefault(b, {})
    blk.update({"status": "running", "level": level})
    try:
        system = _LISTEN_SYSTEM % (LISTEN_WORDS, level, Q_PER_BLOCK)
        topic = random.choice(_LISTEN_TOPICS)
        user = (
            f"/no_think\nTopic: {topic}. Write the JSON now, "
            f"~{LISTEN_WORDS} words in the script."
        )
        raw = await _call_llm_for_script(system, user, max_tokens=1200)
        raw = re.sub(r"<think>.*?</think>\s*", "", raw, flags=re.DOTALL | re.IGNORECASE)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0) if m else raw)
        script = (data.get("script") or "").strip()
        questions = [
            q for q in (data.get("questions") or [])
            if isinstance(q, dict)
            and (q.get("q") or "").strip()
            and isinstance(q.get("choices"), list)
            and len(set(str(c).strip() for c in q["choices"] if str(c).strip())) == 4
            and str(q.get("correct") or "").strip() in [str(c).strip() for c in q["choices"]]
        ]
        if not script or len(questions) < Q_PER_BLOCK:
            raise ValueError(f"плохой JSON: script={len(script)} q={len(questions)}")
        for q in questions:
            q["choices"] = [str(c).strip() for c in q["choices"]]
            q["correct"] = str(q["correct"]).strip()

        pcm = await _synthesize_full(script, speed=1.0)
        wav = _wrap_pcm_to_wav(pcm)
        _gc_audio_store()
        audio_id = secrets.token_urlsafe(16)
        _AUDIO_STORE[audio_id] = (wav, time.time() + _AUDIO_TTL_SEC)

        state = _TESTS.get(test_id)          # мог истечь, пока генерировали
        if state is None:
            return
        state["blocks"][b] = {
            "status": "ready", "level": level, "audio_id": audio_id,
            "script": script, "questions": questions[:Q_PER_BLOCK],
            "seconds": len(pcm) // 2 // OUTPUT_SAMPLE_RATE,
        }
        logger.info("[level-test] блок %d готов (%s, %d с)", b, level,
                    state["blocks"][b]["seconds"])
    except Exception as exc:
        logger.warning("[level-test] блок %d не сгенерирован: %r", b, exc)
        state = _TESTS.get(test_id)
        if state is not None:
            state["blocks"].setdefault(b, {})["status"] = "failed"


def _kick_block(test_id: str, b: int, level: str) -> None:
    """Запустить генерацию, если ещё не запускали."""
    state = _TESTS.get(test_id)
    if state is None:
        return
    if (state["blocks"].get(b) or {}).get("status") in ("running", "ready"):
        return
    state["blocks"].setdefault(b, {})["status"] = "running"
    try:
        asyncio.create_task(_generate_block(test_id, b, level))
    except RuntimeError:
        logger.warning("[level-test] нет event loop для генерации блока")


def _grade(answers: list[dict]) -> dict:
    """Итог: CEFR, разбивка по уровням и навыкам, слабые места.

    Уровень = самый высокий, на котором было не меньше
    MIN_ATTEMPTS_PER_LEVEL попыток и доля верных не ниже PASS_RATIO.
    Ступени с одной попыткой игнорируются как шум.

    Аудирование в определение уровня НЕ входит: сложность подкаста
    задаём мы сами, это не калиброванное измерение, и две угаданные
    догадки могли бы поднять уровень на ступень. Оно идёт отдельным
    показателем в разбивке по навыкам.
    """
    by_level: dict[str, dict] = {lv: {"correct": 0, "total": 0} for lv in LEVELS}
    by_skill: dict[str, dict] = {}
    for a in answers:
        sk = a.get("skill") or "other"
        st = by_skill.setdefault(sk, {"correct": 0, "total": 0})
        st["total"] += 1
        st["correct"] += int(bool(a["correct"]))
        lv = a.get("level")
        if sk == "listening" or lv not in by_level:
            continue
        by_level[lv]["total"] += 1
        by_level[lv]["correct"] += int(bool(a["correct"]))

    cefr = LEVELS[0]
    for lv in LEVELS:  # снизу вверх — остаётся самый высокий пройденный
        st = by_level[lv]
        if st["total"] >= MIN_ATTEMPTS_PER_LEVEL and st["correct"] / st["total"] >= PASS_RATIO:
            cefr = lv

    weak = sorted(
        (sk for sk, st in by_skill.items()
         if st["total"] >= 2 and st["correct"] / st["total"] < PASS_RATIO),
        key=lambda sk: by_skill[sk]["correct"] / by_skill[sk]["total"],
    )
    correct_cnt = sum(1 for a in answers if a["correct"])
    return {
        "cefr": cefr,
        "correct_cnt": correct_cnt,
        "total_cnt": len(answers),
        "by_level": {lv: st for lv, st in by_level.items() if st["total"]},
        "by_skill": by_skill,
        "weak_skills": weak,
        "listening": by_skill.get("listening"),
    }


# ─── Модели запросов ────────────────────────────────────────────────────────

class _StartIn(BaseModel):
    init_data: Optional[str] = None
    # "full" — тест в приложении, "short" — короткая версия для лендинга.
    mode: str = "full"


class _AnswerIn(BaseModel):
    init_data: Optional[str] = None
    test_id: str
    question_id: str
    answer: str = Field(default="", max_length=200)


class _ReportIn(BaseModel):
    init_data: Optional[str] = None
    test_id: str


class _ClaimIn(BaseModel):
    init_data: Optional[str] = None
    test_id: str


def _has_auth(init_data: Optional[str], authorization: Optional[str]) -> bool:
    """Есть ли чем авторизоваться. Пустой заголовок за авторизацию не считаем."""
    if init_data:
        return True
    a = (authorization or "").strip()
    return a.lower().startswith("bearer ") and len(a) > len("bearer ")


# IP → отметки времени стартов. Короткий тест ничего не генерирует, так что
# защищаемся не от расходов на GPU, а от засорения _TESTS ботами.
_ANON_HITS: dict[str, list[float]] = {}
_ANON_HITS_MAX = 5000


def _client_ip(request: Request) -> str:
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if fwd:
        return fwd[:64]
    return (request.client.host if request.client else "?")[:64]


def _anon_allowed(ip: str) -> bool:
    now = time.time()
    if len(_ANON_HITS) > _ANON_HITS_MAX:
        _ANON_HITS.clear()
    hits = [t for t in _ANON_HITS.get(ip, []) if now - t < 3600]
    if len(hits) >= ANON_STARTS_PER_HOUR:
        _ANON_HITS[ip] = hits
        return False
    hits.append(now)
    _ANON_HITS[ip] = hits
    return True


def _trim_result(result: dict) -> dict:
    """Что видит аноним на лендинге: уровень и счёт, без разбора.

    Разбивка по навыкам и слабые места — это уже содержательная часть,
    ради которой человек регистрируется. Отдавать её в браузер «закрытой
    замочком» бессмысленно: она была бы в ответе сети. Поэтому полный
    результат остаётся на сервере до /claim.
    """
    return {
        "cefr": result["cefr"],
        "correct_cnt": result["correct_cnt"],
        "total_cnt": result["total_cnt"],
        "locked": True,
        "by_level": {},
        "by_skill": {},
        "weak_skills": [],
        "listening": None,
    }


# Дольше этого шаг воронки не ждём. Короткий тест — единственный сценарий,
# который в остальном обходится без БД вообще: без таймаута недоступная
# база превратила бы «начать тест» в зависший экран.
_LEAD_TIMEOUT_SEC = 3.0


async def _lead_write(method: str, **kwargs) -> None:
    """Шаг воронки лендинга. Никогда не роняет тест.

    До миграции 0036 таблицы нет, и это нормально: аналитика не должна
    стоить человеку результата. Поэтому любая ошибка только логируется,
    а ожидание ограничено таймаутом.
    """
    if not settings.DATABASE_URL:
        return
    from .db import Repo

    async def _run() -> None:
        async with db_session() as session:
            await getattr(Repo(session), method)(**kwargs)
            await session.commit()

    try:
        await asyncio.wait_for(_run(), timeout=_LEAD_TIMEOUT_SEC)
    except Exception as exc:
        logger.warning("[level-test] воронка (%s) не записана: %r", method, exc)


async def _resolve(init_data: Optional[str], authorization: Optional[str]) -> Optional[int]:
    """user_id или None, если БД не настроена. Ошибку авторизации пробрасываем."""
    if not settings.DATABASE_URL:
        return None
    from .db import Repo
    from .auth import resolve_user
    async with db_session() as session:
        repo = Repo(session)
        user = await resolve_user(
            repo, authorization=authorization, init_data=init_data or "",
        )
        return int(user.id)


# ─── Эндпоинты ──────────────────────────────────────────────────────────────

async def _finish(state: dict) -> dict:
    """Посчитать итог и записать его. Результат возвращается в любом
    случае: провал записи в БД не должен лишать человека результата."""
    result = _grade(state["answers"])
    state["result"] = result
    public = bool(state.get("public"))
    if public and state.get("test_id"):
        await _lead_write(
            "landing_lead_finish", test_id=state["test_id"],
            cefr=result["cefr"], correct_cnt=result["correct_cnt"],
            total_cnt=result["total_cnt"],
        )
        # Залогиненный проходит тест на лендинге без шага /claim — но для
        # воронки он такой же «дошедший до аккаунта», иначе конверсия
        # окажется занижена.
        if state.get("user_id") is not None:
            await _lead_write(
                "landing_lead_claim", test_id=state["test_id"],
                user_id=state["user_id"],
            )
    if state.get("user_id") is not None:
        from .db import Repo
        try:
            async with db_session() as session:
                repo = Repo(session)
                state["db_id"] = await repo.save_level_test(
                    user_id=state["user_id"], cefr=result["cefr"],
                    correct_cnt=result["correct_cnt"],
                    total_cnt=result["total_cnt"],
                    answers=state["answers"],
                    source="landing" if public else "app",
                )
                await session.commit()
        except Exception as exc:
            # До миграции 0035 таблицы нет — результат важнее записи.
            logger.warning("[level-test] не сохранён: %r", exc)
    if public and state.get("user_id") is None:
        return _trim_result(result)
    return result


@router.post("/start")
async def start_test(
    request: Request,
    body: _StartIn,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Начать тест. Первый вопрос отдаётся сразу — LLM не участвует.

    В полном режиме здесь же запускается фоновая генерация первого блока
    аудирования: пока человек отвечает на первые вопросы, подкаст успевает
    сгенерироваться.

    В коротком режиме (лендинг) подкастов нет вообще, и авторизация не
    обязательна — иначе публичная страница теряет смысл.
    """
    short = (body.mode or "").strip().lower() == "short"
    authed = _has_auth(body.init_data, authorization)
    if short and not authed:
        if not _anon_allowed(_client_ip(request)):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "too_many_tests",
            )
        user_id = None
    else:
        user_id = await _resolve(body.init_data, authorization)
    _gc_tests()
    test_id = secrets.token_urlsafe(16)
    state = {
        "test_id": test_id,
        "user_id": user_id,
        "public": short,
        "level_idx": START_LEVEL_IDX,
        "used": set(),
        "answers": [],
        "plan": (
            _build_plan(n_grammar=SHORT_GRAMMAR, n_vocab=SHORT_VOCAB, n_blocks=0)
            if short else _build_plan()
        ),
        "pos": 0,
        "blocks": {},
        "expires_at": time.time() + _TEST_TTL_SEC,
    }
    _TESTS[test_id] = state

    # Воронка лендинга: без отметки о старте нельзя посчитать, сколько
    # человек бросили тест на середине, а для лендинга это ключевая цифра.
    if short:
        await _lead_write("landing_lead_start", test_id=test_id)

    # Первый блок — на стартовом уровне: до ответов судить не о чем.
    if not short:
        _kick_block(test_id, 0, LEVELS[START_LEVEL_IDX])

    screen = _next_screen(state)
    if screen is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "bank_empty")

    prev = None
    if user_id is not None:
        from .db import Repo
        async with db_session() as session:
            prev = await Repo(session).last_level_test(user_id)
    return {"test_id": test_id, "question": screen, "previous": prev}


@router.post("/claim")
async def claim_test(
    body: _ClaimIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Забрать пройденный анонимно тест себе — после регистрации.

    Здесь же открывается полный результат: разбивка по навыкам и слабые
    места, которые аноним не получал. Записываем прохождение в БД и
    проставляем уровень юзеру — ровно как в тесте внутри приложения.

    Забрать чужой тест нельзя: test_id — 128-битный секрет, он есть только
    у того браузера, который тест проходил. Повторный вызов идемпотентен.
    """
    state = _TESTS.get(body.test_id)
    if state is None or not state.get("result"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "result_not_found")
    user_id = await _resolve(body.init_data, authorization)
    if user_id is None:
        # БД не настроена (dev) — отдаём разбор, записывать некуда.
        return {"result": state["result"]}
    if state.get("db_id") and state.get("user_id") == user_id:
        return {"result": state["result"]}

    state["user_id"] = user_id
    result = state["result"]
    from .db import Repo
    try:
        async with db_session() as session:
            repo = Repo(session)
            state["db_id"] = await repo.save_level_test(
                user_id=user_id, cefr=result["cefr"],
                correct_cnt=result["correct_cnt"],
                total_cnt=result["total_cnt"],
                answers=state["answers"],
                source="landing" if state.get("public") else "app",
            )
            if state.get("report"):
                await repo.attach_level_test_report(
                    state["db_id"], state["report"],
                )
            await session.commit()
    except Exception as exc:
        # Результат человеку важнее записи — показываем разбор в любом случае.
        logger.warning("[level-test] claim не сохранён: %r", exc)
    # Отдельной сессией: воронка — аналитика, и её проблемы не должны
    # откатывать транзакцию с самим результатом.
    if state.get("public"):
        await _lead_write(
            "landing_lead_claim",
            test_id=str(state.get("test_id") or body.test_id),
            user_id=user_id,
        )
    return {"result": result}


@router.post("/answer")
async def answer_question(
    body: _AnswerIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Принять ответ (или подтверждение «прослушал»), вернуть следующий экран.

    На последнем задании здесь же приходит готовый результат — клиент
    показывает уровень мгновенно, не дожидаясь AI-разбора.
    """
    state = _TESTS.get(body.test_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "test_not_found")
    if state.get("current") != body.question_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "wrong_question")
    state["expires_at"] = time.time() + _TEST_TTL_SEC

    feedback: dict = {}
    kind = state.get("current_kind")

    if kind == "audio":
        # Экран прослушивания — это не задание, просто листаем дальше.
        b = int(str(body.question_id).split(":")[1])
        blk = state["blocks"].get(b) or {}
        if blk.get("status") == "failed":
            _drop_block(state, b)
            screen = _next_screen(state)
            if screen is not None:
                return {"correct": None, "done": False, "question": screen}
            return {"correct": None, "done": True,
                    "result": await _finish(state)}
        if blk.get("status") != "ready":
            # Ещё генерится — клиент опрашивает /listen и не листает дальше.
            raise HTTPException(status.HTTP_409_CONFLICT, "audio_not_ready")
        feedback = {"correct": None}
    else:
        if kind == "listening":
            _, bs, ks = str(body.question_id).split(":")
            blk = state["blocks"].get(int(bs)) or {}
            q = (blk.get("questions") or [])[int(ks)]
            correct_answer, level, skill = q["correct"], blk.get("level"), "listening"
            note = ""
        else:
            q = BY_ID.get(body.question_id)
            if q is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "question_not_found")
            correct_answer, level, skill = q["correct"], q["level"], q.get("skill", kind)
            note = q.get("note") or ""

        is_correct = body.answer.strip() == correct_answer
        state["answers"].append({
            "id": str(body.question_id), "level": level, "skill": skill,
            "correct": is_correct, "answer": body.answer.strip()[:200],
        })
        # Лестница: верно — ступень вверх, неверно — вниз. Аудирование в
        # ней не участвует: его сложность задаём мы сами, это не измерение.
        if skill != "listening":
            state["level_idx"] = max(0, min(
                len(LEVELS) - 1,
                state["level_idx"] + (1 if is_correct else -1),
            ))
        feedback = {
            "correct": is_correct,
            "correct_answer": correct_answer,
            "note": note,
        }

        # Второй блок готовим, когда лестница уже нащупала уровень.
        # В коротком режиме блоков нет — и звать LLM не за чем.
        if (
            not state.get("public")
            and len(state["answers"]) == BLOCK2_TRIGGER_ANSWER
            and N_BLOCKS > 1
        ):
            _kick_block(body.test_id, 1, LEVELS[state["level_idx"]])

    state["pos"] += 1
    screen = _next_screen(state)
    if screen is not None:
        return {**feedback, "done": False, "question": screen}

    return {**feedback, "done": True, "result": await _finish(state)}


class _ListenIn(BaseModel):
    init_data: Optional[str] = None
    test_id: str
    block: int


@router.post("/listen")
async def listen_status(
    body: _ListenIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Готов ли подкаст. Клиент опрашивает, пока идёт фоновая генерация.

    В норме к моменту показа блок уже готов — этот эндпоинт нужен на
    случай медленной генерации или холодного Kokoro.
    """
    state = _TESTS.get(body.test_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "test_not_found")
    state["expires_at"] = time.time() + _TEST_TTL_SEC
    return _block_screen(state, int(body.block), state["pos"] + 1)


_REPORT_SYSTEM = (
    "Ты преподаватель английского. Пишешь короткий разбор результата теста "
    "уровня для русскоязычного ученика.\n"
    "Пиши ПО-РУССКИ, дружелюбно и по делу, на «ты». Без markdown, без "
    "списков со звёздочками, без заголовков — только связный текст.\n"
    "Структура ровно такая:\n"
    "1) Одно предложение: что означает этот уровень на практике — что "
    "человек уже может делать на английском.\n"
    "2) Два-три предложения: что конкретно провисает, со ссылкой на "
    "ошибки из теста. Называй правило, а не номер задания.\n"
    "3) Одно-два предложения: что делать дальше в приложении — какие "
    "режимы и на что обратить внимание.\n"
    "Всего 4-6 предложений. Не хвали пусто, не извиняйся, не перечисляй "
    "всё подряд."
)


@router.post("/report")
async def build_report(
    body: _ReportIn, authorization: Optional[str] = Header(None),
) -> dict:
    """AI-разбор результата. Вызывается ПОСЛЕ показа уровня."""
    state = _TESTS.get(body.test_id)
    if state is None or not state.get("result"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "result_not_found")
    # Разбор — часть, ради которой с лендинга регистрируются. Анониму его
    # не отдаём (и заодно не тратим на него GPU).
    if state.get("public") and state.get("user_id") is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "auth_required")
    if state.get("report"):
        return {"report": state["report"]}

    result = state["result"]
    missed = [
        BY_ID[a["id"]] for a in state["answers"]
        if not a["correct"] and a["id"] in BY_ID
    ][:6]
    missed_lines = "\n".join(
        f"- [{q['level']}/{q.get('skill')}] {q['prompt']} → правильно "
        f"«{q['correct']}»." + (f" {q['note']}" if q.get("note") else "")
        for q in missed
    ) or "- ошибок почти не было"
    lst = result.get("listening")
    listen_line = (
        f"Понимание на слух: {lst['correct']} из {lst['total']}."
        if lst and lst.get("total") else "Аудирование не засчиталось."
    )
    skills_line = ", ".join(
        f"{sk} {st['correct']}/{st['total']}"
        for sk, st in sorted(result["by_skill"].items())
    )
    user_payload = (
        f"/no_think\n"
        f"Уровень по тесту: {result['cefr']}. "
        f"Верных ответов: {result['correct_cnt']} из {result['total_cnt']}.\n"
        f"По навыкам: {skills_line}.\n"
        f"{listen_line}\n"
        f"Ошибки:\n{missed_lines}\n\n"
        "Напиши разбор по структуре из системного промпта."
    )

    base_url = (settings.VLLM_BASE_URL or "").rstrip("/")
    if not base_url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "llm_not_configured")
    payload = {
        "model": settings.VLLM_MODEL_NAME or "",
        "messages": [
            {"role": "system", "content": _REPORT_SYSTEM},
            {"role": "user", "content": user_payload},
        ],
        "temperature": 0.6,
        "max_tokens": 500,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {
        "Authorization": f"Bearer {settings.VLLM_API_KEY or 'not-needed'}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=10.0)
        ) as client:
            resp = await client.post(
                f"{base_url}/chat/completions", json=payload, headers=headers,
            )
        resp.raise_for_status()
        content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception as exc:
        logger.warning("[level-test] разбор не сгенерирован: %r", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "llm_failed")

    # Гигиена reasoning-тегов — как в grammar.py и listening.py.
    content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL | re.IGNORECASE)
    content = content.strip()
    if not content:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "llm_empty")
    state["report"] = content

    if state.get("db_id"):
        from .db import Repo
        try:
            async with db_session() as session:
                await Repo(session).attach_level_test_report(state["db_id"], content)
                await session.commit()
        except Exception as exc:
            logger.warning("[level-test] разбор не записан: %r", exc)
    return {"report": content}

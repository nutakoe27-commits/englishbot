"""
exam_gen.py — генерация заданий ЕГЭ (раздел «Грамматика и лексика») в банк
exam_tasks с автопроверкой. Этап 2 подготовки к экзамену (docs/exam-prep.md).

Конвейер одного задания:
  1. generate  — модель пишет группу КИМ целиком (тексты + пропуски + ключ +
                 разбор по-русски) строго в JSON по схеме типа.
  2. validate  — структурная проверка: число и нумерация пропусков, формат
                 ответов, уникальность вариантов. Не прошло — задание не
                 сохраняется, ошибка попадает в отчёт job'а.
  3. blind     — вторая модель (та же, temperature 0) решает задание вслепую,
                 не видя ключа. Доля совпадений → quality 0–100, расхождения
                 → gen_meta.check (модератор видит альтернативную форму и
                 решает: добавить в допустимые или забраковать).
  4. store     — status='review'. В приложение попадают только 'published'
                 после ручной модерации в админке.

Форматы content (без ответов, уходит клиенту):
  gram_form / word_form:
    {"texts": [{"title": "...", "text": "... {{19}} ..."}],
     "items": [{"n": 19, "base": "SITUATE"}, ...]}
  lex_mcq:
    {"texts": [{"title": "...", "text": "... {{30}} ..."}],
     "items": [{"n": 30, "options": ["approach", "allow", "admit", "approve"]}]}
answer_key:
  gram_form / word_form: {"19": ["situated"]}     — список допустимых форм
  lex_mcq:               {"30": "3"}              — номер варианта, как в КИМ
explanation:              {"19": "Participle II в функции определения …"}

Job-раннер держит прогресс в памяти процесса: генерация одной группы — это
1–2 обращения к vLLM по 20–60 секунд, админка опрашивает статус.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import httpx

from .config import settings
from .db import db_session
from .db.repo import Repo

logger = logging.getLogger(__name__)

PROMPT_VERSION = "ege-2027-v1"

# ─── Группы заданий, которые умеет генерировать этап 2 ───────────────────────

TASK_GROUPS: dict[str, dict] = {
    "19-24": {"type": "gram_form", "nos": list(range(19, 25)), "texts": "1-2"},
    "25-29": {"type": "word_form", "nos": list(range(25, 30)), "texts": "1"},
    "30-36": {"type": "lex_mcq", "nos": list(range(30, 37)), "texts": "1"},
}

# Темы для текстов — из тематического содержания кодификатора. Реальные
# места и исторические факты допустимы (в демоверсии — Коринф, Эрмитаж),
# живые люди и спорные темы — нет.
TOPICS = [
    "an ancient city or a historical landmark", "a famous museum or gallery",
    "a national park or a natural wonder", "a river, a lake or a mountain range",
    "the history of a popular sport", "the history of an everyday invention",
    "a traditional holiday in an English-speaking country",
    "a Russian city and its history", "a scientific discovery told for teenagers",
    "how a well-known food or drink appeared", "a famous bridge, tower or building",
    "an animal species and its habits", "a school exchange programme",
    "a summer job or volunteering experience", "a family trip abroad",
    "life in a big city versus the countryside", "a hobby that became a career",
    "a public library or a bookshop with a story", "the history of the cinema or theatre",
    "an unusual sports competition", "learning a foreign language as a teenager",
    "a zoo, an aquarium or a botanical garden", "the story of a famous ship or train",
    "keeping healthy: sleep, food and exercise", "how the internet changed shopping",
    "a school science fair or a robotics club", "an old craft that is still alive",
    "a lighthouse, a castle or a fortress", "a space mission told simply",
    "a weather phenomenon and how people live with it",
]

# ─── Схема ответа модели по типам ────────────────────────────────────────────

_SYSTEM = (
    "You write tasks for the Russian state exam in English (ЕГЭ, ФИПИ format). "
    "You produce ONLY valid JSON, no markdown fences, no comments. Texts must be "
    "original, factual, neutral, suitable for 16–18-year-olds, CEFR B1–B2. "
    "Never mention living people, politics, religion, violence or brands. "
    "Gaps are written as {{N}} where N is the task number. Each gap number "
    "appears exactly once. Do not put the answer word anywhere near its gap."
)


def _prompt_gram_form(topic: str, nos: list[int]) -> str:
    first, last = nos[0], nos[-1]
    return f"""Create ЕГЭ tasks {first}–{last} (section "Грамматика и лексика", grammar transformation).
Format from the official demo: a coherent informational text of 90–130 words about {topic}.
It contains {len(nos)} gaps numbered {first}..{last}. For each gap the student sees a BASE word in
capital letters and must write the correct GRAMMATICAL form (not a derived word of another part of speech).
Cover different grammar points from this list, at most one gap per point:
- verb tense/aspect forms (Past Simple, Present Perfect, Past Continuous, Present Perfect Continuous, Future-in-the-Past)
- passive voice (Present/Past Simple Passive, Present Perfect Passive)
- irregular past forms and participles; Participle I / Participle II as an attribute
- comparative / superlative of adjectives and adverbs, including irregular ones
- plural of nouns including irregular ones
- ordinal numerals from cardinal ones (ONE → first, TWO → second)
- pronouns: personal (object case), possessive, absolute possessive, reflexive (I → me / my / mine / myself)
- modal verbs, conditionals 0/I/II, reported speech, "used to"
Rules: exactly one correct answer per gap in this context; the base word must be a single word in CAPITALS;
the answer must be a form of the base word (1–3 words, e.g. "was founded", "have been working").
Return JSON:
{{"title": "short title", "text": "text with {{{{{first}}}}} ... {{{{{last}}}}}",
 "items": [{{"n": {first}, "base": "SITUATE", "answer": "situated",
            "explanation_ru": "1–2 предложения по-русски: какое правило и почему именно эта форма"}}, ...]}}"""


def _prompt_word_form(topic: str, nos: list[int]) -> str:
    first, last = nos[0], nos[-1]
    return f"""Create ЕГЭ tasks {first}–{last} (section "Грамматика и лексика", word formation).
Format from the official demo: a coherent informational text of 90–130 words about {topic}.
It contains {len(nos)} gaps numbered {first}..{last}. For each gap the student sees a BASE word in capitals
and must form a RELATED word of a different part of speech using affixes so that it fits grammatically
and lexically. Use a variety of affixes from the codifier:
- nouns: -ance/-ence, -er/-or, -ing, -ist, -ity, -ment, -ness, -sion/-tion, -ship; prefixes un-, in-/im-, il-/ir-
- adjectives: -able/-ible, -al, -ed, -ful, -ian/-an, -ical, -ing, -ish, -ive, -less, -ly, -ous, -y; prefixes un-, in-/im-, il-/ir-, inter-, non-, post-, pre-
- adverbs: -ly (also with negative prefixes)
- verbs: dis-, mis-, re-, over-, under-; -ise/-ize, -en
Rules: exactly one correct answer per gap; the answer is ONE word that shares the root with the base word
and differs from it (e.g. CERTAIN → certainly, POSSIBLE → impossible, PRICE → priceless, RESIDE → residence);
at least one answer must use a negative prefix.
Return JSON:
{{"title": "short title", "text": "text with {{{{{first}}}}} ... {{{{{last}}}}}",
 "items": [{{"n": {first}, "base": "CERTAIN", "answer": "certainly",
            "explanation_ru": "1–2 предложения по-русски: какая часть речи нужна и какой аффикс"}}, ...]}}"""


def _prompt_lex_mcq(topic: str, nos: list[int]) -> str:
    first, last = nos[0], nos[-1]
    return f"""Create ЕГЭ tasks {first}–{last} (section "Грамматика и лексика", lexical multiple choice).
Format from the official demo: a coherent narrative or informational text of 150–200 words about {topic},
told in the first or third person, with {len(nos)} gaps numbered {first}..{last}. For each gap give FOUR options
of the SAME part of speech; only one fits the context. Test typical ЕГЭ lexical points, one per gap:
- collocations (make/do, take/have, pay attention, keep in touch)
- near-synonyms with different usage (say/tell/speak/talk, look/see/watch, travel/trip/journey)
- phrasal verbs (look after / look for / look up / look through)
- prepositions after verbs and adjectives (depend on, interested in, good at)
- -ed / -ing adjectives, linking words (although / however / despite / because)
Rules: exactly one option is correct; the other three must be clearly wrong in this context for a B2 reader;
options are single words or two-word phrasal verbs, lowercase, no duplicates.
Return JSON:
{{"title": "short title", "text": "text with {{{{{first}}}}} ... {{{{{last}}}}}",
 "items": [{{"n": {first}, "options": ["approach", "allow", "admit", "approve"], "correct": 3,
            "explanation_ru": "1–2 предложения по-русски: почему подходит именно это слово и чем не подходят остальные"}}, ...]}}"""


_PROMPTS = {"gram_form": _prompt_gram_form, "word_form": _prompt_word_form, "lex_mcq": _prompt_lex_mcq}


# ─── LLM-вызов ───────────────────────────────────────────────────────────────

LLMCall = Callable[[str, str, int, float], Awaitable[str]]


async def _call_vllm(system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    base_url = (settings.VLLM_BASE_URL or "").rstrip("/")
    if not base_url:
        raise RuntimeError("LLM not configured (VLLM_BASE_URL)")
    payload = {
        "model": settings.VLLM_MODEL_NAME or "",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {
        "Authorization": f"Bearer {settings.VLLM_API_KEY or 'not-needed'}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        if resp.status_code != 200:
            logger.error("[exam_gen LLM] HTTP %s: %s", resp.status_code, resp.text[:300])
            raise RuntimeError(f"LLM returned {resp.status_code}")
        data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        content = ""
    content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL | re.IGNORECASE)
    return content.strip()


# Подменяется в тестах.
LLM_CALL: LLMCall = _call_vllm


# ─── Парсинг и нормализация ──────────────────────────────────────────────────

_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_GAP_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")


def parse_json_object(raw: str) -> Optional[dict]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    m = _OBJ_RE.search(text)
    for candidate in (text, m.group(0) if m else None):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return None


def norm_answer(s: str) -> str:
    """Форма для сравнения: строчные, без пробелов, дефисов и апострофов —
    как на бланке ответов № 1 («havebeenworking»)."""
    return re.sub(r"[\s\-'’`]", "", (s or "").strip().lower())


def _normalize_gaps(text: str) -> str:
    return _GAP_RE.sub(lambda m: "{{" + m.group(1) + "}}", text or "")


# ─── Валидация ───────────────────────────────────────────────────────────────


class ValidationError(Exception):
    pass


def validate_generated(task_type: str, nos: list[int], data: dict) -> tuple[dict, dict, dict, list[str]]:
    """Проверить ответ модели и разложить на (content, answer_key, explanation, notes).

    Бросает ValidationError, если задание нельзя сохранить. notes — мягкие
    замечания для модератора (сохраняются, но не блокируют).
    """
    notes: list[str] = []
    text = _normalize_gaps(str(data.get("text") or ""))
    title = str(data.get("title") or "").strip()[:120]
    items = data.get("items")
    if not text or not isinstance(items, list):
        raise ValidationError("нет текста или списка items")

    found = [int(n) for n in _GAP_RE.findall(text)]
    if sorted(found) != sorted(nos):
        raise ValidationError(f"пропуски в тексте {sorted(found)} ≠ ожидаемые {nos}")
    if len(found) != len(set(found)):
        raise ValidationError("пропуск встречается дважды")

    by_n: dict[int, dict] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            n = int(it.get("n"))
        except (TypeError, ValueError):
            continue
        by_n[n] = it
    if sorted(by_n) != sorted(nos):
        raise ValidationError(f"items {sorted(by_n)} ≠ ожидаемые {nos}")

    words = len(re.findall(r"[A-Za-z’']+", _GAP_RE.sub(" ", text)))
    lo, hi = (120, 260) if task_type == "lex_mcq" else (60, 170)
    if words < lo or words > hi:
        notes.append(f"объём текста {words} слов (ожидалось {lo}–{hi})")

    content_items: list[dict] = []
    answer_key: dict[str, object] = {}
    explanation: dict[str, str] = {}

    for n in nos:
        it = by_n[n]
        expl = str(it.get("explanation_ru") or "").strip()
        if not expl:
            notes.append(f"{n}: нет разбора")
        explanation[str(n)] = expl

        if task_type in ("gram_form", "word_form"):
            base = str(it.get("base") or "").strip().upper()
            answer = str(it.get("answer") or "").strip()
            if not re.fullmatch(r"[A-Z][A-Z\-]{0,24}", base):
                raise ValidationError(f"{n}: base {base!r} не одно слово заглавными")
            if not re.fullmatch(r"[A-Za-z’'\- ]{1,40}", answer):
                raise ValidationError(f"{n}: answer {answer!r} не слово/словосочетание")
            answer_l = answer.lower().strip()
            if task_type == "word_form":
                if answer_l == base.lower():
                    raise ValidationError(f"{n}: словообразование без изменения слова ({base})")
                if " " in answer_l:
                    raise ValidationError(f"{n}: словообразование — ответ из нескольких слов ({answer})")
                root = base.lower()[: max(3, len(base) - 2)]
                if root not in answer_l:
                    notes.append(f"{n}: {answer} не содержит корень {base} — проверить")
            else:
                if answer_l == base.lower():
                    notes.append(f"{n}: форма совпадает с базой ({base}) — допустимо, но проверить")
                if len(answer_l.split()) > 3:
                    raise ValidationError(f"{n}: ответ длиннее 3 слов ({answer})")
            # Ответ не должен торчать в тексте рядом с пропуском.
            ctx = re.search(r"(.{0,40})\{\{" + str(n) + r"\}\}(.{0,40})", text, re.DOTALL)
            if ctx and re.search(r"(?<!\w)" + re.escape(answer_l) + r"(?!\w)", (ctx.group(1) + ctx.group(2)).lower()):
                notes.append(f"{n}: ответ «{answer}» встречается рядом с пропуском")
            content_items.append({"n": n, "base": base})
            answer_key[str(n)] = [answer_l]
        else:  # lex_mcq
            options = it.get("options")
            if not isinstance(options, list) or len(options) != 4:
                raise ValidationError(f"{n}: нужно ровно 4 варианта")
            options = [str(o).strip() for o in options]
            if any(not o for o in options) or len({o.lower() for o in options}) != 4:
                raise ValidationError(f"{n}: варианты пустые или повторяются")
            try:
                correct = int(it.get("correct"))
            except (TypeError, ValueError):
                raise ValidationError(f"{n}: нет номера правильного варианта")
            if not 1 <= correct <= 4:
                raise ValidationError(f"{n}: correct={correct} вне 1..4")
            content_items.append({"n": n, "options": options})
            answer_key[str(n)] = str(correct)

    content = {"texts": [{"title": title, "text": text}], "items": content_items}
    return content, answer_key, explanation, notes


# ─── Слепое решение второй моделью ───────────────────────────────────────────


def _render_for_solver(task_type: str, content: dict) -> str:
    text = "\n\n".join(
        (f"{t.get('title')}\n" if t.get("title") else "") + str(t.get("text") or "")
        for t in content.get("texts", [])
    )
    lines = [text, ""]
    for it in content["items"]:
        n = it["n"]
        if task_type == "lex_mcq":
            opts = " ".join(f"{i + 1}) {o}" for i, o in enumerate(it["options"]))
            lines.append(f"{n}: {opts}")
        else:
            lines.append(f"{n}: {it['base']}")
    return "\n".join(lines)


_SOLVER_SYSTEM = (
    "You are an expert teacher solving a Russian ЕГЭ English task. Answer ONLY with a JSON object "
    "mapping each gap number to your answer. No explanations."
)

_SOLVER_USER = {
    "gram_form": "Fill each gap {{N}} with the correct grammatical form of the base word given after the text. "
                 "Return {\"19\": \"situated\", ...} — the form as it should be written in the gap.",
    "word_form": "Fill each gap {{N}} with a word formed from the base word given after the text (change the part of speech with affixes). "
                 "Return {\"25\": \"certainly\", ...}.",
    "lex_mcq": "For each gap {{N}} choose the option that fits the context. Return {\"30\": 3, ...} with option numbers 1–4.",
}


async def blind_check(task_type: str, content: dict, answer_key: dict, llm: LLMCall) -> dict:
    """Решить задание вслепую и сравнить с ключом.

    Возвращает {"quality": 0..100, "items": {"19": {"key": ..., "model": ..., "ok": bool}}, "error": str|None}.
    """
    rendered = _render_for_solver(task_type, content)
    try:
        raw = await llm(_SOLVER_SYSTEM, _SOLVER_USER[task_type] + "\n\n" + rendered, 400, 0.0)
        data = parse_json_object(raw) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("[exam_gen] blind check failed: %s", e)
        return {"quality": None, "items": {}, "error": str(e)[:200]}

    items: dict[str, dict] = {}
    ok_cnt = 0
    for k, key_val in answer_key.items():
        model_val = data.get(k, data.get(int(k)) if k.isdigit() else None)
        if task_type == "lex_mcq":
            m = str(model_val).strip() if model_val is not None else ""
            ok = m == str(key_val)
        else:
            m = str(model_val or "").strip()
            ok = norm_answer(m) in {norm_answer(a) for a in key_val}
        ok_cnt += int(ok)
        items[k] = {"key": key_val, "model": m, "ok": ok}
    total = len(answer_key) or 1
    return {"quality": round(100 * ok_cnt / total), "items": items, "error": None}


# ─── Генерация одной группы ──────────────────────────────────────────────────


async def generate_one(
    *, exam: str, task_no: str, topic: Optional[str] = None,
    llm: Optional[LLMCall] = None, store: bool = True,
) -> dict:
    """Сгенерировать, проверить и (если store) сохранить одну группу.

    Возвращает {"task_id", "quality", "notes", "content", "answer_key", "explanation", "check"}.
    """
    llm = llm or LLM_CALL
    group = TASK_GROUPS.get(task_no)
    if not group:
        raise ValueError(f"группа {task_no} не поддерживается")
    task_type, nos = group["type"], group["nos"]
    topic = topic or random.choice(TOPICS)

    raw = await llm(_SYSTEM, _PROMPTS[task_type](topic, nos), 2200, 0.8)
    data = parse_json_object(raw)
    if data is None:
        raise ValidationError("модель вернула не JSON")
    content, answer_key, explanation, notes = validate_generated(task_type, nos, data)
    check = await blind_check(task_type, content, answer_key, llm)

    gen_meta = {
        "model": settings.VLLM_MODEL_NAME or "",
        "prompt_version": PROMPT_VERSION,
        "topic": topic,
        "notes": notes,
        "check": check,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    result = {
        "task_id": None, "quality": check.get("quality"), "notes": notes,
        "content": content, "answer_key": answer_key, "explanation": explanation,
        "check": check, "topic": topic,
    }
    if store:
        async with db_session() as s:
            repo = Repo(s)
            result["task_id"] = await repo.exam_task_create(
                exam=exam, task_no=task_no, task_type=task_type, content=content,
                answer_key=answer_key, explanation=explanation, status="review",
                source="llm", quality=check.get("quality"), gen_meta=gen_meta,
                topic=topic[:120], level_hint="B2" if task_type == "lex_mcq" else "B1",
            )
    return result


# ─── Job-раннер для админки ──────────────────────────────────────────────────

MAX_PER_JOB = 20
_JOBS: dict[str, dict] = {}
_JOBS_KEEP = 50


def _prune_jobs() -> None:
    if len(_JOBS) <= _JOBS_KEEP:
        return
    for jid in sorted(_JOBS, key=lambda j: _JOBS[j]["created_at"])[: len(_JOBS) - _JOBS_KEEP]:
        if _JOBS[jid]["status"] != "running":
            _JOBS.pop(jid, None)


def start_job(*, exam: str, task_no: str, count: int, topic: Optional[str] = None) -> str:
    if task_no not in TASK_GROUPS:
        raise ValueError(f"группа {task_no} не поддерживается")
    count = max(1, min(int(count), MAX_PER_JOB))
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id, "exam": exam, "task_no": task_no, "requested": count,
        "done": 0, "failed": 0, "status": "running", "errors": [],
        "task_ids": [], "qualities": [],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "finished_at": None,
    }
    _JOBS[job_id] = job
    _prune_jobs()

    async def _run() -> None:
        for _ in range(count):
            try:
                r = await generate_one(exam=exam, task_no=task_no, topic=topic)
                job["done"] += 1
                job["task_ids"].append(r["task_id"])
                job["qualities"].append(r["quality"])
            except Exception as e:  # noqa: BLE001
                job["failed"] += 1
                job["errors"].append(str(e)[:200])
                logger.warning("[exam_gen] job %s: %s", job_id, e)
        job["status"] = "done"
        job["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    job["_task"] = asyncio.create_task(_run())
    return job_id


def job_status(job_id: str) -> Optional[dict]:
    job = _JOBS.get(job_id)
    if not job:
        return None
    return {k: v for k, v in job.items() if not k.startswith("_")}


def jobs_recent(limit: int = 10) -> list[dict]:
    rows = sorted(_JOBS.values(), key=lambda j: j["created_at"], reverse=True)[:limit]
    return [{k: v for k, v in j.items() if not k.startswith("_")} for j in rows]

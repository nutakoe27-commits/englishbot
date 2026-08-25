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

AI отвечает за то, где он незаменим: пишет персональный разбор по
фактическим ошибкам и говорит, что делать дальше.
"""

from __future__ import annotations

import logging
import random
import re
import secrets
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from .config import settings
from .db import db_session
from .level_test_bank import BANK, BY_ID, BY_LEVEL, LEVELS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/level-test", tags=["LevelTest"])

TOTAL_QUESTIONS = 12
START_LEVEL_IDX = LEVELS.index("B1")
# Уровень засчитывается, если по нему было хотя бы столько попыток...
MIN_ATTEMPTS_PER_LEVEL = 2
# ...и доля верных не ниже этой.
PASS_RATIO = 0.6

# test_id → состояние. Живёт в памяти: тест короткий, переживать рестарт
# backend'а ему незачем (тот же приём, что в grammar._SESSION_STORE).
_TESTS: dict[str, dict] = {}
_TEST_TTL_SEC = 3600
_TESTS_MAX = 500


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


def _public(q: dict, index: int) -> dict:
    """Задание без правильного ответа — его клиент знать не должен."""
    choices = list(q["choices"])
    random.shuffle(choices)
    return {
        "id": q["id"],
        "index": index,
        "total": TOTAL_QUESTIONS,
        "level": q["level"],
        "skill": q["skill"],
        "prompt": q["prompt"],
        "choices": choices,
    }


def _pick(state: dict) -> Optional[dict]:
    """Следующее задание с текущего уровня лестницы.

    Если на уровне всё выдано — идём к ближайшему по расстоянию уровню,
    где ещё есть неиспользованные задания. Так тест не обрывается, даже
    если человек залип на одной ступени.
    """
    used: set[str] = state["used"]
    idx = state["level_idx"]
    order = sorted(range(len(LEVELS)), key=lambda i: (abs(i - idx), i))
    for i in order:
        pool = [q for q in BY_LEVEL[LEVELS[i]] if q["id"] not in used]
        if pool:
            return random.choice(pool)
    return None


def _grade(answers: list[dict]) -> dict:
    """Итог: CEFR, разбивка по уровням и навыкам, слабые места.

    Уровень = самый высокий, на котором было не меньше MIN_ATTEMPTS_PER_LEVEL
    попыток и доля верных не ниже PASS_RATIO. Ступени с одной попыткой
    игнорируются как шум.
    """
    by_level: dict[str, dict] = {lv: {"correct": 0, "total": 0} for lv in LEVELS}
    by_skill: dict[str, dict] = {}
    for a in answers:
        lv = a["level"]
        by_level[lv]["total"] += 1
        by_level[lv]["correct"] += int(bool(a["correct"]))
        sk = by_skill.setdefault(a["skill"], {"correct": 0, "total": 0})
        sk["total"] += 1
        sk["correct"] += int(bool(a["correct"]))

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
    }


# ─── Модели запросов ────────────────────────────────────────────────────────

class _StartIn(BaseModel):
    init_data: Optional[str] = None


class _AnswerIn(BaseModel):
    init_data: Optional[str] = None
    test_id: str
    question_id: str
    answer: str = Field(default="", max_length=200)


class _ReportIn(BaseModel):
    init_data: Optional[str] = None
    test_id: str


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

@router.post("/start")
async def start_test(
    body: _StartIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Начать тест. Первый вопрос отдаётся сразу — LLM не участвует."""
    user_id = await _resolve(body.init_data, authorization)
    _gc_tests()
    test_id = secrets.token_urlsafe(16)
    state = {
        "user_id": user_id,
        "level_idx": START_LEVEL_IDX,
        "used": set(),
        "answers": [],
        "expires_at": time.time() + _TEST_TTL_SEC,
    }
    q = _pick(state)
    if q is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "bank_empty")
    state["used"].add(q["id"])
    state["current"] = q["id"]
    _TESTS[test_id] = state

    prev = None
    if user_id is not None:
        from .db import Repo
        async with db_session() as session:
            prev = await Repo(session).last_level_test(user_id)
    return {
        "test_id": test_id,
        "question": _public(q, index=1),
        "previous": prev,
    }


@router.post("/answer")
async def answer_question(
    body: _AnswerIn, authorization: Optional[str] = Header(None),
) -> dict:
    """Принять ответ, вернуть разбор и следующий вопрос.

    На последнем вопросе здесь же приходит готовый результат — клиент
    показывает уровень мгновенно, не дожидаясь AI-разбора.
    """
    state = _TESTS.get(body.test_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "test_not_found")
    if state["current"] != body.question_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "wrong_question")
    q = BY_ID.get(body.question_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "question_not_found")

    is_correct = body.answer.strip() == q["correct"]
    state["answers"].append({
        "id": q["id"], "level": q["level"], "skill": q["skill"],
        "correct": is_correct, "answer": body.answer.strip()[:200],
    })
    # Лестница: верно — ступень вверх, неверно — вниз.
    state["level_idx"] = max(0, min(
        len(LEVELS) - 1,
        state["level_idx"] + (1 if is_correct else -1),
    ))
    state["expires_at"] = time.time() + _TEST_TTL_SEC

    feedback = {
        "correct": is_correct,
        "correct_answer": q["correct"],
        "note": q["note"],
    }

    if len(state["answers"]) >= TOTAL_QUESTIONS:
        result = _grade(state["answers"])
        state["result"] = result
        state["current"] = None
        if state["user_id"] is not None:
            from .db import Repo
            try:
                async with db_session() as session:
                    repo = Repo(session)
                    state["db_id"] = await repo.save_level_test(
                        user_id=state["user_id"], cefr=result["cefr"],
                        correct_cnt=result["correct_cnt"],
                        total_cnt=result["total_cnt"],
                        answers=state["answers"],
                    )
                    await session.commit()
            except Exception as exc:
                # До миграции 0035 таблицы нет — результат важнее записи.
                logger.warning("[level-test] не сохранён: %r", exc)
        return {**feedback, "done": True, "result": result}

    nxt = _pick(state)
    if nxt is None:
        result = _grade(state["answers"])
        state["result"] = result
        state["current"] = None
        return {**feedback, "done": True, "result": result}
    state["used"].add(nxt["id"])
    state["current"] = nxt["id"]
    return {
        **feedback,
        "done": False,
        "question": _public(nxt, index=len(state["answers"]) + 1),
    }


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
    if state.get("report"):
        return {"report": state["report"]}

    result = state["result"]
    missed = [
        BY_ID[a["id"]] for a in state["answers"]
        if not a["correct"] and a["id"] in BY_ID
    ][:6]
    missed_lines = "\n".join(
        f"- [{q['level']}/{q['skill']}] {q['prompt']} → правильно «{q['correct']}». {q['note']}"
        for q in missed
    ) or "- ошибок почти не было"
    skills_line = ", ".join(
        f"{sk} {st['correct']}/{st['total']}"
        for sk, st in sorted(result["by_skill"].items())
    )
    user_payload = (
        f"/no_think\n"
        f"Уровень по тесту: {result['cefr']}. "
        f"Верных ответов: {result['correct_cnt']} из {result['total_cnt']}.\n"
        f"По навыкам: {skills_line}.\n"
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

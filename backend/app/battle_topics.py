"""Каталог тем для Battle Mode.

Темы хранятся в коде (не в БД), потому что:
  1. Их немного (15 штук для MVP).
  2. Мы часто будем править формулировки — проще через код-ревью.
  3. Включают английский текст для ИИ и русский — для юзера.

Каждая тема — ситуация на 60 секунд, в которой оба участника спорят
с противоположных позиций. Позиции назначаются случайно в момент accept.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BattleTopic:
    key: str              # уникальный ключ (храним в battles.topic_key)
    title_ru: str         # заголовок для превью в чате
    prompt_en: str        # инструкция для участников (на английском)
    side_a_ru: str        # какую позицию занимает initiator (показываем ему в личке)
    side_b_ru: str        # какую позицию занимает opponent
    judging_hint: str     # подсказка судье, на что смотреть


# ─── Каталог (15 тем для MVP) ────────────────────────────────────────────

_TOPICS: list[BattleTopic] = [
    BattleTopic(
        key="dishes",
        title_ru="Кто моет посуду",
        prompt_en=(
            "You two share an apartment. There's a mountain of dirty dishes "
            "in the sink. Each of you must argue that the OTHER person "
            "should wash them. Be persuasive, funny, and use real reasons. "
            "60 seconds."
        ),
        side_a_ru="Ты утверждаешь, что посуду должен мыть соперник.",
        side_b_ru="Ты утверждаешь, что посуду должен мыть соперник.",
        judging_hint="Кто убедительнее переложил ответственность?",
    ),
    BattleTopic(
        key="tourist_pitch",
        title_ru="Пригласи туриста",
        prompt_en=(
            "A tourist has 48 hours and must choose: your country OR the "
            "opponent's country. Pitch yours in 60 seconds. Concrete places, "
            "food, vibes — no clichés."
        ),
        side_a_ru="Расскажи туристу, почему твоя страна — лучший выбор на 48 часов.",
        side_b_ru="Расскажи туристу, почему твоя страна — лучший выбор на 48 часов.",
        judging_hint="Чей пич конкретнее, живее и заманчивее?",
    ),
    BattleTopic(
        key="friday_off",
        title_ru="Выходной в пятницу",
        prompt_en=(
            "You're both asking your boss for this Friday off — but the boss "
            "will give it to only ONE of you. Make your case in 60 seconds. "
            "Be creative: weddings, emergencies, unfinished projects, anything."
        ),
        side_a_ru="Убеди воображаемого босса отдать пятничный выходной тебе, а не сопернику.",
        side_b_ru="Убеди воображаемого босса отдать пятничный выходной тебе, а не сопернику.",
        judging_hint="Чья история правдоподобнее и эмоциональнее?",
    ),
    BattleTopic(
        key="cat_vs_dog",
        title_ru="Коты vs собаки",
        prompt_en=(
            "The oldest debate. In 60 seconds, defend your animal — with real "
            "arguments, not memes. Cover: companionship, independence, "
            "practicality, emotional value."
        ),
        side_a_ru="Ты защищаешь КОТОВ.",
        side_b_ru="Ты защищаешь СОБАК.",
        judging_hint="Кто привёл более взвешенные аргументы (а не просто «они милые»)?",
    ),
    BattleTopic(
        key="best_breakfast",
        title_ru="Лучший завтрак",
        prompt_en=(
            "Each of you proposes the ultimate breakfast. Describe the dish, "
            "why it beats every other breakfast in the world, and how you "
            "eat it. Make the judge hungry. 60 seconds."
        ),
        side_a_ru="Опиши свой идеальный завтрак и докажи, что он лучший в мире.",
        side_b_ru="Опиши свой идеальный завтрак и докажи, что он лучший в мире.",
        judging_hint="Кто заставил судью реально захотеть это съесть?",
    ),
    BattleTopic(
        key="worst_job",
        title_ru="Худшая работа",
        prompt_en=(
            "Each of you describes the worst job you can imagine and "
            "explains why it would destroy your soul faster than the "
            "opponent's. Details matter: smell, hours, boss, pay."
        ),
        side_a_ru="Опиши самую адскую работу и докажи, что она хуже того, что назовёт соперник.",
        side_b_ru="Опиши самую адскую работу и докажи, что она хуже того, что назовёт соперник.",
        judging_hint="Чьё описание ярче, смешнее, детальнее?",
    ),
    BattleTopic(
        key="superpower",
        title_ru="Выбери суперсилу",
        prompt_en=(
            "You can have ONE superpower: flight or invisibility. Each of "
            "you picks one and argues it's the practically better choice. "
            "Use real-life scenarios. 60 seconds."
        ),
        side_a_ru="Ты выбираешь ПОЛЁТ.",
        side_b_ru="Ты выбираешь НЕВИДИМОСТЬ.",
        judging_hint="Чьи примеры практичнее?",
    ),
    BattleTopic(
        key="time_travel",
        title_ru="Машина времени",
        prompt_en=(
            "You just got a one-way time machine ticket. One of you goes to "
            "1925, the other to 2125. Defend your destination in 60 seconds "
            "— why is it the smarter choice?"
        ),
        side_a_ru="Ты отправляешься в 1925 год.",
        side_b_ru="Ты отправляешься в 2125 год.",
        judging_hint="Кто показал более продуманные причины?",
    ),
    BattleTopic(
        key="worst_movie",
        title_ru="Худший фильм",
        prompt_en=(
            "Pitch the worst movie you've ever seen. Explain WHY it's bad "
            "— plot holes, acting, pacing — not just 'it sucks'. The one "
            "whose movie sounds more hilariously terrible wins."
        ),
        side_a_ru="Разнеси самый ужасный фильм, какой ты смотрел.",
        side_b_ru="Разнеси самый ужасный фильм, какой ты смотрел.",
        judging_hint="Чья критика детальнее и смешнее?",
    ),
    BattleTopic(
        key="convince_alien",
        title_ru="Убеди инопланетянина",
        prompt_en=(
            "An alien has landed and is about to destroy Earth. Each of you "
            "has 60 seconds to convince it humanity is worth saving. Use ONE "
            "specific example — not abstract 'love and kindness'."
        ),
        side_a_ru="Убеди инопланетянина не уничтожать Землю. Один конкретный пример.",
        side_b_ru="Убеди инопланетянина не уничтожать Землю. Один конкретный пример.",
        judging_hint="Кто привёл самый неожиданный и конкретный пример?",
    ),
    BattleTopic(
        key="invention",
        title_ru="Твоё изобретение",
        prompt_en=(
            "You have one minute to pitch an invention that would change "
            "daily life. Explain the problem, the solution, and how people "
            "would use it. The one that sounds more real and useful wins."
        ),
        side_a_ru="Предложи изобретение, которое изменит быт людей.",
        side_b_ru="Предложи изобретение, которое изменит быт людей.",
        judging_hint="Чьё изобретение конкретнее и реалистичнее?",
    ),
    BattleTopic(
        key="bad_advice",
        title_ru="Плохой совет",
        prompt_en=(
            "Give the WORST possible career advice — but sound convincing "
            "while doing it. The goal: make it sound like a real guru "
            "speech while the advice itself is terrible. 60 seconds."
        ),
        side_a_ru="Дай самый ужасный карьерный совет, но звучи как гуру.",
        side_b_ru="Дай самый ужасный карьерный совет, но звучи как гуру.",
        judging_hint="Кто звучит увереннее, при том что совет очевидно вредный?",
    ),
    BattleTopic(
        key="last_meal",
        title_ru="Последняя трапеза",
        prompt_en=(
            "If this was your last meal on Earth, what would you eat? "
            "Describe it with enough detail that the judge can taste it. "
            "No 'pizza' — tell us WHAT pizza, from WHERE, with WHOM."
        ),
        side_a_ru="Опиши последнюю трапезу своей жизни. Детально.",
        side_b_ru="Опиши последнюю трапезу своей жизни. Детально.",
        judging_hint="Чьё описание заставило почувствовать вкус, запах, момент?",
    ),
    BattleTopic(
        key="unpopular_opinion",
        title_ru="Непопулярное мнение",
        prompt_en=(
            "Share a genuinely unpopular opinion — something most people "
            "disagree with — and defend it for 60 seconds. No obvious "
            "troll positions. Make it thoughtful."
        ),
        side_a_ru="Вырази непопулярное мнение и защищай его.",
        side_b_ru="Вырази непопулярное мнение и защищай его.",
        judging_hint="Чьё мнение более смелое и лучше аргументировано?",
    ),
    BattleTopic(
        key="roast_yourself",
        title_ru="Роаст самого себя",
        prompt_en=(
            "Roast yourself. 60 seconds of self-deprecating humor about "
            "your own quirks, habits, or flaws. Must be funny — NOT sad. "
            "The one who makes the judge laugh louder wins."
        ),
        side_a_ru="Посмейся над собой. 60 секунд самоиронии. Смешно, не грустно.",
        side_b_ru="Посмейся над собой. 60 секунд самоиронии. Смешно, не грустно.",
        judging_hint="Кто смешнее и без self-pity?",
    ),
]


_BY_KEY: dict[str, BattleTopic] = {t.key: t for t in _TOPICS}


def pick_random() -> BattleTopic:
    """Случайная тема для нового battle."""
    return random.choice(_TOPICS)


def get_by_key(key: str) -> Optional[BattleTopic]:
    """Загрузить тему по ключу (для результатов, рендеринга)."""
    return _BY_KEY.get(key)


def all_topics() -> list[BattleTopic]:
    return list(_TOPICS)

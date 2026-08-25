"""Калиброванный банк заданий для теста уровня английского (CEFR A1–C1).

Почему банк рукописный, а не генерится LLM:

1. **Скорость.** Один сгенерированный вопрос — около 2.5 секунд. Двенадцать
   вопросов подряд превращаются в полминуты ожидания, размазанного по тесту.
   Отсюда вопрос отдаётся из памяти мгновенно, LLM во время прохождения не
   вызывается вообще.
2. **Сравнимость.** Тест — измерительный прибор. Если задания каждый раз
   новые, «уровень в августе против декабря» посчитать нельзя: сравниваются
   разные измерения. С фиксированным банком повторный тест показывает
   реальный прогресс.
3. **Качество.** Кривое задание в тренировке досадно, в тесте уровня —
   подрывает доверие ко всему продукту.

AI подключается после теста: пишет персональный разбор по фактическим
ответам (см. level_test.py).

Формат задания:
    id       — стабильный, НЕ переиспользовать при правках: по нему
               считается статистика и сравниваются прохождения
    level    — CEFR-уровень задания
    skill    — что проверяем (для разбивки в результате)
    prompt   — предложение с пропуском ___
    choices  — 4 варианта, ровно один верный
    correct  — верный вариант (обязан быть среди choices)
    note     — короткое пояснение по-русски, показывается в разборе

Инвариант: prompt с подставленным correct должен читаться как корректное
английское предложение. Проверяется тестом в level_test.py::validate_bank().
"""

from __future__ import annotations

LEVELS: tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1")

# Навыки — для разбивки результата. Совместимы с категориями grammar.py
# там, где это осмысленно.
SKILLS: tuple[str, ...] = ("tense", "article", "preposition", "word_choice", "structure")

BANK: list[dict] = [
    # ─── A1 ──────────────────────────────────────────────────────────
    {"id": "a1_01", "level": "A1", "skill": "tense",
     "prompt": "She ___ coffee every morning.",
     "choices": ["drink", "drinks", "drinking", "is drink"], "correct": "drinks",
     "note": "Present Simple, третье лицо единственного числа — окончание -s."},
    {"id": "a1_02", "level": "A1", "skill": "article",
     "prompt": "I have ___ apple in my bag.",
     "choices": ["a", "an", "the", "—"], "correct": "an",
     "note": "Перед гласным звуком ставится an."},
    {"id": "a1_03", "level": "A1", "skill": "preposition",
     "prompt": "My birthday is ___ May.",
     "choices": ["at", "on", "in", "to"], "correct": "in",
     "note": "С месяцами используется in."},
    {"id": "a1_04", "level": "A1", "skill": "structure",
     "prompt": "___ you like pizza?",
     "choices": ["Do", "Does", "Are", "Is"], "correct": "Do",
     "note": "Вопрос в Present Simple со you — вспомогательный do."},
    {"id": "a1_05", "level": "A1", "skill": "word_choice",
     "prompt": "There ___ two cats in the garden.",
     "choices": ["is", "are", "be", "has"], "correct": "are",
     "note": "Множественное число — there are."},
    {"id": "a1_06", "level": "A1", "skill": "tense",
     "prompt": "Look! The baby ___ .",
     "choices": ["sleeps", "sleep", "is sleeping", "slept"], "correct": "is sleeping",
     "note": "Действие прямо сейчас — Present Continuous."},
    {"id": "a1_07", "level": "A1", "skill": "structure",
     "prompt": "This is my sister. ___ name is Anna.",
     "choices": ["She", "Her", "Hers", "She's"], "correct": "Her",
     "note": "Перед существительным нужно притяжательное местоимение her."},
    {"id": "a1_08", "level": "A1", "skill": "preposition",
     "prompt": "The book is ___ the table.",
     "choices": ["in", "on", "at", "by"], "correct": "on",
     "note": "На горизонтальной поверхности — on."},
    {"id": "a1_09", "level": "A1", "skill": "word_choice",
     "prompt": "How ___ water do you drink a day?",
     "choices": ["many", "much", "lot", "some"], "correct": "much",
     "note": "Water — неисчисляемое, поэтому how much."},
    {"id": "a1_10", "level": "A1", "skill": "tense",
     "prompt": "Yesterday I ___ at home all day.",
     "choices": ["am", "was", "were", "be"], "correct": "was",
     "note": "Прошедшее время глагола be для I — was."},

    # ─── A2 ──────────────────────────────────────────────────────────
    {"id": "a2_01", "level": "A2", "skill": "tense",
     "prompt": "We ___ to Italy last summer.",
     "choices": ["go", "went", "have gone", "are going"], "correct": "went",
     "note": "Last summer — конкретное завершённое прошлое, Past Simple."},
    {"id": "a2_02", "level": "A2", "skill": "structure",
     "prompt": "This film is ___ than the one we saw yesterday.",
     "choices": ["interesting", "more interesting", "most interesting", "the interesting"],
     "correct": "more interesting",
     "note": "Сравнение длинного прилагательного — more + прилагательное."},
    {"id": "a2_03", "level": "A2", "skill": "tense",
     "prompt": "I ___ finish this report before six.",
     "choices": ["must to", "have to", "have", "musting"], "correct": "have to",
     "note": "Внешняя необходимость — have to + инфинитив без to."},
    {"id": "a2_04", "level": "A2", "skill": "preposition",
     "prompt": "She's been waiting ___ the bus stop for ten minutes.",
     "choices": ["in", "on", "at", "to"], "correct": "at",
     "note": "Конкретная точка в пространстве — at."},
    {"id": "a2_05", "level": "A2", "skill": "word_choice",
     "prompt": "There isn't ___ milk left in the fridge.",
     "choices": ["some", "any", "many", "a"], "correct": "any",
     "note": "В отрицательных предложениях с неисчисляемыми — any."},
    {"id": "a2_06", "level": "A2", "skill": "tense",
     "prompt": "If it rains tomorrow, we ___ at home.",
     "choices": ["stay", "will stay", "stayed", "would stay"], "correct": "will stay",
     "note": "Первый тип условия: if + Present Simple, главная часть — will."},
    {"id": "a2_07", "level": "A2", "skill": "article",
     "prompt": "She plays ___ piano beautifully.",
     "choices": ["a", "an", "the", "—"], "correct": "the",
     "note": "С музыкальными инструментами используется определённый артикль."},
    {"id": "a2_08", "level": "A2", "skill": "structure",
     "prompt": "I'm not very good ___ cooking.",
     "choices": ["in", "at", "on", "with"], "correct": "at",
     "note": "Устойчивое сочетание good at."},
    {"id": "a2_09", "level": "A2", "skill": "word_choice",
     "prompt": "He speaks English ___ than his brother.",
     "choices": ["good", "better", "best", "more good"], "correct": "better",
     "note": "Сравнительная степень наречия well — better."},
    {"id": "a2_10", "level": "A2", "skill": "tense",
     "prompt": "They ___ dinner when I called.",
     "choices": ["had", "were having", "have had", "have"], "correct": "were having",
     "note": "Действие в процессе в момент в прошлом — Past Continuous."},

    # ─── B1 ──────────────────────────────────────────────────────────
    {"id": "b1_01", "level": "B1", "skill": "tense",
     "prompt": "I ___ that movie two weeks ago.",
     "choices": ["have seen", "saw", "see", "am seeing"], "correct": "saw",
     "note": "Two weeks ago — завершённый период, Past Simple."},
    {"id": "b1_02", "level": "B1", "skill": "tense",
     "prompt": "I ___ breakfast yet. It's still early.",
     "choices": ["haven't had", "didn't have", "don't have", "hadn't"],
     "correct": "haven't had",
     "note": "Слово yet и незавершённый период указывают на Present Perfect."},
    {"id": "b1_03", "level": "B1", "skill": "structure",
     "prompt": "The report ___ by the whole team last month.",
     "choices": ["wrote", "was written", "has written", "is writing"],
     "correct": "was written",
     "note": "Пассивный залог в прошедшем: was/were + причастие."},
    {"id": "b1_04", "level": "B1", "skill": "word_choice",
     "prompt": "She suggested ___ a different route.",
     "choices": ["to take", "take", "taking", "we take to"], "correct": "taking",
     "note": "После suggest используется герундий."},
    {"id": "b1_05", "level": "B1", "skill": "structure",
     "prompt": "That's the man ___ car was stolen.",
     "choices": ["who", "which", "whose", "whom"], "correct": "whose",
     "note": "Принадлежность в придаточном — whose."},
    {"id": "b1_06", "level": "B1", "skill": "tense",
     "prompt": "By the time we arrived, the concert ___ .",
     "choices": ["started", "has started", "had started", "was starting"],
     "correct": "had started",
     "note": "Действие раньше другого прошедшего — Past Perfect."},
    {"id": "b1_07", "level": "B1", "skill": "preposition",
     "prompt": "I'm looking forward ___ you again.",
     "choices": ["to see", "to seeing", "seeing", "for seeing"],
     "correct": "to seeing",
     "note": "В look forward to слово to — предлог, после него герундий."},
    {"id": "b1_08", "level": "B1", "skill": "word_choice",
     "prompt": "He's used ___ early — he's done it for years.",
     "choices": ["to get up", "to getting up", "get up", "getting up"],
     "correct": "to getting up",
     "note": "Be used to — привычка, после to идёт герундий."},
    {"id": "b1_09", "level": "B1", "skill": "structure",
     "prompt": "If I ___ more time, I would learn another language.",
     "choices": ["have", "had", "will have", "would have"], "correct": "had",
     "note": "Второй тип условия: if + Past Simple, would в главной части."},
    {"id": "b1_10", "level": "B1", "skill": "word_choice",
     "prompt": "The meeting was ___ until next Tuesday.",
     "choices": ["put off", "put on", "put up", "put down"], "correct": "put off",
     "note": "Put off — отложить на более поздний срок."},

    # ─── B2 ──────────────────────────────────────────────────────────
    {"id": "b2_01", "level": "B2", "skill": "structure",
     "prompt": "___ harder, she would have passed the exam.",
     "choices": ["If she studied", "Had she studied", "She had studied", "If she has studied"],
     "correct": "Had she studied",
     "note": "Инверсия вместо if в третьем типе условия — Had she studied."},
    {"id": "b2_02", "level": "B2", "skill": "tense",
     "prompt": "By next June I ___ here for ten years.",
     "choices": ["will work", "will be working", "will have been working", "have worked"],
     "correct": "will have been working",
     "note": "Длительность к моменту в будущем — Future Perfect Continuous."},
    {"id": "b2_03", "level": "B2", "skill": "word_choice",
     "prompt": "I'd rather you ___ tell anyone about this.",
     "choices": ["don't", "didn't", "wouldn't", "won't"], "correct": "didn't",
     "note": "После would rather + другое лицо используется Past Simple."},
    {"id": "b2_04", "level": "B2", "skill": "structure",
     "prompt": "It's high time we ___ about moving to a bigger office.",
     "choices": ["think", "thought", "will think", "have thought"], "correct": "thought",
     "note": "It's high time требует форму прошедшего времени."},
    {"id": "b2_05", "level": "B2", "skill": "word_choice",
     "prompt": "She must ___ the train — she left an hour ago.",
     "choices": ["catch", "have caught", "caught", "be catching"],
     "correct": "have caught",
     "note": "Уверенное предположение о прошлом — must have + причастие."},
    {"id": "b2_06", "level": "B2", "skill": "structure",
     "prompt": "No sooner ___ the door than the phone rang.",
     "choices": ["I had closed", "had I closed", "I closed", "did I close"],
     "correct": "had I closed",
     "note": "После no sooner идёт инверсия: had + подлежащее."},
    {"id": "b2_07", "level": "B2", "skill": "word_choice",
     "prompt": "The new policy will ___ into effect next month.",
     "choices": ["come", "get", "make", "take"], "correct": "come",
     "note": "Устойчивое сочетание come into effect — вступать в силу."},
    {"id": "b2_08", "level": "B2", "skill": "tense",
     "prompt": "He denied ___ anything about the missing files.",
     "choices": ["to know", "know", "knowing", "have known"], "correct": "knowing",
     "note": "После deny используется герундий."},
    {"id": "b2_09", "level": "B2", "skill": "structure",
     "prompt": "The proposal, ___ was submitted late, was still accepted.",
     "choices": ["that", "which", "what", "who"], "correct": "which",
     "note": "В непрерывном придаточном с запятыми that не используется."},
    {"id": "b2_10", "level": "B2", "skill": "word_choice",
     "prompt": "Her argument was ___ convincing that nobody objected.",
     "choices": ["so", "such", "very", "too"], "correct": "so",
     "note": "So + прилагательное + that. Such идёт с существительным."},

    # ─── C1 ──────────────────────────────────────────────────────────
    {"id": "c1_01", "level": "C1", "skill": "structure",
     "prompt": "___ for the delay, the project would have finished on schedule.",
     "choices": ["If not", "Were it not", "Had it not been", "Unless it was"],
     "correct": "Had it not been",
     "note": "Инверсия в третьем типе условия: Had it not been for."},
    {"id": "c1_02", "level": "C1", "skill": "word_choice",
     "prompt": "The evidence lends ___ to his version of events.",
     "choices": ["weight", "power", "force", "strength"], "correct": "weight",
     "note": "Устойчивое сочетание lend weight to — придавать вес."},
    {"id": "c1_03", "level": "C1", "skill": "structure",
     "prompt": "Rarely ___ such a well-argued proposal.",
     "choices": ["I have seen", "have I seen", "I saw", "did I saw"],
     "correct": "have I seen",
     "note": "Наречие rarely в начале предложения требует инверсии."},
    {"id": "c1_04", "level": "C1", "skill": "word_choice",
     "prompt": "The two accounts of the incident are at ___ with each other.",
     "choices": ["odds", "ends", "stake", "large"], "correct": "odds",
     "note": "Be at odds with — противоречить друг другу."},
    {"id": "c1_05", "level": "C1", "skill": "tense",
     "prompt": "Little ___ that the decision would change everything.",
     "choices": ["she knew", "did she know", "she did know", "knew she"],
     "correct": "did she know",
     "note": "Отрицательное little в начале даёт инверсию с did."},
    {"id": "c1_06", "level": "C1", "skill": "structure",
     "prompt": "The committee insisted that the report ___ by Friday.",
     "choices": ["is submitted", "be submitted", "will be submitted", "was submitted"],
     "correct": "be submitted",
     "note": "После insist that используется сослагательное: голый инфинитив."},
    {"id": "c1_07", "level": "C1", "skill": "word_choice",
     "prompt": "His remarks were, ___ , entirely beside the point.",
     "choices": ["by and large", "on the whole hand", "at large", "in the large"],
     "correct": "by and large",
     "note": "By and large — в целом, по большому счёту."},
    {"id": "c1_08", "level": "C1", "skill": "structure",
     "prompt": "___ as it may seem, the simplest option was the best.",
     "choices": ["Strange", "Strangely", "However strange", "As strange"],
     "correct": "Strange",
     "note": "Уступительная конструкция: прилагательное + as + подлежащее + may."},
    {"id": "c1_09", "level": "C1", "skill": "word_choice",
     "prompt": "She managed to ___ the issue without offending anyone.",
     "choices": ["broach", "breach", "brooch", "breech"], "correct": "broach",
     "note": "Broach a subject — поднять тему. Остальные — другие слова."},
    {"id": "c1_10", "level": "C1", "skill": "tense",
     "prompt": "He's said to ___ in Berlin before the war.",
     "choices": ["live", "have lived", "be living", "living"],
     "correct": "have lived",
     "note": "Действие раньше момента речи — perfect infinitive have lived."},
]

BY_LEVEL: dict[str, list[dict]] = {
    lv: [q for q in BANK if q["level"] == lv] for lv in LEVELS
}
BY_ID: dict[str, dict] = {q["id"]: q for q in BANK}

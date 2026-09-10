-- Миграция 0038: подготовка к ЕГЭ/ОГЭ (этап 2 — банк заданий и модерация).
--
-- Что хранится.
--   exam_specs    — структура экзамена как данные (JSON): номера заданий,
--                   типы, баллы, правила скоринга, таймеры, лимиты слов.
--                   Одна активная строка на экзамен и год. Меняется без релиза.
--   exam_tasks    — банк заданий. Одна строка = одна группа КИМ (например,
--                   19–24 целиком: тексты + 6 пропусков). content — задание
--                   без ответов (отдаётся клиенту), answer_key — ключ,
--                   explanation — разбор по элементам. status: draft →
--                   review → published → retired. quality — авто-оценка 0–100
--                   по «слепому» решению второй моделью; gen_meta — модель,
--                   версия промпта, замечания автопроверки.
--   exam_attempts — попытка ученика (по номерам / раздел / вариант).
--   exam_answers  — ответ на элемент задания с баллом и разбором.
--   exam_progress — агрегат по пользователю и номеру задания для карты
--                   «слабые места».
-- Попытки, ответы и прогресс заводятся сейчас, чтобы этап 3 (экраны) не
-- требовал ещё одной миграции на тесте.
--
-- Применять:
--   mysql -u <user> -p <db> < 0038_exam_prep.sql
-- Идемпотентно.

CREATE TABLE IF NOT EXISTS exam_specs (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    exam        VARCHAR(8)   NOT NULL,          -- ege | oge
    year        SMALLINT     NOT NULL,
    is_active   TINYINT(1)   NOT NULL DEFAULT 1,
    spec        JSON         NOT NULL,
    scale       JSON         NULL,              -- первичный → тестовый / оценка
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_exam_specs_exam_year (exam, year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS exam_tasks (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    exam         VARCHAR(8)      NOT NULL,
    task_no      VARCHAR(8)      NOT NULL,      -- '19-24', '25-29', '30-36', '37', ...
    task_type    VARCHAR(24)     NOT NULL,      -- gram_form | word_form | lex_mcq | email | ...
    level_hint   VARCHAR(4)      NULL,          -- B1 / B2
    topic        VARCHAR(120)    NULL,
    content      JSON            NOT NULL,
    answer_key   JSON            NOT NULL,
    explanation  JSON            NULL,
    status       VARCHAR(12)     NOT NULL DEFAULT 'draft',  -- draft|review|published|retired
    source       VARCHAR(12)     NOT NULL DEFAULT 'llm',    -- llm|manual
    quality      TINYINT UNSIGNED NULL,          -- 0..100
    gen_meta     JSON            NULL,
    created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at  DATETIME        NULL,
    reviewed_by  VARCHAR(64)     NULL,
    times_used   INT UNSIGNED    NOT NULL DEFAULT 0,
    avg_score    FLOAT           NULL,           -- средний % верных элементов
    PRIMARY KEY (id),
    KEY idx_exam_tasks_pick (exam, task_no, status),
    KEY idx_exam_tasks_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS exam_attempts (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id       BIGINT UNSIGNED NOT NULL,
    exam          VARCHAR(8)      NOT NULL,
    mode          VARCHAR(12)     NOT NULL,      -- drill | section | variant
    task_no       VARCHAR(8)      NULL,          -- для drill
    task_ids      JSON            NOT NULL,
    started_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at   DATETIME        NULL,
    duration_sec  INT UNSIGNED    NOT NULL DEFAULT 0,
    primary_score INT             NULL,
    max_score     INT             NULL,
    breakdown     JSON            NULL,
    PRIMARY KEY (id),
    KEY idx_exam_attempts_user (user_id, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS exam_answers (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    attempt_id  BIGINT UNSIGNED NOT NULL,
    task_id     BIGINT UNSIGNED NOT NULL,
    item_key    VARCHAR(8)      NOT NULL,        -- номер элемента ('19') или 'all'
    user_answer JSON            NULL,
    score       INT             NOT NULL DEFAULT 0,
    max_score   INT             NOT NULL DEFAULT 1,
    feedback    JSON            NULL,
    checked_by  VARCHAR(8)      NOT NULL DEFAULT 'auto',  -- auto | llm
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_exam_answers_attempt (attempt_id),
    KEY idx_exam_answers_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS exam_progress (
    user_id   BIGINT UNSIGNED NOT NULL,
    exam      VARCHAR(8)      NOT NULL,
    task_no   VARCHAR(8)      NOT NULL,
    attempts  INT UNSIGNED    NOT NULL DEFAULT 0,
    items     INT UNSIGNED    NOT NULL DEFAULT 0,
    correct   INT UNSIGNED    NOT NULL DEFAULT 0,
    last_at   DATETIME        NULL,
    PRIMARY KEY (user_id, exam, task_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Спецификация ЕГЭ-2027 (ФИПИ, проект от 18.08.2026). Источник правды —
-- docs/exam/ege_2027.spec.json; здесь та же структура для первичной загрузки.
INSERT IGNORE INTO exam_specs (exam, year, is_active, spec)
VALUES ('ege', 2027, 1, '{"exam":"ege","year":2027,"subject":"english","source":"ФИПИ, проект от 18.08.2026; изменений относительно 2026 нет","written":{"minutes":190,"max":62,"sections":{"listening":{"max":12,"minutes":30},"reading":{"max":12,"minutes":30},"grammar_vocabulary":{"max":18,"minutes":40},"writing":{"max":20,"minutes":90}},"tasks":[{"no":1,"section":"listening","type":"matching","level":"B","max":2,"elements":6,"scoring":"max_minus_wrong","minutes":8,"codes":["1.2.1"],"shape":{"speakers":6,"statements":7,"extra":1,"plays":2}},{"no":2,"section":"listening","type":"tfn","level":"B","max":3,"elements":7,"scoring":"max_minus_wrong","minutes":8,"codes":["1.2.2"],"shape":{"statements":7,"labels":{"1":"True","2":"False","3":"Not stated"},"plays":2}},{"no":"3-9","nos":[3,4,5,6,7,8,9],"section":"listening","type":"mcq3","level":"В","max":1,"per_item":true,"minutes":2,"codes":["1.2.3"],"shape":{"questions":7,"options":3,"plays":2,"genre":"interview"}},{"no":10,"section":"reading","type":"headings","level":"B","max":3,"elements":7,"scoring":"max_minus_wrong","minutes":8,"codes":["1.3.1"],"shape":{"texts":7,"headings":8,"extra":1}},{"no":11,"section":"reading","type":"gapped","level":"B","max":2,"elements":6,"scoring":"max_minus_wrong","minutes":8,"codes":["1.3.2"],"shape":{"gaps":6,"fragments":7,"extra":1}},{"no":"12-18","nos":[12,13,14,15,16,17,18],"section":"reading","type":"mcq4","level":"В","max":1,"per_item":true,"minutes":2,"codes":["1.3.3"],"shape":{"questions":7,"options":4}},{"no":"19-24","nos":[19,20,21,22,23,24],"section":"grammar","type":"gram_form","level":"B","max":1,"per_item":true,"minutes":2,"codes":["2.4.12","2.4.14","2.4.15","2.4.17","2.4.18","2.4.19","2.4.20","2.4.21","2.4.22","2.4.24","2.4.25","2.4.26","2.4.27","2.4.29","2.4.30","2.4.31","2.4.33","2.4.35","2.4.36","2.4.37"],"shape":{"gaps":6,"texts":"1-2"},"answer_format":"word_nospace"},{"no":"25-29","nos":[25,26,27,28,29],"section":"vocabulary","type":"word_form","level":"B","max":1,"per_item":true,"minutes":2,"codes":["2.3.11"],"shape":{"gaps":5,"texts":1},"answer_format":"word_nospace"},{"no":"30-36","nos":[30,31,32,33,34,35,36],"section":"vocabulary","type":"lex_mcq","level":"В","max":1,"per_item":true,"minutes":2,"codes":["2.3.1","2.3.2","2.3.3","2.3.4","2.3.5"],"shape":{"gaps":7,"options":4,"texts":1}},{"no":37,"section":"writing","type":"email","level":"B","max":6,"minutes":30,"codes":["1.4.3"],"words":{"min":100,"max":140,"count_first":140,"zero_below":90},"criteria":[{"key":"K1","name":"Решение коммуникативной задачи","max":2,"zero_kills":true},{"key":"K2","name":"Организация текста","max":2},{"key":"K3","name":"Языковое оформление текста","max":2}],"aspects":["ответ на вопрос 1","ответ на вопрос 2","ответ на вопрос 3","3 вопроса по указанной теме","нормы вежливости (благодарность / надежда на контакт)","стилевое оформление: обращение, завершающая фраза, подпись"]},{"no":38,"section":"writing","type":"project","level":"В","max":14,"minutes":60,"codes":["1.4.9"],"variants":["38.1 table","38.2 pie_chart"],"words":{"min":200,"max":250,"count_first":250,"zero_below":180},"criteria":[{"key":"K1","name":"Решение коммуникативной задачи","max":3,"zero_kills":true},{"key":"K2","name":"Организация текста","max":3},{"key":"K3","name":"Лексика","max":3},{"key":"K4","name":"Грамматика","max":3},{"key":"K5","name":"Орфография и пунктуация","max":2}],"plan":["вступление по теме проекта","2–3 факта из данных","1–2 сравнения с комментарием","возможная проблема и способ решения","вывод с собственным мнением"],"nonproductive_limit_pct":30}]},"oral":{"minutes":17,"max":20,"tasks":[{"no":1,"type":"read_aloud","level":"B","max":1,"prep_sec":90,"answer_sec":90,"codes":["2.1.2"],"rule":"≤5 фонетических ошибок, из них ≤2 искажающих смысл"},{"no":2,"type":"ask_questions","level":"B","max":4,"prep_sec":90,"answer_sec_each":20,"items":4,"codes":["1.1.1.3"],"rule":"по 1 баллу: вопрос по содержанию и в правильной форме прямого вопроса"},{"no":3,"type":"interview","level":"В","max":5,"prep_sec":0,"answer_sec_each":40,"items":5,"codes":["1.1.1.5"],"rule":"по 1 баллу: полный точный ответ из 2–3 фраз без элементарных ошибок; 1 фраза = 0"},{"no":4,"type":"monologue_photos","level":"В","max":10,"prep_sec":150,"answer_sec":180,"phrases":{"min":12,"max":15,"zero_at_or_below":7},"codes":["1.1.2.3"],"criteria":[{"key":"K1","name":"Решение коммуникативной задачи","max":4,"zero_kills":true},{"key":"K2","name":"Организация высказывания","max":3},{"key":"K3","name":"Языковое оформление высказывания","max":3}],"aspects":["выбор иллюстраций: краткое описание фото и различия, связь с темой","достоинства (1–2) двух вариантов","недостатки (1–2) двух вариантов","мнение автора и обоснование"]}]},"max_primary":82,"word_count_rules":["считаются все слова с первого по последнее, включая обращение и подпись","стяжённые формы (can''t, I''m) — одно слово","числительные цифрами, в т.ч. с % — одно слово","числительные словами — по словам","сложные слова через дефис — одно слово","сокращения (USA, e-mail, TV) — одно слово"],"scale_primary_to_test":null}');


-- ─── schema_version = 38 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (38)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

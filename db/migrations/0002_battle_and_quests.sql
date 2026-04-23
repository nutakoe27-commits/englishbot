-- Миграция 0002: Battle Mode + Daily Quest.
-- Применять ПОСЛЕ 0001_init.sql. Идемпотентна (IF NOT EXISTS).
--
-- Как применять:
--   mysql -u megotim4y2 -p megotim4y2 < 0002_battle_and_quests.sql
--
-- Что добавляет:
--   1. battles            — таблица дуэлей (inline-вызов → принятие → запись → судейство)
--   2. quests_catalog     — статический каталог квестов (лексика/грамматика/роль)
--   3. user_quests        — связь юзер↔квест с прогрессом
--   4. В users: колонка quest_reward_seconds (нагренадный бонус к дневному лимиту)
--   5. В daily_usage: колонка bonus_seconds (подарок от квеста на сегодня)


-- ─── battles ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS battles (
    id                   BIGINT NOT NULL AUTO_INCREMENT,

    initiator_tg_id      BIGINT NOT NULL COMMENT 'Кто бросил вызов',
    opponent_tg_id       BIGINT NULL     COMMENT 'Кто принял (NULL = открыт)',

    chat_id              BIGINT NOT NULL COMMENT 'Чат, в который постим результат',
    chat_message_id      BIGINT NULL     COMMENT 'id сообщения-вызова для edit',

    topic_key            VARCHAR(64) NOT NULL COMMENT 'ключ из каталога тем в коде',

    status               ENUM('open', 'accepted', 'recording',
                              'judged', 'expired', 'canceled')
                         NOT NULL DEFAULT 'open',

    -- Записи обоих участников (путь к аудиофайлу на бэкенде + транскрипт)
    a_audio_path         VARCHAR(500) NULL,
    b_audio_path         VARCHAR(500) NULL,
    a_transcript         TEXT NULL,
    b_transcript         TEXT NULL,

    -- Скоры судейства (JSON: {"grammar": 7, "fluency": 8, "argumentation": 6})
    a_score              JSON NULL,
    b_score              JSON NULL,

    winner               ENUM('a', 'b', 'tie') NULL,
    judge_comment        TEXT NULL COMMENT 'Одна-две строки шутливого резюме от ИИ',

    created_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                              ON UPDATE CURRENT_TIMESTAMP,
    expires_at           DATETIME NOT NULL COMMENT '+24ч от created_at для open',

    PRIMARY KEY (id),
    KEY idx_battles_status_expires    (status, expires_at),
    KEY idx_battles_initiator_created (initiator_tg_id, created_at),
    KEY idx_battles_opponent          (opponent_tg_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─── quests_catalog ──────────────────────────────────────────────────────
-- Статический каталог. Наливается INSERT IGNORE ниже + docker/seed при деплое.
-- Бот и backend читают только отсюда.
CREATE TABLE IF NOT EXISTS quests_catalog (
    `key`               VARCHAR(64) NOT NULL COMMENT 'Напр. lex_serendipity_3x',

    type                ENUM('lexical', 'grammar', 'role') NOT NULL,
    difficulty          ENUM('easy', 'medium', 'hard') NOT NULL DEFAULT 'medium',
    target_level        ENUM('A2', 'B1', 'B2', 'C1', 'any') NOT NULL DEFAULT 'any',

    title_ru            VARCHAR(200) NOT NULL,
    description_ru      VARCHAR(500) NOT NULL,

    -- Правило проверки в JSON. Обрабатывается в backend/app/quests.py:
    --   lexical: {"kind": "word_count", "word": "serendipity", "min": 3}
    --   grammar: {"kind": "grammar_pattern", "pattern": "past_perfect", "min": 5}
    --   role:    {"kind": "role_time", "role": "barista", "min_seconds": 300}
    verification_rule   JSON NOT NULL,

    reward_seconds      INT NOT NULL DEFAULT 1800 COMMENT '+30 мин по умолчанию',
    badge_key           VARCHAR(64) NULL COMMENT 'Ключ значка, None если только минуты',

    is_active           TINYINT(1) NOT NULL DEFAULT 1,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`key`),
    KEY idx_quests_active_level (is_active, target_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─── user_quests ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_quests (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    user_id             BIGINT NOT NULL,
    quest_key           VARCHAR(64) NOT NULL,

    assigned_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at        DATETIME NULL,
    expired_at          DATETIME NULL COMMENT 'Если день прошёл — помечаем',

    -- Отладочные данные от проверки (что нашли в транскрипте)
    verification_data   JSON NULL,

    PRIMARY KEY (id),
    UNIQUE KEY uk_user_quest (user_id, quest_key),
    KEY idx_user_active (user_id, completed_at, expired_at),
    CONSTRAINT fk_user_quests_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_user_quests_quest FOREIGN KEY (quest_key)
        REFERENCES quests_catalog(`key`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─── daily_usage: бонус от квеста ────────────────────────────────────────
-- Не трогаем used_seconds, но даём «крышу» выше обычного лимита.
-- Логика лимитов в backend/app/limits.py станет: free_seconds + bonus_seconds.
ALTER TABLE daily_usage
    ADD COLUMN IF NOT EXISTS bonus_seconds INT NOT NULL DEFAULT 0
        COMMENT 'Бонус от выполненного квеста (сбрасывается вместе с usage_date)';


-- ─── каталог квестов: 20 штук для MVP ────────────────────────────────────
-- Лексика (8 квестов) — словарь на разных уровнях
INSERT IGNORE INTO quests_catalog
    (`key`, type, difficulty, target_level, title_ru, description_ru,
     verification_rule, reward_seconds, badge_key) VALUES

('lex_awkward_3x', 'lexical', 'easy', 'A2',
 'Слово дня: awkward',
 'Используй слово "awkward" в разговоре с ИИ 3 раза. Это значит "неловкий, неудобный" — отличное слово для описания ситуаций.',
 '{"kind":"word_count","word":"awkward","min":3}', 1800, 'word_rookie'),

('lex_actually_5x', 'lexical', 'easy', 'A2',
 'Слово дня: actually',
 'Используй "actually" 5 раз. Это одно из самых частотных слов носителей — значит "на самом деле, вообще-то".',
 '{"kind":"word_count","word":"actually","min":5}', 1800, 'word_rookie'),

('lex_overwhelmed_3x', 'lexical', 'medium', 'B1',
 'Слово дня: overwhelmed',
 'Используй "overwhelmed" 3 раза. Значит "перегруженный, ошарашенный" — пригодится для разговоров про работу и стресс.',
 '{"kind":"word_count","word":"overwhelmed","min":3}', 1800, 'word_collector'),

('lex_honestly_4x', 'lexical', 'medium', 'B1',
 'Слово дня: honestly',
 'Используй "honestly" 4 раза. Натуральный filler, который делает речь живее.',
 '{"kind":"word_count","word":"honestly","min":4}', 1800, 'word_collector'),

('lex_serendipity_3x', 'lexical', 'hard', 'B2',
 'Слово ниндзя: serendipity',
 'Незаметно используй "serendipity" в разговоре 3 раза, да так, чтобы ИИ не почуял, что это специально. Значит "счастливое совпадение".',
 '{"kind":"word_count","word":"serendipity","min":3}', 1800, 'word_ninja'),

('lex_apparently_3x', 'lexical', 'medium', 'B1',
 'Слово дня: apparently',
 'Используй "apparently" 3 раза — "по-видимому, судя по всему". Очень по-английски звучит.',
 '{"kind":"word_count","word":"apparently","min":3}', 1800, 'word_collector'),

('lex_unequivocally_2x', 'lexical', 'hard', 'C1',
 'Слово мастера: unequivocally',
 'Используй "unequivocally" 2 раза — "безоговорочно, однозначно". Академическое слово, звучит солидно.',
 '{"kind":"word_count","word":"unequivocally","min":2}', 1800, 'word_master'),

('lex_basically_5x', 'lexical', 'easy', 'A2',
 'Слово дня: basically',
 'Используй "basically" 5 раз. Один из самых частых filler-ов в разговорной речи.',
 '{"kind":"word_count","word":"basically","min":5}', 1800, 'word_rookie'),


-- Грамматика (6 квестов) — структуры
('gram_past_perfect_5x', 'grammar', 'hard', 'B2',
 'Past Perfect × 5',
 'Построй 5 предложений в Past Perfect (had + V3). Напр.: "I had already left when she arrived."',
 '{"kind":"grammar_pattern","pattern":"past_perfect","min":5}', 1800, 'grammar_nerd'),

('gram_conditionals_3x', 'grammar', 'medium', 'B1',
 'Условные × 3',
 'Используй 3 условных предложения (If I were…, If I had…). Разговор про «что было бы, если».',
 '{"kind":"grammar_pattern","pattern":"conditionals","min":3}', 1800, 'grammar_nerd'),

('gram_phrasal_verbs_5', 'grammar', 'medium', 'B1',
 'Фразовые глаголы × 5',
 'Используй 5 разных фразовых глаголов (look up, give up, run into, и т.д.). Без них английский звучит неестественно.',
 '{"kind":"phrasal_verbs","min":5}', 1800, 'grammar_nerd'),

('gram_questions_7x', 'grammar', 'easy', 'A2',
 '7 вопросов собеседнику',
 'Задай ИИ 7 вопросов за сессию. В реальном диалоге вопросы двигают разговор — не давай ему зависать.',
 '{"kind":"question_count","min":7}', 1800, 'chatterbox'),

('gram_used_to_3x', 'grammar', 'medium', 'B1',
 'Used to × 3',
 'Используй конструкцию "used to" 3 раза — для привычек в прошлом. "I used to live in…"',
 '{"kind":"grammar_pattern","pattern":"used_to","min":3}', 1800, 'grammar_nerd'),

('gram_present_perfect_5x', 'grammar', 'medium', 'B1',
 'Present Perfect × 5',
 'Построй 5 предложений в Present Perfect (have/has + V3). Самое сложное время для русскоговорящих.',
 '{"kind":"grammar_pattern","pattern":"present_perfect","min":5}', 1800, 'grammar_nerd'),


-- Роли (6 квестов) — провести минимум N минут в роли
('role_barista_5min', 'role', 'easy', 'any',
 'Утро в кофейне',
 'Поговори с ИИ в роли "Бариста" минимум 5 минут. Закажи, поговори о погоде, спроси совета.',
 '{"kind":"role_time","role":"barista","min_seconds":300}', 1800, 'role_player'),

('role_interview_7min', 'role', 'hard', 'B1',
 'Собеседование',
 'Пройди "интервью" с ИИ-интервьюером минимум 7 минут. Расскажи о себе, опыте, отвечай на каверзные вопросы.',
 '{"kind":"role_time","role":"interviewer","min_seconds":420}', 2700, 'role_player'),

('role_travel_5min', 'role', 'medium', 'B1',
 'Планируем путешествие',
 'Запланируй поездку с ИИ-турагентом. Минимум 5 минут диалога: куда, когда, бюджет, впечатления.',
 '{"kind":"role_time","role":"travel_agent","min_seconds":300}', 1800, 'role_player'),

('role_doctor_5min', 'role', 'medium', 'B1',
 'На приёме у врача',
 'Поговори с ИИ-врачом 5 минут. Опиши симптомы, ответь на вопросы. Полезно для отпускных ситуаций.',
 '{"kind":"role_time","role":"doctor","min_seconds":300}', 1800, 'role_player'),

('role_friend_10min', 'role', 'easy', 'any',
 'Болтовня с другом',
 'Поболтай с ИИ в роли "Друг" 10 минут. Расскажи, как прошёл день, послушай его истории.',
 '{"kind":"role_time","role":"friend","min_seconds":600}', 1800, 'role_player'),

('role_shopkeeper_5min', 'role', 'easy', 'A2',
 'За покупками',
 'В магазине одежды: найди размер, обсуди стиль, поторгуйся. 5 минут в роли "Продавец".',
 '{"kind":"role_time","role":"shopkeeper","min_seconds":300}', 1800, 'role_player');


-- ─── schema_version ──────────────────────────────────────────────────────
INSERT IGNORE INTO schema_version (version) VALUES (2);

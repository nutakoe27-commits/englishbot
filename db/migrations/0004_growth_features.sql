-- Миграция 0004: основа для «English Tutor 2.0» (streak + persistent learner state).
--
-- Что добавляет:
--   1. users.streak_days, users.best_streak_days, users.last_practice_date —
--      ежедневный стрик практики, инкрементится в backend voice.py finally.
--   2. users.learning_goal — цель изучения, устанавливается в onboarding.
--   3. user_vocabulary — слова/фразы, которые тьютор вводил в разговор.
--      Источник истины для подсказок «давай реюзнем выученное» в system_prompt.
--   4. user_mistakes — повторяющиеся ошибки (категория + bad/good формы).
--      Источник для «patterns to gently reinforce» в system_prompt.
--
-- Как применять:
--   mysql -u <user> -p <db> < 0004_growth_features.sql
--
-- Идемпотентно: каждое ALTER/CREATE проверяет, существует ли объект.

-- ─── 1. users: новые колонки ───────────────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND COLUMN_NAME = 'streak_days'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE users ADD COLUMN streak_days INT NOT NULL DEFAULT 0 COMMENT ''Текущий стрик дней практики подряд''',
    'SELECT ''streak_days already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND COLUMN_NAME = 'best_streak_days'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE users ADD COLUMN best_streak_days INT NOT NULL DEFAULT 0 COMMENT ''Лучший стрик за всё время''',
    'SELECT ''best_streak_days already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND COLUMN_NAME = 'last_practice_date'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE users ADD COLUMN last_practice_date DATE NULL COMMENT ''Последний день (МСК), когда юзер практиковался''',
    'SELECT ''last_practice_date already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND COLUMN_NAME = 'learning_goal'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE users ADD COLUMN learning_goal VARCHAR(32) NULL COMMENT ''travel|work|daily|exam|fun (NULL до onboarding)''',
    'SELECT ''learning_goal already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ─── 2. user_vocabulary ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_vocabulary (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    user_id         BIGINT NOT NULL,
    word            VARCHAR(64) NOT NULL,
    first_seen_at   DATETIME NOT NULL,
    last_seen_at    DATETIME NOT NULL,
    times_used      INT NOT NULL DEFAULT 1,
    context         VARCHAR(255) NULL COMMENT 'Реплика, где впервые встретилось',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_user_word (user_id, word),
    KEY idx_user_recent (user_id, last_seen_at DESC),
    CONSTRAINT fk_uv_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Слова и фразы, которые тьютор вводил в разговор; для reinforce в следующих сессиях';


-- ─── 3. user_mistakes ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_mistakes (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    user_id         BIGINT NOT NULL,
    category        VARCHAR(32) NOT NULL COMMENT 'article|tense|preposition|word_choice|phrasal|other',
    bad_phrase      VARCHAR(255) NOT NULL,
    good_phrase     VARCHAR(255) NOT NULL,
    occurred_at     DATETIME NOT NULL,
    PRIMARY KEY (id),
    KEY idx_user_recent (user_id, occurred_at DESC),
    CONSTRAINT fk_um_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Лог повторяющихся ошибок учащегося для гентл-reinforce в системном промпте';


-- ─── 4. schema_version (если есть) ─────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (4)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

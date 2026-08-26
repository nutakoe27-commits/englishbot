-- Миграция 0036: аналитика теста уровня.
--
-- 1) level_tests.source — откуда прохождение: 'app' (тест внутри мини-аппа)
--    или 'landing' (публичная страница /level). Раньше источник был один,
--    теперь их два, и смешивать их в статистике нельзя: у них разный состав
--    заданий и разная аудитория.
--
-- 2) level_test_leads — воронка лендинга. Анонимное прохождение не создаёт
--    юзера и до регистрации нигде не оставляет следа, поэтому самый важный
--    для лендинга вопрос — «сколько начали, сколько дошли до конца, сколько
--    зарегистрировались» — по level_tests не считается в принципе.
--    Строка заводится на старте теста и дополняется на финише и на claim.
--
--    test_id UNIQUE: обновляем строку по нему, а не по id, и повторный
--    claim ничего не ломает.
--
-- 3) schema_version = 36.
--
-- Применять:
--   mysql -u <user> -p <db> < 0036_level_test_leads.sql
-- Идемпотентно.

SET @has := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'level_tests'
      AND COLUMN_NAME = 'source'
);
SET @ddl := IF(@has = 0,
    'ALTER TABLE level_tests ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT ''app''',
    'SELECT ''level_tests.source exists'' AS msg');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

CREATE TABLE IF NOT EXISTS level_test_leads (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    test_id         VARCHAR(32)     NOT NULL,
    started_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at     DATETIME        NULL,
    cefr            VARCHAR(4)      NULL,
    correct_cnt     INT             NULL,
    total_cnt       INT             NULL,
    claimed_user_id BIGINT UNSIGNED NULL,
    claimed_at      DATETIME        NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ltl_test (test_id),
    KEY idx_ltl_started (started_at),
    KEY idx_ltl_claimed (claimed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─── schema_version = 36 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl2 := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (36)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl2; EXECUTE stmt; DEALLOCATE PREPARE stmt;

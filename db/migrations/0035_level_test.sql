-- Миграция 0035: тест уровня английского (CEFR A1–C1).
--
-- 1) users.cefr_level — результат последнего теста. Его подхватывают все
--    режимы (разговор, подкасты, грамматика), поэтому поле на юзере, а не
--    только в истории тестов.
-- 2) level_tests — история прохождений: по ней считается прогресс
--    («в августе B1, в декабре B2») и строится AI-разбор.
--    answers хранит id заданий и верность ответа — этого достаточно,
--    чтобы восстановить прохождение, тексты лежат в коде (level_test_bank).
-- 3) schema_version = 35.
--
-- Применять:
--   mysql -u <user> -p <db> < 0035_level_test.sql
-- Идемпотентно.

SET @has := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'
      AND COLUMN_NAME = 'cefr_level'
);
SET @ddl := IF(@has = 0,
    'ALTER TABLE users ADD COLUMN cefr_level VARCHAR(4) NULL, ADD COLUMN cefr_tested_at DATETIME NULL',
    'SELECT ''users.cefr_level exists'' AS msg');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

CREATE TABLE IF NOT EXISTS level_tests (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id     BIGINT UNSIGNED NOT NULL,
    cefr        VARCHAR(4)      NOT NULL,
    correct_cnt INT             NOT NULL DEFAULT 0,
    total_cnt   INT             NOT NULL DEFAULT 0,
    answers     JSON            NULL,
    report      TEXT            NULL,
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_lt_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─── schema_version = 35 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl2 := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (35)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl2; EXECUTE stmt; DEALLOCATE PREPARE stmt;

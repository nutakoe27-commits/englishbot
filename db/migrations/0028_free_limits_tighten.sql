-- Миграция 0028: ужесточение бесплатных дневных лимитов.
--
-- Было: говорение 900с (15 мин), слушание 2/день, грамматика 3/день.
-- Стало: говорение 300с (5 мин), слушание 1/день, грамматика 1/день.
--
-- Источник истины — settings_kv (читается backend через get_kv_int /
-- SECTION_LIMITS). Ключи существуют — делаем явный UPDATE; INSERT IGNORE —
-- страховка, если ключа вдруг нет.
--
-- Применять:
--   mysql -u <user> -p <db> < 0028_free_limits_tighten.sql
-- Идемпотентно.

UPDATE settings_kv SET value = '300' WHERE `key` = 'free_seconds_per_day';
UPDATE settings_kv SET value = '1'   WHERE `key` = 'free_listening_per_day';
UPDATE settings_kv SET value = '1'   WHERE `key` = 'free_grammar_per_day';

INSERT IGNORE INTO settings_kv (`key`, value) VALUES
    ('free_seconds_per_day',   '300'),
    ('free_listening_per_day', '1'),
    ('free_grammar_per_day',   '1');


-- ─── schema_version = 28 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (28)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Миграция 0019: увеличение бесплатных дневных лимитов
--
-- Было: говорение 600с (10 мин), слушание 1/день, грамматика 1/день.
-- Стало: говорение 1200с (20 мин), слушание 2/день, грамматика 3/день.
--
-- Источник истины — settings_kv (их читает backend через get_kv_int).
-- Ключи уже существуют (0001 / 0016), поэтому INSERT IGNORE их не обновит —
-- делаем явный UPDATE. INSERT IGNORE ниже — страховка, если ключа вдруг нет.
--
-- Примечание: эти настройки задаются только миграциями (в админке не
-- редактируются), поэтому перезапись безопасна.
--
-- Применять:
--   mysql -u <user> -p <db> < 0019_increase_free_limits.sql
--
-- Идемпотентно.

UPDATE settings_kv SET value = '1200' WHERE `key` = 'free_seconds_per_day';
UPDATE settings_kv SET value = '2'    WHERE `key` = 'free_listening_per_day';
UPDATE settings_kv SET value = '3'    WHERE `key` = 'free_grammar_per_day';

INSERT IGNORE INTO settings_kv (`key`, value) VALUES
    ('free_seconds_per_day',   '1200'),
    ('free_listening_per_day', '2'),
    ('free_grammar_per_day',   '3');


-- ─── schema_version = 19 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (19)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Миграция 0026: сократить бесплатное говорение 20 → 15 минут в день.
--
-- Было: free_seconds_per_day = 1200 (20 мин, миграция 0019).
-- Стало: 900 (15 мин). Слушание/грамматика не трогаем.
--
-- Источник истины — settings_kv (их читает backend через get_kv_int).
-- Ключ уже существует, поэтому делаем явный UPDATE. INSERT IGNORE — страховка.
--
-- Применять:
--   mysql -u <user> -p <db> < 0026_free_speaking_15min.sql
--
-- Идемпотентно.

UPDATE settings_kv SET value = '900' WHERE `key` = 'free_seconds_per_day';

INSERT IGNORE INTO settings_kv (`key`, value) VALUES
    ('free_seconds_per_day', '900');


-- ─── schema_version = 26 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (26)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

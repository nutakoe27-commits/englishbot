-- Миграция 0008: sessions.mode += 'listening'
--
-- Зачем: добавляем listening-тренажёр (генерация подкастов через Kokoro TTS).
-- Чтобы не плодить отдельную таблицу, переиспользуем существующую `sessions`
-- (поля user_id, started_at, ended_at, used_seconds, level, role подходят
-- как есть; в role хранится category подкаста).
--
-- Как применять:
--   mysql -u <user> -p <db> < 0008_listening_mode.sql
--
-- Идемпотентно: MODIFY COLUMN до полного enum-набора применяется безопасно
-- даже если 'listening' уже присутствует.

ALTER TABLE sessions
    MODIFY COLUMN mode ENUM('voice','chat','listening') NOT NULL DEFAULT 'voice';


-- ─── schema_version ──────────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (8)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

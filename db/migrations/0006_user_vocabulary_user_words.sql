-- Миграция 0006: расширение user_vocabulary для пользовательских слов.
--
-- Что добавляет:
--   1. source: 'tutor' (по умолчанию — поведение как сейчас, слова из
--      session_recap) | 'user' (юзер добавил вручную через Mini App)
--      | 'import' (зарезервировано на будущее — массовый импорт из Anki/
--      Quizlet/Duolingo).
--   2. note: опциональный перевод/заметка — для будущего импорта;
--      в MVP не используется.
--   3. Индекс (user_id, source, last_seen_at) — быстрая выборка именно
--      user-слов из репо.
--
-- Зачем:
--   Юзер хочет добавлять свои слова, которые сейчас учит, и чтобы
--   тьютор их вкручивал в разговор приоритетно. Архитектура уже умеет
--   подмешивать user_vocabulary в system_prompt — поэтому проще всего
--   расширить ту же таблицу маркером source='user' вместо новой.
--
-- Как применять:
--   mysql -u <user> -p <db> < 0006_user_vocabulary_user_words.sql
--
-- Идемпотентно: проверяет существование колонок/индекса.

-- ─── 1. source ────────────────────────────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_vocabulary' AND COLUMN_NAME = 'source'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE user_vocabulary ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT ''tutor'' COMMENT ''tutor | user | import''',
    'SELECT ''source already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 2. note ──────────────────────────────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_vocabulary' AND COLUMN_NAME = 'note'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE user_vocabulary ADD COLUMN note VARCHAR(255) NULL COMMENT ''Перевод/заметка — для будущего импорта''',
    'SELECT ''note already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 3. индекс idx_uv_source ─────────────────────────────────────────
SET @idx := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_vocabulary' AND INDEX_NAME = 'idx_uv_source'
);
SET @ddl := IF(@idx = 0,
    'ALTER TABLE user_vocabulary ADD INDEX idx_uv_source (user_id, source, last_seen_at DESC)',
    'SELECT ''idx_uv_source already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── schema_version ──────────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (6)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

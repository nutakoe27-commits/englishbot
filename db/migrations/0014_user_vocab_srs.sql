-- Миграция 0014: SRS-поля для user_vocabulary + расширение лимита.
--
-- Что добавляет:
--   1. translation: перевод как first-class колонка (раньше жил в свободном `note`).
--   2. srs_box, srs_due_at, srs_correct_streak, srs_total_attempts,
--      srs_last_reviewed_at — состояние карточки в Leitner-системе.
--   3. Индекс (user_id, srs_due_at) — выборка due-карточек на review.
--   4. Backfill: для всех существующих user-слов выставляем srs_due_at=NOW(),
--      чтобы юзеры могли начать повторение сразу после деплоя.
--   5. Переносим существующие note → translation (для user-слов).
--
-- Tutor-слова не трогаем: SRS работает только над source='user'. Tutor-слова
-- остаются «пассивным контекстом» для промпта, как и раньше.
--
-- Лимит USER_WORDS_LIMIT (100 → 3000) — в коде backend/app/db/repo.py,
-- ALTER не нужен (это не схема, а app-level constant).
--
-- Применять:
--   mysql -u <user> -p <db> < 0014_user_vocab_srs.sql
--
-- Идемпотентно: проверяет наличие колонок/индекса.

-- ─── 1. translation ─────────────────────────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_vocabulary' AND COLUMN_NAME = 'translation'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE user_vocabulary ADD COLUMN translation VARCHAR(255) NULL AFTER word',
    'SELECT ''translation already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Переносим существующие note → translation (только для user-слов где
-- translation ещё пуст). Делаем один раз — после ALTER. Если миграция
-- запускается повторно, обновятся только те же rows (idempotent — все
-- уже скопированы, condition `IS NULL` отсечёт).
UPDATE user_vocabulary
   SET translation = note
 WHERE source = 'user'
   AND translation IS NULL
   AND note IS NOT NULL;

-- ─── 2. srs_box ─────────────────────────────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_vocabulary' AND COLUMN_NAME = 'srs_box'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE user_vocabulary ADD COLUMN srs_box TINYINT NOT NULL DEFAULT 0',
    'SELECT ''srs_box already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 3. srs_due_at ──────────────────────────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_vocabulary' AND COLUMN_NAME = 'srs_due_at'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE user_vocabulary ADD COLUMN srs_due_at DATETIME NULL',
    'SELECT ''srs_due_at already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 4. srs_correct_streak ──────────────────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_vocabulary' AND COLUMN_NAME = 'srs_correct_streak'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE user_vocabulary ADD COLUMN srs_correct_streak INT NOT NULL DEFAULT 0',
    'SELECT ''srs_correct_streak already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 5. srs_total_attempts ──────────────────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_vocabulary' AND COLUMN_NAME = 'srs_total_attempts'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE user_vocabulary ADD COLUMN srs_total_attempts INT NOT NULL DEFAULT 0',
    'SELECT ''srs_total_attempts already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 6. srs_last_reviewed_at ────────────────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_vocabulary' AND COLUMN_NAME = 'srs_last_reviewed_at'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE user_vocabulary ADD COLUMN srs_last_reviewed_at DATETIME NULL',
    'SELECT ''srs_last_reviewed_at already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 7. Индекс idx_vocab_user_due ───────────────────────────────────────
SET @idx := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_vocabulary' AND INDEX_NAME = 'idx_vocab_user_due'
);
SET @ddl := IF(@idx = 0,
    'ALTER TABLE user_vocabulary ADD INDEX idx_vocab_user_due (user_id, srs_due_at)',
    'SELECT ''idx_vocab_user_due already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 8. Backfill srs_due_at для существующих user-слов ──────────────────
-- Все user-слова, у которых due ещё пуст, становятся доступны для
-- повторения прямо сейчас. Если у юзера 200 слов — будет 200 due-карточек,
-- но review-сессия берёт максимум 20 за раз (см. backend/app/srs.py),
-- так что юзер просто будет каждый день первое время разгребать.
UPDATE user_vocabulary
   SET srs_due_at = NOW()
 WHERE source = 'user' AND srs_due_at IS NULL;


-- ─── schema_version ─────────────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (14)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

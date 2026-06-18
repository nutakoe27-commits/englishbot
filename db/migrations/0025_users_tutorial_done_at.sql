-- Миграция 0025: users.tutorial_done_at — флаг прохождения онбординга (PR-10).
--
-- Зачем:
--   Новый юзер при первом входе видит модалку-слайдер с 4 шагами (режимы,
--   профиль, привязки, подписка). Существующим юзерам на момент применения
--   миграции онбординг показывать НЕ нужно — для них ставим tutorial_done_at
--   = NOW() (backfill).
--
-- После миграции:
--   - все существующие users.tutorial_done_at IS NOT NULL → онбординг скрыт;
--   - новые users.tutorial_done_at IS NULL (default) → онбординг покажется
--     при первом заходе; ставится в NOW() после прохождения/skip.
--
-- Применять:
--   mysql -u <user> -p <db> < 0025_users_tutorial_done_at.sql
--
-- Идемпотентно: если колонка уже есть, ALTER пропускается; backfill
-- безопасно повторяется (ставит NOW() только там, где IS NULL).

SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND COLUMN_NAME = 'tutorial_done_at'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE users ADD COLUMN tutorial_done_at DATETIME NULL DEFAULT NULL',
    'SELECT ''users.tutorial_done_at already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Backfill: существующим юзерам на момент миграции онбординг не показываем.
UPDATE users SET tutorial_done_at = NOW() WHERE tutorial_done_at IS NULL;


-- ─── schema_version = 25 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (25)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

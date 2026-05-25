-- Миграция 0007: retention v1 — achievements + last_winback_at
--
-- Что добавляет:
--   1. users.last_winback_at — для дедупликации winback-рассылки в боте.
--      Юзер не получает повторный win-back пока с прошлого не прошло 7 дней.
--   2. user_achievements — таблица заработанных медалей.
--      PK (user_id, achievement_key) обеспечивает идемпотентность INSERT.
--
-- Зачем:
--   Retention-механики: персонализированные напоминания, win-back для
--   неактивных, экран «Мой прогресс» с медалями в mini-app.
--
-- Как применять:
--   docker compose exec mysql mysql englishbot < 0007_retention_v1.sql
--
-- Идемпотентно.

-- ─── 1. users.last_winback_at ────────────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND COLUMN_NAME = 'last_winback_at'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE users ADD COLUMN last_winback_at DATETIME NULL COMMENT ''Когда последний раз слали win-back-сообщение''',
    'SELECT ''last_winback_at already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 2. таблица user_achievements ────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_achievements'
);
SET @ddl := IF(@tbl = 0,
    'CREATE TABLE user_achievements (
       user_id BIGINT UNSIGNED NOT NULL,
       achievement_key VARCHAR(64) NOT NULL,
       earned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
       PRIMARY KEY (user_id, achievement_key),
       CONSTRAINT fk_user_achievements_user FOREIGN KEY (user_id)
         REFERENCES users(id) ON DELETE CASCADE
     ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT=''Заработанные медали''',
    'SELECT ''user_achievements already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── schema_version ──────────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (7)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

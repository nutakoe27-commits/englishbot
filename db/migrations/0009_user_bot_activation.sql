-- Миграция 0009: users.bot_activated_at
--
-- Зачем: до сих пор в таблицу `users` юзер попадал только после открытия
-- Mini App (WS-preflight в voice.py / POST /api/listening/generate).
-- Тех, кто написал боту /start, но ни разу не открыл Mini App, в БД не было.
-- Админка хочет видеть «сколько всего активировало бота» — добавляем колонку
-- bot_activated_at и пишем в неё из бота на любом апдейте от юзера.
--
-- Семантика:
--   - NULL = юзер пришёл из Mini App, бота в Telegram не открывал.
--   - NOT NULL = был хотя бы один апдейт от юзера в Telegram-боте.
--   - Заполняется один раз (первая активация), потом не перезаписывается.
--
-- Как применять:
--   mysql -u <user> -p <db> < 0009_user_bot_activation.sql
--
-- Идемпотентно: проверяет существование колонки.

SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND COLUMN_NAME = 'bot_activated_at'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE users ADD COLUMN bot_activated_at DATETIME NULL COMMENT ''Время первого апдейта от юзера в Telegram-боте (NULL = только Mini App)''',
    'SELECT ''bot_activated_at already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Индекс для метрики count(*) WHERE bot_activated_at IS NOT NULL.
SET @idx := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND INDEX_NAME = 'idx_users_bot_activated_at'
);
SET @ddl := IF(@idx = 0,
    'CREATE INDEX idx_users_bot_activated_at ON users (bot_activated_at)',
    'SELECT ''idx_users_bot_activated_at already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ─── schema_version ──────────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (9)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

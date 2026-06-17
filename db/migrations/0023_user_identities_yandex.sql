-- Миграция 0023: расширение ENUM user_identities.provider для Яндекс ID.
--
-- Что делает:
--   Добавляет 'yandex' в ENUM колонки provider — необходимо для PR-7 серии
--   0021, где Яндекс ID становится основным OAuth-провайдером на сайте
--   (вместо Telegram Login, запрещённого в РФ).
--
-- Зачем:
--   Без расширения enum попытка INSERT INTO user_identities
--   (provider='yandex', ...) упадёт с ER_TRUNCATED_WRONG_VALUE_FOR_FIELD.
--
-- Применять:
--   mysql -u <user> -p <db> < 0023_user_identities_yandex.sql
--
-- Идемпотентно: если 'yandex' уже есть в ENUM, ALTER пропускается.

SET @col_def := (
  SELECT COLUMN_TYPE FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'user_identities' AND COLUMN_NAME = 'provider'
);
SET @ddl := IF(@col_def NOT LIKE '%yandex%',
  "ALTER TABLE user_identities MODIFY COLUMN provider ENUM('telegram','native','vk','yandex') NOT NULL",
  "SELECT 'enum already has yandex — skipped' AS msg");
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ─── schema_version = 23 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (23)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

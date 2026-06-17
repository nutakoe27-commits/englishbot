-- Миграция 0024: убрать 'vk' из ENUM user_identities.provider.
--
-- Что делает:
--   VK ID никогда не реализовывался (план менялся). Заглушка убрана из
--   моделей и UI — убираем и из БД.
--
-- Безопасность:
--   Перед ALTER проверяем, что нет ни одной строки с provider='vk'. Если
--   есть — пропускаем миграцию и логируем (внезапных vk-identity на проде
--   быть не должно, но страховка). После применения 0023 ENUM был
--   ('telegram','native','vk','yandex'), цель — ('telegram','native','yandex').
--
-- Применять:
--   mysql -u <user> -p <db> < 0024_drop_vk_from_provider.sql
--
-- Идемпотентно: если 'vk' уже отсутствует — ALTER пропускается.

SET @col_def := (
  SELECT COLUMN_TYPE FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'user_identities' AND COLUMN_NAME = 'provider'
);
SET @has_vk_rows := (
  SELECT COUNT(*) FROM user_identities WHERE provider = 'vk'
);
SET @ddl := CASE
  WHEN @col_def NOT LIKE '%vk%' THEN
    "SELECT 'vk already absent from ENUM — skipped' AS msg"
  WHEN @has_vk_rows > 0 THEN
    "SELECT 'rows with provider=vk exist — manual cleanup required, skipped' AS msg"
  ELSE
    "ALTER TABLE user_identities MODIFY COLUMN provider ENUM('telegram','native','yandex') NOT NULL"
END;
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ─── schema_version = 24 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (24)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

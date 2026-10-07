-- Миграция 0040: вход через VK ID (ВКонтакте, Одноклассники, Mail.ru).
--
-- Все три сервиса работают через одно приложение VK ID (id.vk.ru): OK и
-- Mail.ru — это способы входа внутри VK ID, а не отдельные провайдеры.
-- Человек, вошедший через OK, получает тот же VK ID user_id, что и через
-- ВКонтакте, если это один аккаунт. Поэтому личность одна — provider='vk',
-- provider_uid = VK ID user_id, — а через какой сервис пришли, пишем в
-- новую колонку via: 'vk' | 'ok' | 'mail'. Для статистики и админки.
--
-- 1) ENUM provider гарантированно содержит 'vk' (миграция 0023 его уже
--    добавляла — проверка на случай базы, где enum перестраивали вручную).
-- 2) user_identities.via VARCHAR(16) NULL.
--
-- Применять:
--   mysql -u <user> -p <db> < 0040_vk_id.sql
-- Идемпотентно.

SET @col_def := (
  SELECT COLUMN_TYPE FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'user_identities' AND COLUMN_NAME = 'provider'
);
SET @ddl := IF(@col_def NOT LIKE '%''vk''%',
  "ALTER TABLE user_identities MODIFY COLUMN provider ENUM('telegram','native','vk','yandex') NOT NULL",
  "SELECT 'enum already has vk — skipped' AS msg");
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @has := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_identities'
      AND COLUMN_NAME = 'via'
);
SET @ddl := IF(@has = 0,
    'ALTER TABLE user_identities ADD COLUMN via VARCHAR(16) NULL',
    'SELECT ''user_identities.via exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ─── schema_version = 40 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (40)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

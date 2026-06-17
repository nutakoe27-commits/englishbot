-- Миграция 0021: откат Google-входа + подготовка к нативной (email/password)
-- регистрации.
--
-- Что делает:
--   1) Удаляет пользователей, у которых единственная identity — google
--      (CASCADE подчистит сессии/слова/платежи). Если у юзера есть и
--      telegram, и google — оставляем самого юзера, удаляем только
--      google-identity (см. шаг 2).
--   2) Удаляет ВСЕ оставшиеся google-identity (у юзеров с Telegram-связью).
--   3) Перестраивает ENUM identity_provider: telegram/google/apple →
--      telegram/native/vk. Google и Apple запрещены в РФ с 9 июня 2026.
--   4) Добавляет users.password_hash для будущей нативной регистрации
--      (сама логика — в коде, см. PR-2).
--
-- Зачем:
--   Юридически нельзя использовать Google/Apple OAuth (штрафы). Откатываем
--   PR'ы #116/#117/#118/#123/#124 (часть с Google) и готовим почву для
--   email+password и VK ID.
--
-- Применять:
--   mysql -u <user> -p <db> < 0021_remove_google_add_native.sql
--
-- Идемпотентно.

-- ─── 1. Удалить юзеров с ТОЛЬКО google-identity ──────────────────────
-- CASCADE на user_identities/sessions/daily_usage/etc подчистит остатки.
DELETE FROM users
 WHERE id IN (
   SELECT user_id FROM (
     SELECT user_id FROM user_identities WHERE provider = 'google'
   ) g
   WHERE user_id NOT IN (
     SELECT user_id FROM user_identities
      WHERE provider IN ('telegram', 'native', 'vk', 'apple')
   )
 );

-- ─── 2. Отвязать google-identity у тех, у кого остался Telegram ──────
DELETE FROM user_identities WHERE provider = 'google';
-- На всякий случай — и apple (фактически не использовался, но в ENUM был).
DELETE FROM user_identities WHERE provider = 'apple';

-- ─── 3. Перестроить ENUM identity_provider ───────────────────────────
-- MODIFY на ENUM до нового набора — безопасно, если значений вне него нет
-- (мы только что почистили google/apple на шаге 2).
ALTER TABLE user_identities
  MODIFY COLUMN provider ENUM('telegram','native','vk') NOT NULL;

-- ─── 4. users.password_hash ──────────────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND COLUMN_NAME = 'password_hash'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE users ADD COLUMN password_hash VARCHAR(255) NULL AFTER email',
    'SELECT ''users.password_hash already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ─── schema_version = 21 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (21)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Миграция 0020: мульти-провайдерная авторизация (Telegram/Google/Apple).
--
-- Что делает:
--   1. user_identities — личности пользователя у разных провайдеров. Один
--      users.id может иметь несколько identity (telegram + google + apple).
--      UNIQUE(provider, provider_uid) — одна внешняя личность = один аккаунт.
--   2. users.email — опциональный email (приходит от Google/Apple; для
--      веб-аккаунтов и поиска в админке).
--   3. users.tg_id → NULLABLE: теперь можно зарегистрироваться через
--      Google/Apple без Telegram. UNIQUE в MySQL допускает несколько NULL.
--   4. Backfill: для каждого существующего юзера с tg_id создаём
--      telegram-identity (INSERT IGNORE — идемпотентно).
--
-- Зачем: вход в сервис через сайт (Telegram Login / Google), регистрация любым
-- провайдером, привязка провайдеров к аккаунту (сохранить прогресс при
-- блокировке Telegram).
--
-- Применять:
--   mysql -u <user> -p <db> < 0020_auth_identities.sql
--
-- Идемпотентно.

-- ─── 1. таблица user_identities ──────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_identities'
);
SET @ddl := IF(@tbl = 0,
    'CREATE TABLE user_identities (
       id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
       user_id       BIGINT UNSIGNED NOT NULL,
       provider      ENUM(''telegram'',''google'',''apple'') NOT NULL,
       provider_uid  VARCHAR(191) NOT NULL,
       email         VARCHAR(255) NULL,
       created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
       PRIMARY KEY (id),
       UNIQUE KEY uq_identity_provider_uid (provider, provider_uid),
       KEY idx_identity_user (user_id),
       KEY idx_identity_email (email),
       CONSTRAINT fk_identity_user FOREIGN KEY (user_id)
         REFERENCES users(id) ON DELETE CASCADE
     ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci',
    'SELECT ''user_identities already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 2. users.email ──────────────────────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND COLUMN_NAME = 'email'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE users ADD COLUMN email VARCHAR(255) NULL AFTER language_code',
    'SELECT ''users.email already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @idx := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND INDEX_NAME = 'idx_users_email'
);
SET @ddl := IF(@idx = 0,
    'ALTER TABLE users ADD INDEX idx_users_email (email)',
    'SELECT ''idx_users_email already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 3. users.tg_id → NULLABLE ───────────────────────────────────────
-- Проверяем текущую nullability; если NOT NULL — снимаем. UNIQUE-ключ
-- uq_users_tg_id остаётся (MySQL допускает несколько NULL в UNIQUE).
SET @nullable := (
    SELECT IS_NULLABLE FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND COLUMN_NAME = 'tg_id'
);
SET @ddl := IF(@nullable = 'NO',
    'ALTER TABLE users MODIFY COLUMN tg_id BIGINT NULL',
    'SELECT ''users.tg_id already nullable'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 4. Backfill telegram-identity для существующих юзеров ────────────
INSERT IGNORE INTO user_identities (user_id, provider, provider_uid, created_at)
SELECT id, 'telegram', CAST(tg_id AS CHAR), created_at
  FROM users
 WHERE tg_id IS NOT NULL;

-- Заодно перенести email из identities? Нет — email на users заполняется
-- при регистрации через Google/Apple, у telegram-юзеров его нет.


-- ─── schema_version = 20 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (20)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

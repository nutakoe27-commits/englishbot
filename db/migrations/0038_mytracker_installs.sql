-- Миграция 0038: связка аккаунта с установкой Android-приложения (VK MyTracker).
--
-- Зачем. Рекламная кампания VK на установку видит установку через SDK
-- MyTracker в APK. Регистрация и оплата происходят в веб-части приложения,
-- и SDK о них не знает. Нативная обёртка передаёт идентификатор установки
-- (instanceId) в стартовый адрес, сайт после входа отдаёт его бэкенду, а
-- бэкенд шлёт регистрацию и оплату в MyTracker через S2S API с этим
-- instanceId. Так события привязываются к рекламе, давшей установку.
--
-- Один аккаунт может стоять на нескольких телефонах, и на одном телефоне
-- могут входить разные аккаунты — поэтому ключ составной.
--
-- Применять:
--   mysql -u <user> -p <db> < 0038_mytracker_installs.sql
-- Идемпотентно.

CREATE TABLE IF NOT EXISTS mytracker_installs (
    user_id      BIGINT UNSIGNED NOT NULL,
    instance_id  VARCHAR(64)     NOT NULL,
    created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, instance_id),
    KEY idx_mt_install_user_seen (user_id, last_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─── schema_version = 38 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (38)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

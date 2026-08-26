-- Миграция 0037: веб-пуши (Web Push API).
--
-- Зачем: Telegram в России доступен рывками, и канал до человека, который
-- от него не зависит, стал критичным. Web push работает и в обычном
-- мобильном Chrome, и внутри установленного приложения (TWA) — одна
-- подписка обслуживает обе площадки.
--
-- Что хранится. Подписка, которую отдал браузер: endpoint (адрес, куда
-- слать) и два ключа шифрования. Тело уведомления шифруется на стороне
-- нашего сервера этими ключами — промежуточный сервис (у Chrome это
-- инфраструктура Google) содержимого не видит.
--
-- endpoint UNIQUE: браузер выдаёт один endpoint на установку, и повторная
-- подписка того же браузера должна обновлять строку, а не плодить копии.
-- Ключ длинный, поэтому под индекс берём префикс в 190 символов —
-- utf8mb4 не даёт проиндексировать VARCHAR(512) целиком.
--
-- user_id NULL допустим: подписаться можно и до входа (например, с
-- лендинга теста уровня), а привязать к аккаунту позже.
--
-- failed_count и last_ok_at нужны для чистки: браузер отвечает 404/410 на
-- отозванную подписку, такие строки удаляются сразу, а редкие сетевые
-- ошибки просто копятся счётчиком.
--
-- Применять:
--   mysql -u <user> -p <db> < 0037_push_subscriptions.sql
-- Идемпотентно.

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id      BIGINT UNSIGNED NULL,
    endpoint     VARCHAR(512)    NOT NULL,
    p256dh       VARCHAR(255)    NOT NULL,
    auth         VARCHAR(255)    NOT NULL,
    user_agent   VARCHAR(255)    NULL,
    source       VARCHAR(16)     NOT NULL DEFAULT 'web',
    created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_ok_at   DATETIME        NULL,
    failed_count INT             NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE KEY uq_push_endpoint (endpoint(190)),
    KEY idx_push_user (user_id),
    KEY idx_push_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─── schema_version = 37 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (37)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

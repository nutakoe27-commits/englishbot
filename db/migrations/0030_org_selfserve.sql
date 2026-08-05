-- Миграция 0030: самостоятельное подключение школ (B2B self-service).
--
-- 1) payments.plan ENUM += 'org' — платёж за пакет мест для школы.
--    ВАЖНО: такие платежи НЕ продлевают личную подписку плательщика,
--    вебхук обрабатывает их отдельной веткой (fulfill_org_order).
-- 2) org_orders — заявка на подключение школы, привязанная к платежу.
--    Хранит параметры заказа до оплаты; после успеха создаётся organization,
--    а её id проставляется в org_id.
--    UNIQUE(payment_id) — один заказ на платёж, защита от повторной выдачи.
-- 3) schema_version = 30.
--
-- Применять:
--   mysql -u <user> -p <db> < 0030_org_selfserve.sql
-- Идемпотентно.

ALTER TABLE payments
    MODIFY COLUMN plan
    ENUM('trial3','monthly','yearly','twoyear','gift','admin_grant','manual_pay','org')
    NOT NULL;

CREATE TABLE IF NOT EXISTS org_orders (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    payment_id     BIGINT UNSIGNED NOT NULL,
    user_id        BIGINT UNSIGNED NOT NULL,
    school_name    VARCHAR(128)    NOT NULL,
    contact_person VARCHAR(128)    NULL,
    contact_email  VARCHAR(255)    NULL,
    seats          INT             NOT NULL,
    months         INT             NOT NULL,
    amount_rub     INT             NOT NULL,
    org_id         BIGINT UNSIGNED NULL,
    status         ENUM('pending','done','failed') NOT NULL DEFAULT 'pending',
    created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_org_order_payment (payment_id),
    KEY idx_org_order_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─── schema_version = 30 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (30)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Миграция 0027: тариф «2 года» + система промокодов.
--
-- 1) payments.plan ENUM += 'twoyear' (старые значения, включая trial3,
--    оставляем для исторических строк).
-- 2) payments += promo_code, discount_percent (аудит скидки на платеже).
-- 3) promo_codes — каталог промокодов (код + % скидки + active + счётчик).
-- 4) promo_activations — кто/когда активировал; UNIQUE(code,user_id) даёт
--    правило «1 раз на юзера».
-- 5) schema_version = 27.
--
-- Применять:
--   mysql -u <user> -p <db> < 0027_promo_and_twoyear.sql
-- Идемпотентно.

ALTER TABLE payments
    MODIFY COLUMN plan
    ENUM('trial3','monthly','yearly','twoyear','gift','admin_grant','manual_pay')
    NOT NULL;

-- Колонки promo на платеже (IF NOT EXISTS — MySQL 8 поддерживает для ADD COLUMN).
SET @has_promo := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payments'
      AND COLUMN_NAME = 'promo_code'
);
SET @ddl := IF(@has_promo = 0,
    'ALTER TABLE payments ADD COLUMN promo_code VARCHAR(32) NULL, ADD COLUMN discount_percent INT NULL',
    'SELECT ''payments.promo_code exists'' AS msg');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

CREATE TABLE IF NOT EXISTS promo_codes (
    code             VARCHAR(32)     NOT NULL,
    discount_percent INT             NOT NULL,
    active           TINYINT(1)      NOT NULL DEFAULT 1,
    used_count       INT             NOT NULL DEFAULT 0,
    created_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS promo_activations (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code             VARCHAR(32)     NOT NULL,
    user_id          BIGINT UNSIGNED NOT NULL,
    payment_id       BIGINT UNSIGNED NULL,
    discount_percent INT             NOT NULL,
    created_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_promo_user (code, user_id),
    KEY idx_promo_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─── schema_version = 27 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl2 := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (27)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl2; EXECUTE stmt; DEALLOCATE PREPARE stmt;

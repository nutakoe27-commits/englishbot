-- Миграция 0031: учительские ссылки, продление/докупка, пробный период,
-- заявки на счёт для юрлиц.
--
-- 1) organizations.teacher_code — второй инвайт-код. Переход по нему даёт
--    роль teacher (кабинет школы, места не занимает). Backfill для
--    существующих школ делается кодом на старте backend, не здесь:
--    коды должны быть уникальными и генерируются тем же алфавитом.
-- 2) organizations.is_trial — школа заведена по бесплатному пробному
--    периоду. Один пробный период на пользователя.
-- 3) org_orders.kind — что оплачивается: новая школа | продление |
--    докупка мест. target_org_id — для renew/seats указывает на школу.
-- 4) org_invoice_requests — заявки на счёт и договор от юрлиц (оплата
--    картой подходит не всем школам).
-- 5) schema_version = 31.
--
-- Применять:
--   mysql -u <user> -p <db> < 0031_org_selfserve_v2.sql
-- Идемпотентно.

-- ─── organizations: teacher_code + is_trial ──────────────────────────
SET @has_tc := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'organizations'
      AND COLUMN_NAME = 'teacher_code'
);
SET @ddl := IF(@has_tc = 0,
    'ALTER TABLE organizations ADD COLUMN teacher_code VARCHAR(32) NULL, ADD COLUMN is_trial TINYINT(1) NOT NULL DEFAULT 0',
    'SELECT ''organizations.teacher_code exists'' AS msg');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @has_tc_idx := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'organizations'
      AND INDEX_NAME = 'uq_org_teacher_code'
);
SET @ddl := IF(@has_tc_idx = 0,
    'ALTER TABLE organizations ADD UNIQUE KEY uq_org_teacher_code (teacher_code)',
    'SELECT ''uq_org_teacher_code exists'' AS msg');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── org_orders: тип заказа и целевая школа ──────────────────────────
SET @has_kind := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'org_orders'
      AND COLUMN_NAME = 'kind'
);
SET @ddl := IF(@has_kind = 0,
    'ALTER TABLE org_orders ADD COLUMN kind ENUM(''new'',''renew'',''seats'') NOT NULL DEFAULT ''new'', ADD COLUMN target_org_id BIGINT UNSIGNED NULL',
    'SELECT ''org_orders.kind exists'' AS msg');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── Заявки на счёт для юрлица ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS org_invoice_requests (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    school_name    VARCHAR(128)    NOT NULL,
    inn            VARCHAR(32)     NULL,
    contact_person VARCHAR(128)    NULL,
    contact_email  VARCHAR(255)    NOT NULL,
    phone          VARCHAR(32)     NULL,
    seats          INT             NOT NULL,
    months         INT             NOT NULL,
    amount_rub     INT             NOT NULL,
    comment        VARCHAR(1000)   NULL,
    user_id        BIGINT UNSIGNED NULL,
    status         ENUM('new','done') NOT NULL DEFAULT 'new',
    created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_invoice_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─── schema_version = 31 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl2 := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (31)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl2; EXECUTE stmt; DEALLOCATE PREPARE stmt;

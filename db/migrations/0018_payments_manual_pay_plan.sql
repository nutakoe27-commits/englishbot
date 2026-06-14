-- Миграция 0018: payments.plan += 'manual_pay'
--
-- Зачем: админка («Продлить / подарить подписку») предлагает тип
-- 'manual_pay (ручная оплата)' и шлёт его в grant-subscription, но ENUM
-- payment_plan этого значения не содержал → INSERT падал, ручная выдача
-- возвращала Internal Server Error.
--
-- Также обновляется backend-модель Payment (SAEnum) — иначе чтение такой
-- строки в админке падает LookupError.
--
-- Применять:
--   mysql -u <user> -p <db> < 0018_payments_manual_pay_plan.sql
--
-- Идемпотентно: MODIFY COLUMN до полного набора применяется безопасно.

ALTER TABLE payments
    MODIFY COLUMN plan
    ENUM('trial3','monthly','yearly','gift','admin_grant','manual_pay') NOT NULL;


-- ─── schema_version = 18 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (18)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

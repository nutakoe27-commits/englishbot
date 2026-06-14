-- Миграция 0017: payments.plan += 'trial3'
--
-- Зачем: добавлен 3-дневный тариф (trial3, 99₽). При успешной оплате бот
-- пишет в payments строку с plan='trial3', но ENUM payment_plan его не
-- содержал → INSERT падал с «Data truncated for column 'plan'», и дни не
-- зачислялись («Оплата получена, но не удалось зачислить дни»).
--
-- Также обновляется backend-модель Payment (SAEnum) — иначе чтение такой
-- строки в админке падает с LookupError (как было с mode='srs').
--
-- Применять:
--   mysql -u <user> -p <db> < 0017_payments_trial3_plan.sql
--
-- Идемпотентно: MODIFY COLUMN до полного набора применяется безопасно.

ALTER TABLE payments
    MODIFY COLUMN plan
    ENUM('trial3','monthly','yearly','gift','admin_grant') NOT NULL;


-- ─── schema_version = 17 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (17)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

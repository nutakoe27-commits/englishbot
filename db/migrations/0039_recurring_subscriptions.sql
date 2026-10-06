-- Миграция 0039: автопродление месячной подписки (автоплатежи ЮKassa).
--
-- Как устроено. Первая оплата месяца с галочкой «Продлевать автоматически»
-- идёт в ЮKassa с save_payment_method=true. Если ЮKassa сохранила способ
-- оплаты, вебхук заводит строку recurring_subscriptions с его id. За сутки
-- до конца оплаченного периода бэкенд сам создаёт платёж по этому способу
-- (без участия человека), а успешный платёж продлевает подписку так же, как
-- обычный. Подробно: docs/recurring.md.
--
-- recurring_subscriptions — одна строка на пользователя:
--   status            active | canceled (отключил сам) | failed (не списалось
--                     после всех попыток или способ оплаты отозван)
--   payment_method_id сохранённый способ оплаты в ЮKassa
--   method_title      «Bank card *4444» — показываем в профиле
--   next_charge_at    когда списывать (UTC); сдвигается после каждой оплаты
--   attempts          неудачные попытки в текущем цикле (сбрасывается)
--   locked_until      захват строки на время списания: защищает от двойного
--                     списания, если цикл запущен в двух процессах
--   reminded_for      next_charge_at, о котором уже напомнили (один раз)
--
-- payments.recurring_subscription_id — какие платежи были автосписаниями.
--
-- Применять:
--   mysql -u <user> -p <db> < 0039_recurring_subscriptions.sql
-- Идемпотентно.

CREATE TABLE IF NOT EXISTS recurring_subscriptions (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id           BIGINT UNSIGNED NOT NULL,
    plan              VARCHAR(16)     NOT NULL DEFAULT 'monthly',
    status            VARCHAR(12)     NOT NULL DEFAULT 'active',
    payment_method_id VARCHAR(64)     NOT NULL,
    method_title      VARCHAR(64)     NULL,
    amount_rub        INT             NOT NULL,
    period_days       INT             NOT NULL DEFAULT 30,
    email             VARCHAR(255)    NULL,
    next_charge_at    DATETIME        NOT NULL,
    attempts          INT             NOT NULL DEFAULT 0,
    last_error        VARCHAR(255)    NULL,
    locked_until      DATETIME        NULL,
    reminded_for      DATETIME        NULL,
    canceled_at       DATETIME        NULL,
    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_recurring_user (user_id),
    KEY idx_recurring_due (status, next_charge_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET @has := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payments'
      AND COLUMN_NAME = 'recurring_subscription_id'
);
SET @ddl := IF(@has = 0,
    'ALTER TABLE payments ADD COLUMN recurring_subscription_id BIGINT UNSIGNED NULL, ADD KEY idx_payments_recurring (recurring_subscription_id)',
    'SELECT ''payments.recurring_subscription_id exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ─── schema_version = 39 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (39)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

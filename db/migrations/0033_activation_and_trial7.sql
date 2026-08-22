-- Миграция 0033: активация новых пользователей и платный триал-ступень.
--
-- 1) payments.plan ENUM += 'trial7' — платный пробный период 7 дней.
--    (trial3 остаётся для исторических строк.)
-- 2) user_nudges — дедупликация проактивных сообщений (окончание триала,
--    напоминание о продлении, чек-ин подписчику, апсейл после trial7).
--    UNIQUE(user_id, kind, dedup_key) + INSERT IGNORE = «отправить ровно раз».
-- 3) Промокод для апсейла с trial7 на год — создаём здесь, чтобы бот мог
--    слать его сразу после накатывания миграции.
-- 4) schema_version = 33.
--
-- Бесплатные 3 дня полного доступа новым пользователям СХЕМЫ НЕ ТРЕБУЮТ:
-- окно считается от users.created_at (см. Repo.in_free_trial). Так оно
-- автоматически работает для всех путей создания юзера — бот, мини-апп,
-- веб-регистрация — без правки каждого INSERT'а.
--
-- Применять:
--   mysql -u <user> -p <db> < 0033_activation_and_trial7.sql
-- Идемпотентно.

ALTER TABLE payments
    MODIFY COLUMN plan
    ENUM('trial3','trial7','monthly','yearly','twoyear','gift','admin_grant',
         'manual_pay','org','referral')
    NOT NULL;

CREATE TABLE IF NOT EXISTS user_nudges (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id    BIGINT UNSIGNED NOT NULL,
    kind       VARCHAR(32)     NOT NULL,
    dedup_key  VARCHAR(64)     NOT NULL DEFAULT '',
    created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_nudge (user_id, kind, dedup_key),
    KEY idx_nudge_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Промокод для апсейла «trial7 → год» (30%). Меняется в админке как обычный.
INSERT IGNORE INTO promo_codes (code, discount_percent, active, used_count)
VALUES ('HABIT30', 30, 1, 0);


-- ─── schema_version = 33 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (33)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

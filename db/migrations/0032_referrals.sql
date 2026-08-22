-- Миграция 0032: реферальная система + ссылки для аналитики (только TG-бот).
--
-- 1) payments.plan ENUM += 'referral' — аудит начисленных за приглашение дней.
-- 2) users += ref_code (личный код-приглашение, UNIQUE) и source_link_id
--    (первая аналитическая ссылка, по которой юзер пришёл — атрибуция).
-- 3) referrals — факт приглашения. UNIQUE(invited_user_id) даёт правило
--    «одного человека можно пригласить только один раз за всё время».
--    status: rewarded (оба получили дни) | skipped_existing (получатель уже
--    был зарегистрирован в боте — по требованию НИКТО не получает бонус).
-- 4) ad_links — ссылки для аналитики, создаются в админке (/start src_<code>).
-- 5) ad_link_hits — переходы. UNIQUE(link_id, user_id) — уникальные люди;
--    сырые клики считаются счётчиком ad_links.clicks.
-- 6) schema_version = 32.
--
-- Применять:
--   mysql -u <user> -p <db> < 0032_referrals.sql
-- Идемпотентно.

ALTER TABLE payments
    MODIFY COLUMN plan
    ENUM('trial3','monthly','yearly','twoyear','gift','admin_grant','manual_pay',
         'org','referral')
    NOT NULL;

-- ── users.ref_code ───────────────────────────────────────────────────
SET @has := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'
      AND COLUMN_NAME = 'ref_code'
);
SET @ddl := IF(@has = 0,
    'ALTER TABLE users ADD COLUMN ref_code VARCHAR(16) NULL, ADD UNIQUE KEY uq_users_ref_code (ref_code)',
    'SELECT ''users.ref_code exists'' AS msg');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ── users.source_link_id ─────────────────────────────────────────────
SET @has := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'
      AND COLUMN_NAME = 'source_link_id'
);
SET @ddl := IF(@has = 0,
    'ALTER TABLE users ADD COLUMN source_link_id BIGINT UNSIGNED NULL, ADD KEY idx_users_source_link (source_link_id)',
    'SELECT ''users.source_link_id exists'' AS msg');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ── referrals ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS referrals (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    referrer_user_id  BIGINT UNSIGNED NOT NULL,
    invited_user_id   BIGINT UNSIGNED NOT NULL,
    status            ENUM('rewarded','skipped_existing') NOT NULL,
    days_invited      INT             NOT NULL DEFAULT 0,
    days_referrer     INT             NOT NULL DEFAULT 0,
    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ref_invited (invited_user_id),
    KEY idx_ref_referrer (referrer_user_id),
    KEY idx_ref_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── ad_links (аналитические ссылки из админки) ───────────────────────
CREATE TABLE IF NOT EXISTS ad_links (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code        VARCHAR(32)     NOT NULL,
    title       VARCHAR(128)    NOT NULL,
    note        VARCHAR(255)    NULL,
    active      TINYINT(1)      NOT NULL DEFAULT 1,
    clicks      INT             NOT NULL DEFAULT 0,
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ad_link_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ad_link_hits (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    link_id     BIGINT UNSIGNED NOT NULL,
    user_id     BIGINT UNSIGNED NOT NULL,
    is_new_user TINYINT(1)      NOT NULL DEFAULT 0,
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ad_hit_user (link_id, user_id),
    KEY idx_ad_hit_link (link_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─── schema_version = 32 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl2 := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (32)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl2; EXECUTE stmt; DEALLOCATE PREPARE stmt;

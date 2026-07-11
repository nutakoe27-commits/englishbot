-- Миграция 0029: B2B — школы (организации) и участники.
--
-- 1) organizations — школа: пакет мест (seats_total) на срок (valid_until),
--    инвайт-код для подключения учеников, settings TEXT — задел под
--    white-label (логотип/цвета/bot_token), сейчас не используется.
-- 2) org_members — участники школы. role: student занимает место и получает
--    полный доступ (как подписчик), teacher/admin — для кабинета (фаза 2).
--    active=0 — отключённый участник, место освобождается.
--    UNIQUE(org_id, user_id) — один юзер один раз в одной школе.
-- 3) schema_version = 29.
--
-- Применять:
--   mysql -u <user> -p <db> < 0029_organizations.sql
-- Идемпотентно.

CREATE TABLE IF NOT EXISTS organizations (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name          VARCHAR(128)    NOT NULL,
    invite_code   VARCHAR(32)     NOT NULL,
    seats_total   INT             NOT NULL DEFAULT 0,
    valid_until   DATETIME        NULL,
    active        TINYINT(1)      NOT NULL DEFAULT 1,
    contact_email VARCHAR(255)    NULL,
    settings      TEXT            NULL,
    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_org_invite (invite_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS org_members (
    id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    org_id    BIGINT UNSIGNED NOT NULL,
    user_id   BIGINT UNSIGNED NOT NULL,
    role      ENUM('student','teacher','admin') NOT NULL DEFAULT 'student',
    group_id  BIGINT UNSIGNED NULL,
    active    TINYINT(1)      NOT NULL DEFAULT 1,
    joined_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_org_user (org_id, user_id),
    KEY idx_orgmember_user (user_id),
    CONSTRAINT fk_orgmember_org FOREIGN KEY (org_id)
        REFERENCES organizations(id) ON DELETE CASCADE,
    CONSTRAINT fk_orgmember_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─── schema_version = 29 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (29)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

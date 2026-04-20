-- ─────────────────────────────────────────────────────────────────────────
-- EnglishBot — начальная схема БД (миграция 0001)
-- MySQL 5.7+, charset utf8mb4
--
-- Применяется один раз при первой настройке. Идемпотентно — IF NOT EXISTS
-- везде, где это поддерживается.
-- ─────────────────────────────────────────────────────────────────────────

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 1;

-- ─── users ───────────────────────────────────────────────────────────────
-- Один пользователь Telegram = одна строка. tg_id — натуральный PK снаружи.
CREATE TABLE IF NOT EXISTS users (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    tg_id           BIGINT          NOT NULL,
    username        VARCHAR(64)     NULL,           -- @username, может отсутствовать
    first_name      VARCHAR(128)    NULL,
    last_name       VARCHAR(128)    NULL,
    language_code   VARCHAR(8)      NULL,           -- 'ru', 'en', etc.
    -- Подписка
    subscription_until DATETIME     NULL,           -- NULL = нет активной подписки
    -- Напоминания
    reminder_time   TIME            NOT NULL DEFAULT '19:00:00',
    reminder_enabled TINYINT(1)     NOT NULL DEFAULT 1,
    -- Сервис
    is_blocked      TINYINT(1)      NOT NULL DEFAULT 0,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_tg_id (tg_id),
    KEY idx_users_subscription_until (subscription_until),
    KEY idx_users_reminder (reminder_enabled, reminder_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─── sessions ────────────────────────────────────────────────────────────
-- Каждое подключение mini app к WebSocket. used_seconds списывается с
-- дневного лимита бесплатных юзеров.
CREATE TABLE IF NOT EXISTS sessions (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id         BIGINT UNSIGNED NOT NULL,
    started_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at        DATETIME        NULL,
    used_seconds    INT UNSIGNED    NOT NULL DEFAULT 0,
    mode            ENUM('voice','chat') NOT NULL DEFAULT 'voice',
    -- Снимок настроек на момент сессии (полезно для аналитики)
    level           VARCHAR(8)      NULL,
    role            VARCHAR(64)     NULL,
    PRIMARY KEY (id),
    KEY idx_sessions_user_started (user_id, started_at),
    CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─── daily_usage ─────────────────────────────────────────────────────────
-- Денормализованный счётчик: сколько секунд юзер использовал за конкретный
-- день (по МСК). Обновляется на каждый close WS. Нужен, чтобы быстро
-- проверить лимит без агрегации sessions.
CREATE TABLE IF NOT EXISTS daily_usage (
    user_id         BIGINT UNSIGNED NOT NULL,
    usage_date      DATE            NOT NULL,       -- день в Europe/Moscow
    used_seconds    INT UNSIGNED    NOT NULL DEFAULT 0,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, usage_date),
    CONSTRAINT fk_daily_usage_user FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─── payments ────────────────────────────────────────────────────────────
-- История платежей. Пока заглушка, но структура готова под ЮKassa.
CREATE TABLE IF NOT EXISTS payments (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id         BIGINT UNSIGNED NOT NULL,
    amount_rub      DECIMAL(10,2)   NOT NULL,
    plan            ENUM('monthly','yearly','gift','admin_grant') NOT NULL,
    status          ENUM('pending','succeeded','canceled','refunded')
                                    NOT NULL DEFAULT 'pending',
    -- ЮKassa: id платежа в их системе (уникальный)
    provider_payment_id VARCHAR(64) NULL,
    -- Сколько дней даёт этот платёж (для аудита и ручных грантов)
    days_granted    INT UNSIGNED    NOT NULL,
    -- Кто выдал, если admin_grant/gift (Telegram ID администратора)
    granted_by_tg_id BIGINT         NULL,
    notes           VARCHAR(500)    NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_payments_provider_id (provider_payment_id),
    KEY idx_payments_user (user_id, created_at),
    KEY idx_payments_status (status),
    CONSTRAINT fk_payments_user FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─── settings_kv ─────────────────────────────────────────────────────────
-- Глобальные настройки приложения, управляемые из админ-панели.
-- Например: maintenance_mode, maintenance_message, free_minutes_per_day.
CREATE TABLE IF NOT EXISTS settings_kv (
    `key`           VARCHAR(64)     NOT NULL,
    value           TEXT            NOT NULL,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Дефолтные значения. INSERT IGNORE — не перетирает существующие.
INSERT IGNORE INTO settings_kv (`key`, value) VALUES
    ('maintenance_mode',        '0'),
    ('maintenance_message',     'Бот временно недоступен — ведутся технические работы. Возвращайся через 10–15 минут.'),
    ('free_seconds_per_day',    '600'),  -- 10 минут
    ('subscription_price_monthly_rub', '699'),
    ('subscription_price_yearly_rub',  '4990');


-- ─── schema_version ──────────────────────────────────────────────────────
-- Простейший трекер миграций. Без Alembic для MVP — каждая новая миграция
-- 0002_*.sql / 0003_*.sql добавит свою строку.
CREATE TABLE IF NOT EXISTS schema_version (
    version         INT UNSIGNED    NOT NULL,
    applied_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO schema_version (version) VALUES (1);

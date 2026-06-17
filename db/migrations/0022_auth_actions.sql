-- Миграция 0022: auth_actions — одноразовые токены для Telegram deep-link
-- авторизации и подтверждения чувствительных действий (unlink email/password).
--
-- Что добавляет:
--   Таблицу auth_actions. Один действие = одна строка (PK=token).
--   Жизненный цикл: pending → done|cancelled|failed (final).
--   Хранит initiator (user_id) и resulting_user_id (например, primary после
--   merge на linking), которые backend отдаст сайту при /api/auth/poll.
--
-- Зачем:
--   1) Сайт сейчас использует Telegram Login Widget (oauth.telegram.org с
--      номером телефона) — путает юзеров. Заменяем на deep-link
--      t.me/<bot>?start=<token>, который открывает реальное TG-приложение.
--   2) Отвязка email/password должна подтверждаться в боте (inline-кнопка)
--      — действие может уничтожить единственный способ входа.
--
-- Применять:
--   mysql -u <user> -p <db> < 0022_auth_actions.sql
--
-- Идемпотентно: создаёт таблицу IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS auth_actions (
  token            VARCHAR(40)     NOT NULL,
  action           VARCHAR(32)     NOT NULL
                   COMMENT 'login_telegram | link_telegram | unlink_native',
  user_id          BIGINT UNSIGNED NULL
                   COMMENT 'Инициатор (login: NULL — юзер ещё не создан)',
  resulting_user_id BIGINT UNSIGNED NULL
                   COMMENT 'Куда выдать JWT после применения (login/link primary)',
  status           VARCHAR(16)     NOT NULL DEFAULT 'pending'
                   COMMENT 'pending | done | cancelled | failed',
  expires_at       DATETIME        NOT NULL,
  consumed_at      DATETIME        NULL,
  created_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (token),
  KEY idx_actions_user (user_id),
  KEY idx_actions_status_exp (status, expires_at),
  CONSTRAINT fk_actions_user FOREIGN KEY (user_id)
    REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─── schema_version = 22 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (22)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

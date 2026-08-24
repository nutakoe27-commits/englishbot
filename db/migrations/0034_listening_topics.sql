-- Миграция 0034: история тем подкастов — борьба с повторами.
--
-- Категория («история», «наука») задаёт лишь широкое поле, а конкретную
-- тему выбирает модель. Промпт для одной категории был байт-в-байт
-- одинаковым при каждой генерации, поэтому модель раз за разом сваливалась
-- в одни и те же сюжеты. Теперь запоминаем, что юзер уже слушал, и явно
-- просим этих тем избегать.
--
-- Держим отдельной таблицей, а не в sessions.role: там лежит категория, и
-- она же уходит в users.last_session_role для выдачи role-квестов — ломать
-- эту семантику ради истории тем нельзя.
--
-- Применять:
--   mysql -u <user> -p <db> < 0034_listening_topics.sql
-- Идемпотентно.

CREATE TABLE IF NOT EXISTS listening_topics (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id    BIGINT UNSIGNED NOT NULL,
    category   VARCHAR(32)     NOT NULL,
    topic      VARCHAR(160)    NOT NULL,
    created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_lt_user (user_id, created_at),
    KEY idx_lt_user_cat (user_id, category, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─── schema_version = 34 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (34)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

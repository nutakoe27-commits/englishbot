-- Миграция 0005: users.last_session_role
--
-- Зачем: assign_daily_quest сейчас выдаёт role-quest без оглядки на роль,
-- в которой юзер играет в Mini App. В каталоге 6 role-квестов с фиксированной
-- ролью (barista/interviewer/doctor/...). При несовпадении verify_session
-- никогда не проходит → бонус не начисляется. Фикс: фильтровать role-квесты
-- по последней роли юзера. Хранить её в users.last_session_role.
--
-- Заполняется после каждой осмысленной сессии (см. backend/app/voice.py).
--
-- Как применять:
--   mysql -u <user> -p <db> < 0005_user_last_session_role.sql
--
-- Идемпотентно: проверяет существование колонки.

SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users' AND COLUMN_NAME = 'last_session_role'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE users ADD COLUMN last_session_role VARCHAR(64) NULL COMMENT ''Роль из последней успешной сессии — для умной выдачи role-quest''',
    'SELECT ''last_session_role already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- schema_version (если есть)
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (5)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

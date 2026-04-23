-- Миграция 0003: поле inline_message_id в battles.
-- Требуется, чтобы бот мог редактировать исходное inline-сообщение вызова
-- (в inline-запросе нет chat_id/message_id — только inline_message_id).
--
-- Как применять:
--   mysql -u megotim4y2 -p megotim4y2 < 0003_battle_inline_msg.sql

-- MySQL 8 не поддерживает ADD COLUMN IF NOT EXISTS — через information_schema
SET @col_exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'battles'
      AND COLUMN_NAME = 'inline_message_id'
);
SET @ddl := IF(
    @col_exists = 0,
    'ALTER TABLE battles ADD COLUMN inline_message_id VARCHAR(128) NULL COMMENT ''id inline-сообщения для edit_message_text (Telegram Bot API)''',
    'SELECT ''inline_message_id already exists'' AS msg'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- chat_id теперь может быть NULL для inline-вызовов (MODIFY идемпотентен)
ALTER TABLE battles
    MODIFY COLUMN chat_id BIGINT NULL COMMENT 'Чат (если вызов из чата), NULL для inline';

-- schema_version может не существовать в ранних базах — не падаем
SET @tbl_exists := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl2 := IF(
    @tbl_exists = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (3)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt2 FROM @ddl2;
EXECUTE stmt2;
DEALLOCATE PREPARE stmt2;

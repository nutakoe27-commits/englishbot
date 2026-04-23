-- Миграция 0003: поле inline_message_id в battles.
-- Требуется, чтобы бот мог редактировать исходное inline-сообщение вызова
-- (в inline-запросе нет chat_id/message_id — только inline_message_id).
--
-- Как применять:
--   mysql -u megotim4y2 -p megotim4y2 < 0003_battle_inline_msg.sql

ALTER TABLE battles
    ADD COLUMN IF NOT EXISTS inline_message_id VARCHAR(128) NULL
        COMMENT 'id inline-сообщения для edit_message_text (Telegram Bot API)';

-- chat_id теперь может быть 0 для inline-вызовов (не привязаны к чату).
-- Делаем его NULL-способным для единообразия.
ALTER TABLE battles
    MODIFY COLUMN chat_id BIGINT NULL COMMENT 'Чат (если вызов из чата), NULL для inline';

INSERT IGNORE INTO schema_version (version) VALUES (3);

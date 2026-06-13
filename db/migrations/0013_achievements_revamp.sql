-- Миграция 0013: пересборка достижений
--
-- Что делает:
--   1) Удаляет битвовую медаль 'battle_first' у всех юзеров (сама медаль
--      убрана из каталога вместе с Battle Mode в #98).
--   2) Сбрасывает флаг achievements_backfilled — backend на следующем старте
--      пересчитает медали по новым правилам и накинет новые
--      (listening_first/10, grammar_first/10/25/all, polyglot) тем, кто их
--      заслужил по уже накопленной истории. Без push'ей в TG, чтобы юзеры
--      не получили burst уведомлений.
--
-- Применять:
--   mysql -u <user> -p <db> < 0013_achievements_revamp.sql
--
-- Идемпотентно.

DELETE FROM user_achievements WHERE achievement_key = 'battle_first';

DELETE FROM settings_kv WHERE `key` = 'achievements_backfilled';


-- ─── schema_version ──────────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (13)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

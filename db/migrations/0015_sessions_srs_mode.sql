-- Миграция 0015: sessions.mode += 'srs'
--
-- Зачем: добавляем четвёртый режим тренировки — SRS («📚 Слова»,
-- интервальное повторение). Сессия пишется в ту же таблицу `sessions`
-- с mode='srs'. По аналогии с миграциями 0008 (listening) и 0010 (grammar).
--
-- Эту правку нужно было сделать сразу в 0014, но без неё SRS падает
-- с DataError «Data truncated for column 'mode'» на первом open_session.
--
-- Как применять:
--   mysql -u <user> -p <db> < 0015_sessions_srs_mode.sql
--
-- Идемпотентно: MODIFY COLUMN до полного enum-набора применяется безопасно
-- даже если 'srs' уже присутствует.

ALTER TABLE sessions
    MODIFY COLUMN mode ENUM('voice','chat','listening','grammar','srs')
                  NOT NULL DEFAULT 'voice';


-- ─── schema_version ──────────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (15)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

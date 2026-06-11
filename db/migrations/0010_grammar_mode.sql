-- Миграция 0010: sessions.mode += 'grammar'
--
-- Зачем: добавляем третий режим тренировки — Grammar (текстовый тренажёр
-- грамматических упражнений). Сессия пишется в ту же таблицу `sessions` с
-- mode='grammar'. По аналогии с миграцией 0008 (listening).
--
-- Как применять:
--   mysql -u <user> -p <db> < 0010_grammar_mode.sql
--
-- Идемпотентно: MODIFY COLUMN до полного enum-набора применяется безопасно
-- даже если 'grammar' уже присутствует.

ALTER TABLE sessions
    MODIFY COLUMN mode ENUM('voice','chat','listening','grammar') NOT NULL DEFAULT 'voice';


-- ─── schema_version ──────────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (10)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Миграция 0016: посекционные лимиты — speaking_seconds + free-квоты.
--
-- Что добавляет:
--   1. daily_usage.speaking_seconds — отдельный дневной счётчик времени
--      ТОЛЬКО говорения (voice/chat). Нужен, чтобы лимит говорения (10 мин)
--      не «съедался» временем слушания/грамматики, которые пишут в общий
--      used_seconds (аналитика). Гейт говорения переключается на этот столбец.
--   2. settings_kv: free_listening_per_day=1, free_grammar_per_day=1 —
--      бесплатные дневные квоты по секциям (ключ free_seconds_per_day для
--      говорения уже есть, дефолт 600). Лимиты слушания/грамматики считаются
--      по таблице sessions (COUNT за сегодня), здесь — только пороги.
--
-- Зачем:
--   Включение платной модели: free-юзер получает 10 мин говорения,
--   1 подкаст и 1 урок грамматики в день; слова — бесплатно. Дальше — пейволл.
--
-- Применять:
--   mysql -u <user> -p <db> < 0016_daily_speaking_seconds.sql
--
-- Идемпотентно.

-- ─── 1. daily_usage.speaking_seconds ─────────────────────────────────
SET @col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'daily_usage' AND COLUMN_NAME = 'speaking_seconds'
);
SET @ddl := IF(@col = 0,
    'ALTER TABLE daily_usage ADD COLUMN speaking_seconds INT NOT NULL DEFAULT 0 COMMENT ''Время только говорения (voice/chat) за день — для лимита''',
    'SELECT ''speaking_seconds already exists'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── 2. free-квоты в settings_kv ─────────────────────────────────────
-- INSERT IGNORE — не перетираем, если владелец уже выставил свои значения.
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'settings_kv'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO settings_kv (`key`, `value`) VALUES
        (''free_listening_per_day'', ''1''),
        (''free_grammar_per_day'', ''1'')',
    'SELECT ''settings_kv absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ─── schema_version = 16 ─────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (16)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Миграция 0012: уровень A1 в Grammar Learn
--
-- 1) Расширяем enum grammar_topics.level до A1..C1.
-- 2) Добавляем 10 тем A1 для совсем новичков.
--
-- Теория уроков теперь живёт в backend/app/grammar_lessons.py (рукописная),
-- LLM генерирует только упражнения — каждому юзеру свежие. Таблица
-- grammar_lesson_cache больше не используется (оставлена на случай отката).
--
-- Как применять:
--   mysql -u <user> -p <db> < 0012_grammar_a1.sql
--
-- Идемпотентно.

ALTER TABLE grammar_topics
    MODIFY COLUMN level ENUM('A1','A2','B1','B2','C1') NOT NULL;

INSERT IGNORE INTO grammar_topics (`key`, level, sort_order, title_ru, category) VALUES
  ('a1_to_be',               'A1', 1,  'Глагол to be: am / is / are',                  'other'),
  ('a1_pronouns',            'A1', 2,  'Местоимения: I / my, you / your',              'other'),
  ('a1_this_that',           'A1', 3,  'This / that / these / those',                  'other'),
  ('a1_have_got',            'A1', 4,  'Have got — «у меня есть»',                     'other'),
  ('a1_there_is',            'A1', 5,  'There is / there are',                         'other'),
  ('a1_present_simple_i',    'A1', 6,  'Present Simple: I / you / we / they',          'tense'),
  ('a1_present_simple_s',    'A1', 7,  'Present Simple: he / she / it (-s)',           'tense'),
  ('a1_questions_negatives', 'A1', 8,  'Вопросы и отрицания: do / does',               'tense'),
  ('a1_articles_a_an',       'A1', 9,  'Артикль a / an — первое знакомство',           'article'),
  ('a1_imperatives',         'A1', 10, 'Просьбы и команды: Sit down! / Let''s go!',    'other');


-- ─── schema_version ──────────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (12)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

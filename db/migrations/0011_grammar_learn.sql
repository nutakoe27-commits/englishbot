-- Миграция 0011: Grammar Learn — трек «Учить правила»
--
-- Три таблицы:
--   grammar_topics        — статический каталог тем по уровням (по образцу quests_catalog)
--   grammar_lesson_cache  — кеш LLM-сгенерированных уроков (одинаковы для всех юзеров)
--   user_grammar_progress — прогресс юзера по темам (порог прохождения 70%)
--
-- Управление контентом:
--   - поправить теорию урока: UPDATE grammar_lesson_cache SET theory='...' WHERE topic_key='...';
--   - перегенерировать урок:   DELETE FROM grammar_lesson_cache WHERE topic_key='...';
--   - выключить тему:          UPDATE grammar_topics SET is_active=FALSE WHERE `key`='...';
--
-- Как применять:
--   mysql -u <user> -p <db> < 0011_grammar_learn.sql
--
-- Идемпотентно: CREATE TABLE IF NOT EXISTS + INSERT IGNORE.

CREATE TABLE IF NOT EXISTS grammar_topics (
  `key`      VARCHAR(64) PRIMARY KEY,
  level      ENUM('A2','B1','B2','C1') NOT NULL,
  sort_order INT NOT NULL,
  title_ru   VARCHAR(200) NOT NULL,
  category   ENUM('article','tense','preposition','word_choice','phrasal','other') NOT NULL,
  is_active  BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_topics_level_order (level, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Каталог грамматических тем (Grammar Learn)';

CREATE TABLE IF NOT EXISTS grammar_lesson_cache (
  topic_key    VARCHAR(64) PRIMARY KEY,
  theory       TEXT NOT NULL,
  exercises    JSON NOT NULL,
  generated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_lesson_topic FOREIGN KEY (topic_key)
    REFERENCES grammar_topics(`key`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Кеш LLM-уроков — общий для всех юзеров';

CREATE TABLE IF NOT EXISTS user_grammar_progress (
  user_id      BIGINT NOT NULL,
  topic_key    VARCHAR(64) NOT NULL,
  completed_at DATETIME NULL,
  best_score   INT NOT NULL DEFAULT 0,
  attempts     INT NOT NULL DEFAULT 0,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, topic_key),
  CONSTRAINT fk_ugp_user  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_ugp_topic FOREIGN KEY (topic_key) REFERENCES grammar_topics(`key`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Прогресс юзера по грамматическим темам';


-- ─── Каталог тем ──────────────────────────────────────────────────────
-- A2: 10 тем
INSERT IGNORE INTO grammar_topics (`key`, level, sort_order, title_ru, category) VALUES
  ('a2_present_simple',      'A2', 1,  'Present Simple — настоящее простое',           'tense'),
  ('a2_present_continuous',  'A2', 2,  'Present Continuous — настоящее длительное',    'tense'),
  ('a2_past_simple',         'A2', 3,  'Past Simple — прошедшее простое',              'tense'),
  ('a2_articles_basics',     'A2', 4,  'Артикли a / an / the — база',                  'article'),
  ('a2_plurals_countability','A2', 5,  'Множественное число и исчисляемость',          'other'),
  ('a2_prepositions_place',  'A2', 6,  'Предлоги места: in / on / at / under / next to','preposition'),
  ('a2_prepositions_time',   'A2', 7,  'Предлоги времени: in / on / at',               'preposition'),
  ('a2_can_cant',            'A2', 8,  'Can / can''t — умения и разрешения',           'other'),
  ('a2_going_to',            'A2', 9,  'Going to — планы и намерения',                 'tense'),
  ('a2_comparatives',        'A2', 10, 'Степени сравнения прилагательных',             'other');

-- B1: 12 тем
INSERT IGNORE INTO grammar_topics (`key`, level, sort_order, title_ru, category) VALUES
  ('b1_present_perfect_vs_past','B1', 1,  'Present Perfect vs Past Simple',            'tense'),
  ('b1_past_continuous',     'B1', 2,  'Past Continuous — прошедшее длительное',       'tense'),
  ('b1_will_vs_going_to',    'B1', 3,  'Will vs Going to — будущее время',             'tense'),
  ('b1_first_conditional',   'B1', 4,  'First Conditional — реальное условие',         'other'),
  ('b1_second_conditional',  'B1', 5,  'Second Conditional — нереальное условие',      'other'),
  ('b1_modals_obligation',   'B1', 6,  'Must / have to / should — долженствование',    'other'),
  ('b1_passive_basics',      'B1', 7,  'Пассивный залог — база',                       'other'),
  ('b1_relative_clauses',    'B1', 8,  'Относительные придаточные: who / which / that','other'),
  ('b1_used_to',             'B1', 9,  'Used to — прошлые привычки',                   'tense'),
  ('b1_gerund_vs_infinitive','B1', 10, 'Герундий vs инфинитив: enjoy doing / want to do','word_choice'),
  ('b1_quantifiers',         'B1', 11, 'Some / any / much / many / a few / a little',  'other'),
  ('b1_phrasal_verbs_intro', 'B1', 12, 'Фразовые глаголы — введение',                  'phrasal');

-- B2: 10 тем
INSERT IGNORE INTO grammar_topics (`key`, level, sort_order, title_ru, category) VALUES
  ('b2_present_perfect_cont','B2', 1,  'Present Perfect Continuous',                   'tense'),
  ('b2_third_conditional',   'B2', 2,  'Third Conditional — нереальное прошлое',       'other'),
  ('b2_mixed_conditionals',  'B2', 3,  'Mixed Conditionals — смешанные условные',      'other'),
  ('b2_passive_advanced',    'B2', 4,  'Пассив продвинутый: have something done',      'other'),
  ('b2_reported_speech',     'B2', 5,  'Reported Speech — косвенная речь',             'tense'),
  ('b2_wish_if_only',        'B2', 6,  'Wish / If only — сожаления',                   'other'),
  ('b2_modals_deduction',    'B2', 7,  'Must have / can''t have — дедукция о прошлом', 'other'),
  ('b2_causative',           'B2', 8,  'Causative: have / get something done',         'other'),
  ('b2_linking_words',       'B2', 9,  'Linking words: however / although / despite',  'word_choice'),
  ('b2_phrasal_separable',   'B2', 10, 'Фразовые глаголы: separable / inseparable',    'phrasal');

-- C1: 8 тем
INSERT IGNORE INTO grammar_topics (`key`, level, sort_order, title_ru, category) VALUES
  ('c1_inversion',           'C1', 1,  'Инверсия: Never have I seen…',                 'other'),
  ('c1_cleft_sentences',     'C1', 2,  'Cleft sentences: It was X that…',              'other'),
  ('c1_subjunctive',         'C1', 3,  'Subjunctive: I suggest he be…',                'other'),
  ('c1_advanced_modality',   'C1', 4,  'Продвинутая модальность: bound to / dare / needn''t have','other'),
  ('c1_ellipsis',            'C1', 5,  'Эллипсис и замещение: so do I / if not',       'other'),
  ('c1_discourse_markers',   'C1', 6,  'Discourse markers: mind you / as it were',     'word_choice'),
  ('c1_collocations',        'C1', 7,  'Продвинутые коллокации',                       'word_choice'),
  ('c1_nominalisation',      'C1', 8,  'Номинализация в формальном письме',            'other');


-- ─── schema_version ──────────────────────────────────────────────────
SET @tbl := (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schema_version'
);
SET @ddl := IF(@tbl = 1,
    'INSERT IGNORE INTO schema_version (version) VALUES (11)',
    'SELECT ''schema_version table absent — skipped'' AS msg'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

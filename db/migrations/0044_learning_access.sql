-- Isolate lesson practice from standalone quotas without changing recorded minutes.
SET @ddl = IF(EXISTS(SELECT 1 FROM information_schema.columns
 WHERE table_schema=DATABASE() AND table_name='sessions' AND column_name='learning_lesson_id'),
 'SELECT 1', 'ALTER TABLE sessions ADD COLUMN learning_lesson_id VARCHAR(36) NULL');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl = IF(EXISTS(SELECT 1 FROM information_schema.statistics
 WHERE table_schema=DATABASE() AND table_name='sessions' AND index_name='ix_sessions_learning_lesson_id'),
 'SELECT 1', 'CREATE INDEX ix_sessions_learning_lesson_id ON sessions(learning_lesson_id)');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Recover bindings for lessons already started before this release.
UPDATE sessions s JOIN learning_lessons l ON l.session_id=s.id AND l.user_id=s.user_id
 SET s.learning_lesson_id=l.id WHERE s.learning_lesson_id IS NULL;
UPDATE sessions s JOIN learning_lessons l ON l.user_id=s.user_id
 AND JSON_UNQUOTE(JSON_EXTRACT(s.grammar_result,'$._learning_lesson_id'))=l.id
 SET s.learning_lesson_id=l.id WHERE s.learning_lesson_id IS NULL;
UPDATE learning_lessons l JOIN sessions s ON s.user_id=l.user_id
 AND CAST(s.id AS CHAR)=JSON_UNQUOTE(JSON_EXTRACT(l.answers,'$._modules.grammar.session_id'))
 SET s.learning_lesson_id=l.id WHERE s.learning_lesson_id IS NULL;
UPDATE learning_lessons l JOIN sessions s ON s.user_id=l.user_id
 AND CAST(s.id AS CHAR)=JSON_UNQUOTE(JSON_EXTRACT(l.answers,'$._modules.mistakes.session_id'))
 SET s.learning_lesson_id=l.id WHERE s.learning_lesson_id IS NULL;
UPDATE learning_lessons l JOIN sessions s ON s.user_id=l.user_id
 AND CAST(s.id AS CHAR)=JSON_UNQUOTE(JSON_EXTRACT(l.answers,'$._modules.words.session_id'))
 SET s.learning_lesson_id=l.id WHERE s.learning_lesson_id IS NULL;
UPDATE learning_lessons l JOIN sessions s ON s.user_id=l.user_id
 AND CAST(s.id AS CHAR)=JSON_UNQUOTE(JSON_EXTRACT(l.answers,'$._modules.listening.podcast.session_id'))
 SET s.learning_lesson_id=l.id WHERE s.learning_lesson_id IS NULL;
UPDATE learning_lessons l JOIN sessions s ON s.user_id=l.user_id
 AND CAST(s.id AS CHAR)=JSON_UNQUOTE(JSON_EXTRACT(l.answers,'$._modules.conversation.session_id'))
 SET s.learning_lesson_id=l.id WHERE s.learning_lesson_id IS NULL;

INSERT IGNORE INTO settings_kv (`key`,value) VALUES ('free_lesson_conversation_seconds','300');
INSERT IGNORE INTO schema_version(version) VALUES (44);

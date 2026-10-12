-- Grammar: durable activity/completion state and idempotent activation.
-- Generated exercises do not create a sessions row until practice starts.
SET @ddl := IF((SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sessions' AND COLUMN_NAME='grammar_status')=0,
 'ALTER TABLE sessions ADD COLUMN grammar_status VARCHAR(16) NULL', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl := IF((SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sessions' AND COLUMN_NAME='grammar_last_seen_at')=0,
 'ALTER TABLE sessions ADD COLUMN grammar_last_seen_at DATETIME NULL', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl := IF((SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sessions' AND COLUMN_NAME='grammar_result')=0,
 'ALTER TABLE sessions ADD COLUMN grammar_result JSON NULL', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl := IF((SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sessions' AND COLUMN_NAME='grammar_preparation_id')=0,
 'ALTER TABLE sessions ADD COLUMN grammar_preparation_id VARCHAR(64) NULL', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl := IF((SELECT COUNT(*) FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sessions' AND INDEX_NAME='uq_grammar_preparation')=0,
 'ALTER TABLE sessions ADD UNIQUE INDEX uq_grammar_preparation (grammar_preparation_id)', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
UPDATE sessions SET grammar_status='completed'
 WHERE mode='grammar' AND ended_at IS NOT NULL AND grammar_status IS NULL;
INSERT IGNORE INTO schema_version (version) VALUES (42);

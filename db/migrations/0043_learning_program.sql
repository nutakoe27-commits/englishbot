-- Durable learning program, lessons, mastery evidence and checkpoints.
ALTER TABLE sessions MODIFY COLUMN mode ENUM('voice','chat','listening','grammar','srs','lesson') NOT NULL DEFAULT 'voice';
CREATE TABLE IF NOT EXISTS learning_profiles (
 user_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
 goal VARCHAR(16) NOT NULL, level VARCHAR(4) NOT NULL,
 completed_count INT NOT NULL DEFAULT 0, checkpoint_count INT NOT NULL DEFAULT 0,
 created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS learning_lessons (
 id VARCHAR(36) NOT NULL PRIMARY KEY, user_id BIGINT UNSIGNED NOT NULL,
 ordinal INT NOT NULL, level VARCHAR(4) NOT NULL, topic_key VARCHAR(64) NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'prepared', step_index INT NOT NULL DEFAULT 0,
 content JSON NOT NULL, answers JSON NOT NULL, conversation JSON NOT NULL,
 used_seconds INT NOT NULL DEFAULT 0, session_id BIGINT UNSIGNED NULL,
 generation_status VARCHAR(16) NOT NULL DEFAULT 'pending', generation_attempts INT NOT NULL DEFAULT 0,
 generation_lease DATETIME NULL, instance_id VARCHAR(36) NULL,
 created_at DATETIME NOT NULL, last_seen_at DATETIME NULL, completed_at DATETIME NULL,
 UNIQUE KEY uq_learning_ordinal(user_id,ordinal),
 KEY idx_learning_generation(generation_status,generation_lease),
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS learning_skills (
 user_id BIGINT UNSIGNED NOT NULL, skill_key VARCHAR(64) NOT NULL,
 attempts INT NOT NULL DEFAULT 0, correct_count INT NOT NULL DEFAULT 0,
 evidence JSON NOT NULL, updated_at DATETIME NOT NULL,
 PRIMARY KEY(user_id,skill_key), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS learning_checkpoints (
 id VARCHAR(36) NOT NULL PRIMARY KEY, user_id BIGINT UNSIGNED NOT NULL,
 after_count INT NOT NULL, level_before VARCHAR(4) NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'active', questions JSON NOT NULL,
 answers JSON NOT NULL, result JSON NULL,
 created_at DATETIME NOT NULL, completed_at DATETIME NULL,
 UNIQUE KEY uq_learning_checkpoint(user_id,after_count),
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
INSERT IGNORE INTO settings_kv (`key`,value) VALUES ('free_lessons_per_day','1');
INSERT IGNORE INTO schema_version(version) VALUES(43);

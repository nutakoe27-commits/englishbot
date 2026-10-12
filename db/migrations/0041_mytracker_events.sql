-- Доставка мобильных событий MyTracker независимо от входа и оплаты.
-- Применить до обновления backend. Секреты в таблицу не записываются.
CREATE TABLE IF NOT EXISTS mytracker_events (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id         BIGINT UNSIGNED NOT NULL,
    dedup_key       VARCHAR(191) NOT NULL,
    method          VARCHAR(32) NOT NULL,
    payload         JSON NOT NULL,
    attempts        INT UNSIGNED NOT NULL DEFAULT 0,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    next_attempt_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at         DATETIME NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_mt_event_dedup (dedup_key),
    KEY idx_mt_event_pending (sent_at, next_attempt_at),
    KEY idx_mt_event_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO schema_version (version) VALUES (41);

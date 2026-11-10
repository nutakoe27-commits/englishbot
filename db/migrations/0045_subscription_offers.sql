-- Account-wide survey suppression and a rolling 24-hour popup cooldown.
CREATE TABLE IF NOT EXISTS subscription_offer_state (
  user_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  first_shown_at DATETIME(6) NOT NULL,
  last_shown_at DATETIME(6) NOT NULL,
  reason_code VARCHAR(40) NULL,
  answered_at DATETIME(6) NULL,
  answer_impression_id VARCHAR(36) NULL,
  INDEX ix_subscription_offer_state_answered_at (answered_at),
  CONSTRAINT fk_subscription_offer_state_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS subscription_offer_impressions (
  id VARCHAR(36) NOT NULL PRIMARY KEY,
  user_id BIGINT UNSIGNED NOT NULL,
  audience VARCHAR(24) NOT NULL,
  payment_cursor BIGINT UNSIGNED NOT NULL DEFAULT 0,
  shown_at DATETIME(6) NOT NULL,
  declined_at DATETIME(6) NULL,
  closed_at DATETIME(6) NULL,
  clicked_at DATETIME(6) NULL,
  initial_clicked_at DATETIME(6) NULL,
  followup_clicked_at DATETIME(6) NULL,
  INDEX ix_subscription_offer_impressions_user_id (user_id),
  INDEX ix_subscription_offer_impressions_shown_at (shown_at),
  CONSTRAINT fk_subscription_offer_impression_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Preserve any explicit changes made by an administrator to an existing code.
INSERT IGNORE INTO promo_codes (code, discount_percent, active, used_count, created_at)
VALUES ('SALE20', 20, 1, 0, UTC_TIMESTAMP());
INSERT IGNORE INTO promo_codes (code, discount_percent, active, used_count, created_at)
VALUES ('OLD50', 50, 1, 0, UTC_TIMESTAMP());

INSERT IGNORE INTO schema_version(version) VALUES (45);

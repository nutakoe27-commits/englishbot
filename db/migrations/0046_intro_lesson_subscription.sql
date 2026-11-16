-- Preserve legacy payments, answers and impressions. Re-runnable on MySQL 8.
SET @ddl = IF(EXISTS(SELECT 1 FROM information_schema.columns
 WHERE table_schema=DATABASE() AND table_name='payments' AND column_name='checkout_data'),
 'SELECT 1', 'ALTER TABLE payments ADD COLUMN checkout_data JSON NULL');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl = IF(EXISTS(SELECT 1 FROM information_schema.columns
 WHERE table_schema=DATABASE() AND table_name='subscription_offer_impressions' AND column_name='campaign'),
 'SELECT 1', 'ALTER TABLE subscription_offer_impressions ADD COLUMN campaign VARCHAR(32) NOT NULL DEFAULT ''legacy_discounts''');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl = IF(EXISTS(SELECT 1 FROM information_schema.columns
 WHERE table_schema=DATABASE() AND table_name='recurring_subscriptions' AND column_name='charge_before_hours'),
 'SELECT 1', 'ALTER TABLE recurring_subscriptions ADD COLUMN charge_before_hours INT NULL');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl = IF(EXISTS(SELECT 1 FROM information_schema.columns
 WHERE table_schema=DATABASE() AND table_name='recurring_subscriptions' AND column_name='setup_payment_id'),
 'SELECT 1', 'ALTER TABLE recurring_subscriptions ADD COLUMN setup_payment_id BIGINT NULL');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
UPDATE promo_codes SET active=0 WHERE code IN ('SALE20','OLD50');
INSERT IGNORE INTO schema_version(version) VALUES (46);

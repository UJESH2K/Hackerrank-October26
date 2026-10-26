-- Ghost Data Still Exists: point-in-time database snapshot
-- Captured at: 2026-09-20T09:00:00Z
-- Database topology: primary_only

BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT,
    full_name TEXT, email TEXT, phone TEXT, email_hash TEXT,
    account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO snapshot_scope VALUES ('account_service', '2026-09-20T09:00:00Z');
COMMIT;

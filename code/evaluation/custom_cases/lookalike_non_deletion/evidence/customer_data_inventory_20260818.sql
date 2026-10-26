-- Ghost Data Still Exists: point-in-time database snapshot
-- Dialect: SQLite-compatible SQL
-- Captured at: 2026-08-18T10:00:00Z
-- Database topology: primary_only

BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT,
    full_name TEXT, email TEXT, phone TEXT, email_hash TEXT,
    account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_search_documents (resource_id TEXT PRIMARY KEY, customer_reference TEXT,
    display_name TEXT, email TEXT, phone TEXT, email_hash TEXT,
    index_name TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO snapshot_scope VALUES ('account_service', '2026-08-18T10:00:00Z');
INSERT INTO snapshot_scope VALUES ('search_service', '2026-08-18T10:00:00Z');
INSERT INTO customer_profiles VALUES ('account_401', NULL, NULL,
    'alice.monroe@example.com', NULL, NULL, 'ACTIVE', '2026-08-18T09:40:00Z');
INSERT INTO customer_profiles VALUES ('account_402', NULL, NULL,
    'a.monroe@example.com', NULL, NULL, 'ACTIVE', '2026-08-18T09:41:00Z');
INSERT INTO customer_search_documents VALUES ('search_401', NULL, NULL,
    'alice.monroe@example.com', NULL, NULL, 'customer_directory_v3',
    '2026-08-18T09:42:00Z');
INSERT INTO customer_search_documents VALUES ('search_402', NULL, NULL,
    'a.monroe@example.com', NULL, NULL, 'customer_directory_v3',
    '2026-08-18T09:43:00Z');
COMMIT;

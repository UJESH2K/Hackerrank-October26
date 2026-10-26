-- Ghost Data Still Exists: point-in-time database snapshot
-- Dialect: SQLite-compatible SQL
-- Captured at: 2026-05-10T09:00:00Z
-- Database topology: primary_only

PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT,
    full_name TEXT, email TEXT, phone TEXT, email_hash TEXT,
    account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_search_documents (resource_id TEXT PRIMARY KEY, customer_reference TEXT,
    display_name TEXT, email TEXT, phone TEXT, email_hash TEXT,
    index_name TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_analytics_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT,
    email TEXT, phone TEXT, email_hash TEXT,
    audience_cohort TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO snapshot_scope VALUES ('account_service', '2026-05-10T09:00:00Z');
INSERT INTO snapshot_scope VALUES ('search_service', '2026-05-10T09:00:00Z');
INSERT INTO snapshot_scope VALUES ('analytics_platform', '2026-05-10T09:00:00Z');
INSERT INTO customer_profiles VALUES ('account_101', NULL, NULL,
    'rosa.delgado.1998@oldmail.example', NULL, NULL, 'ACTIVE', '2026-05-10T08:40:00Z');
INSERT INTO customer_search_documents VALUES ('search_101', NULL, NULL,
    'rosa.delgado@midmail.example', NULL, NULL, 'customer_directory_v3',
    '2026-05-10T08:41:00Z');
INSERT INTO customer_analytics_profiles VALUES ('analytics_101', NULL,
    'r.delgado@newmail.example', NULL, NULL, 'returning_customer',
    '2026-05-10T08:42:00Z');
COMMIT;

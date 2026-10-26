-- Ghost Data Still Exists: point-in-time database snapshot
-- Dialect: SQLite-compatible SQL
-- Captured at: 2026-06-14T10:00:00Z
-- Database topology: failover_replica
-- Compare the primary and any replica tables for replication drift.

BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (
    resource_id TEXT PRIMARY KEY,
    origin_system TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    record_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO "customer_data_primary" VALUES('search_201','search_service',
    'customer_search_documents',
    '{"resource_id":"search_201","user_id":"user_201",
      "display_name":"Tomas Brandt","email":"tomas.brandt@example.net",
      "index_name":"customer_directory_v3"}',
    '2026-06-14T09:50:00Z');
INSERT INTO "customer_data_primary" VALUES('data_flow_201','integration_service',
    'connector_configurations',
    '{"resource_id":"data_flow_201","customer_email":"tomas.brandt@example.net",
      "connector_name":"customer_events_connector","enabled":true,
      "last_delivery_status":"delivered"}',
    '2026-06-14T09:51:00Z');
CREATE TABLE customer_data_failover_replica (
    resource_id TEXT PRIMARY KEY,
    origin_system TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    record_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO "customer_data_failover_replica" VALUES('account_201','account_service',
    'customer_profiles',
    '{"resource_id":"account_201","user_id":"user_201",
      "full_name":"Tomas Brandt","email":"tomas.brandt@example.net",
      "account_status":"ACTIVE"}',
    '2026-06-14T09:45:00Z');
COMMIT;

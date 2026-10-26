# Ingestion facts

Produced by `python code/scripts/ingest_report.py` (read-only).

## Totals

- evidence files in manifest: **94**
- files that produced at least one observation: **78**
- observations: **2893**
- coverage facts: **369**
- distinct resource ids: **2040**
- observations with at least one link key: **2640**
- distinct link keys: **1364**
- parse warnings: **0**

## Observations per system

| system_id | observations | present | absent | unknown |
|---|---|---|---|---|
| account_service | 663 | 418 | 245 | 0 |
| security_service | 662 | 662 | 0 | 0 |
| search_service | 486 | 486 | 0 | 0 |
| analytics_platform | 337 | 332 | 5 | 0 |
| support_platform | 313 | 299 | 13 | 1 |
| integration_service | 243 | 239 | 4 | 0 |
| backup_service | 107 | 103 | 4 | 0 |
| export_service | 52 | 50 | 2 | 0 |
| identity_service | 18 | 18 | 0 | 0 |
| legal_service | 12 | 12 | 0 | 0 |

## Observations per copy label

| copy_label | observations |
|---|---|
| primary | 1425 |
| trace | 1071 |
| read_replica | 144 |
| replica_ap_south | 87 |
| replica_eu_west | 87 |
| reporting_replica | 51 |
| failover_replica | 20 |
| export | 8 |

## Observations per evidence file

| file_id | type | file | observations | resources | with link key | coverage facts |
|---|---|---|---|---|---|---|
| evidence_01 | log | service_events_ap_south_20260915.log | 36 | 36 | 36 | 0 |
| evidence_02 | json | customer_data_snapshot_bundle_20260917.json | 41 | 41 | 36 | 11 |
| evidence_03 | txt | customer_account_handover_20261001.txt | 41 | 41 | 39 | 0 |
| evidence_04 | png | account_admin_console_20260926.png | 0 | 0 | 0 | 0 |
| evidence_05 | sql | customer_data_read_replica_20260921.sql | 61 | 31 | 55 | 13 |
| evidence_06 | log | structured_service_events_eu_west_20260918.log | 37 | 36 | 36 | 0 |
| evidence_07 | json | customer_data_snapshot_bundle_20260913.json | 30 | 30 | 26 | 9 |
| evidence_08 | log | search_index_diagnostic_20260906.log | 36 | 35 | 35 | 0 |
| evidence_09 | png | search_index_inspector_20260901.png | 0 | 0 | 0 | 0 |
| evidence_10 | sql | customer_data_read_replica_20260827.sql | 59 | 30 | 49 | 13 |
| evidence_11 | log | syslog_archive_us_east_20260824.log | 36 | 36 | 34 | 0 |
| evidence_12 | json | customer_data_snapshot_bundle_20260819.json | 29 | 29 | 27 | 11 |
| evidence_13 | json | connector_configurations_20260812.json | 39 | 38 | 39 | 0 |
| evidence_14 | png | connector_management_console_20260807.png | 0 | 0 | 0 | 0 |
| evidence_15 | sql | customer_data_read_replica_20260802.sql | 49 | 25 | 45 | 15 |
| evidence_16 | log | application_trace_ap_northeast_20260730.log | 36 | 35 | 34 | 0 |
| evidence_17 | json | customer_data_snapshot_bundle_20260725.json | 36 | 36 | 34 | 11 |
| evidence_18 | pdf | account_history_report_20260718.pdf | 0 | 0 | 0 | 0 |
| evidence_19 | png | analytics_profile_view_20260713.png | 0 | 0 | 0 | 0 |
| evidence_20 | sql | customer_data_read_replica_20260708.sql | 63 | 32 | 59 | 13 |
| evidence_21 | log | operations_stream_eu_central_20260705.log | 41 | 40 | 37 | 0 |
| evidence_22 | json | customer_data_snapshot_bundle_20260630.json | 71 | 69 | 65 | 11 |
| evidence_23 | png | customer_export_console_20260618.png | 0 | 0 | 0 | 0 |
| evidence_24 | sql | customer_data_read_replica_20260613.sql | 50 | 26 | 44 | 15 |
| evidence_25 | log | cloud_runtime_export_us_west_20260608.log | 43 | 43 | 41 | 0 |
| evidence_26 | json | customer_data_snapshot_bundle_20260605.json | 32 | 32 | 29 | 9 |
| evidence_27 | pdf | legal_retention_register_20260529.pdf | 0 | 0 | 0 | 0 |
| evidence_28 | png | support_ticket_console_20260524.png | 0 | 0 | 0 | 0 |
| evidence_29 | sql | customer_data_regional_replicas_20260519.sql | 85 | 29 | 79 | 22 |
| evidence_30 | log | service_events_sa_east_20260516.log | 38 | 37 | 34 | 0 |
| evidence_31 | json | customer_data_snapshot_bundle_20260511.json | 74 | 73 | 68 | 10 |
| evidence_32 | png | backup_restore_console_20260429.png | 0 | 0 | 0 | 0 |
| evidence_33 | sql | customer_data_regional_replicas_20260424.sql | 89 | 31 | 83 | 19 |
| evidence_34 | log | structured_service_events_ca_central_20260419.log | 44 | 44 | 43 | 0 |
| evidence_35 | json | customer_data_snapshot_bundle_20260416.json | 28 | 28 | 24 | 8 |
| evidence_36 | json | customer_export_manifest_20260409.json | 46 | 44 | 43 | 0 |
| evidence_37 | png | security_audit_explorer_20260905.png | 0 | 0 | 0 | 0 |
| evidence_38 | sql | customer_data_regional_replicas_20260330.sql | 85 | 29 | 73 | 16 |
| evidence_39 | log | syslog_archive_ap_southeast_20260325.log | 40 | 40 | 40 | 0 |
| evidence_40 | json | customer_data_snapshot_bundle_20260322.json | 31 | 31 | 28 | 12 |
| evidence_41 | log | notification_delivery_20260315.log | 39 | 39 | 39 | 0 |
| evidence_42 | png | deleted_account_reference_20260310.png | 0 | 0 | 0 | 0 |
| evidence_43 | sql | customer_data_reporting_replica_20260305.sql | 51 | 26 | 45 | 15 |
| evidence_44 | log | application_trace_eu_north_20260302.log | 41 | 39 | 38 | 0 |
| evidence_45 | json | customer_data_snapshot_bundle_20260223.json | 75 | 74 | 73 | 11 |
| evidence_46 | png | object_storage_browser_20260213.png | 0 | 0 | 0 | 0 |
| evidence_47 | sql | customer_data_reporting_replica_20260208.sql | 49 | 25 | 47 | 17 |
| evidence_48 | log | operations_stream_me_central_20260203.log | 36 | 36 | 34 | 0 |
| evidence_49 | json | customer_data_snapshot_bundle_20260131.json | 34 | 34 | 32 | 10 |
| evidence_50 | json | security_audit_ledger_20261001.json | 20 | 20 | 20 | 0 |
| evidence_51 | json | customer_data_snapshot_bundle_20260915.json | 4 | 4 | 4 | 3 |
| evidence_52 | sql | customer_data_failover_replica_20260920.sql | 32 | 16 | 32 | 4 |
| evidence_53 | log | cloud_runtime_export_af_south_20260930.log | 300 | 300 | 300 | 0 |
| evidence_54 | sql | customer_accounts_failover_replica_20260902.sql | 7 | 4 | 7 | 2 |
| evidence_55 | pdf | security_retention_extract.pdf | 0 | 0 | 0 | 0 |
| evidence_56 | json | customer_analytics_report_20260907.json | 2 | 2 | 1 | 0 |
| evidence_57 | sql | customer_search_primary_20260910.sql | 3 | 3 | 3 | 1 |
| evidence_58 | pdf | delivery_correction_attachment.pdf | 0 | 0 | 0 | 0 |
| evidence_59 | json | customer_export_jobs_20260914.json | 2 | 2 | 2 | 0 |
| evidence_60 | png | marketing_profile_console.png | 0 | 0 | 0 | 0 |
| evidence_61 | log | support_replica.log | 4 | 4 | 3 | 0 |
| evidence_62 | json | service_resource_export_20260129_114459.json | 29 | 29 | 24 | 0 |
| evidence_63 | sql | customer_data_inventory_20260213.sql | 17 | 17 | 14 | 8 |
| evidence_64 | log | resource_lookup_trace_20260218_083911.log | 26 | 26 | 25 | 0 |
| evidence_65 | json | service_resource_export_20260228_103637.json | 27 | 27 | 20 | 0 |
| evidence_66 | sql | customer_data_inventory_20260310.sql | 23 | 23 | 21 | 8 |
| evidence_67 | log | resource_lookup_trace_20260310_061801.log | 1 | 1 | 1 | 0 |
| evidence_68 | json | service_resource_export_20260320_072349.json | 25 | 25 | 18 | 0 |
| evidence_69 | sql | customer_data_inventory_20260404.sql | 18 | 18 | 16 | 8 |
| evidence_70 | log | resource_lookup_trace_20260414_054007.log | 24 | 24 | 20 | 0 |
| evidence_71 | json | service_resource_export_20260429_083222.json | 28 | 28 | 20 | 0 |
| evidence_72 | sql | customer_data_inventory_20260504.sql | 21 | 21 | 19 | 8 |
| evidence_73 | log | resource_lookup_trace_20260509_104353.log | 33 | 33 | 29 | 0 |
| evidence_74 | json | service_resource_export_20260514_114454.json | 25 | 25 | 17 | 0 |
| evidence_75 | sql | customer_data_inventory_20260524.sql | 24 | 24 | 20 | 8 |
| evidence_76 | log | resource_lookup_trace_20260529_074313.log | 23 | 23 | 20 | 0 |
| evidence_77 | json | service_resource_export_20260603_083457.json | 27 | 27 | 21 | 0 |
| evidence_78 | sql | customer_data_inventory_20260618.sql | 19 | 19 | 17 | 8 |
| evidence_79 | log | resource_lookup_trace_20260623_053726.log | 29 | 29 | 26 | 0 |
| evidence_80 | json | service_resource_export_20260628_062556.json | 24 | 24 | 18 | 0 |
| evidence_81 | sql | customer_data_inventory_20260703.sql | 24 | 24 | 20 | 8 |
| evidence_82 | log | resource_lookup_trace_20260713_093359.log | 26 | 26 | 22 | 0 |
| evidence_83 | json | service_resource_export_20260718_104335.json | 29 | 29 | 24 | 0 |
| evidence_84 | sql | customer_data_inventory_20260723.sql | 26 | 26 | 24 | 8 |
| evidence_85 | log | resource_lookup_trace_20260728_054417.log | 22 | 22 | 20 | 0 |
| evidence_86 | json | service_resource_export_20260807_074104.json | 23 | 23 | 18 | 0 |
| evidence_87 | sql | customer_data_inventory_20260817.sql | 19 | 19 | 17 | 8 |
| evidence_88 | log | resource_lookup_trace_20260822_103910.log | 22 | 22 | 20 | 0 |
| evidence_89 | json | service_resource_export_20260901_053316.json | 23 | 23 | 21 | 0 |
| evidence_90 | sql | customer_data_inventory_20260905.sql | 0 | 0 | 0 | 8 |
| evidence_91 | log | resource_lookup_trace_20260911_074253.log | 30 | 30 | 26 | 0 |
| evidence_92 | json | service_resource_export_20260915_114455.json | 36 | 36 | 26 | 0 |
| evidence_93 | sql | customer_data_inventory_20260916.sql | 17 | 17 | 16 | 8 |
| evidence_94 | log | resource_lookup_trace_20260926_103856.log | 28 | 28 | 25 | 0 |

## Files that produced no observations

- `evidence_04` account_admin_console_20260926.png (account_admin_portal)
- `evidence_09` search_index_inspector_20260901.png (search_index_admin)
- `evidence_14` connector_management_console_20260807.png (integration_admin)
- `evidence_18` account_history_report_20260718.pdf (account_history_service)
- `evidence_19` analytics_profile_view_20260713.png (analytics_platform)
- `evidence_23` customer_export_console_20260618.png (export_service)
- `evidence_27` legal_retention_register_20260529.pdf (legal_retention_service)
- `evidence_28` support_ticket_console_20260524.png (support_platform)
- `evidence_32` backup_restore_console_20260429.png (backup_service)
- `evidence_37` security_audit_explorer_20260905.png (security_service)
- `evidence_42` deleted_account_reference_20260310.png (identity_service)
- `evidence_46` object_storage_browser_20260213.png (object_storage)
- `evidence_55` security_retention_extract.pdf (security_service)
- `evidence_58` delivery_correction_attachment.pdf (support_platform)
- `evidence_60` marketing_profile_console.png (marketing_platform)
- `evidence_90` customer_data_inventory_20260905.sql (customer_data_inventory_capture)

## Parse warnings

None.

## Reachability of resources cited by `sample_output.csv`

75 cited (case, evidence, resource) triples, graded by how ingestion reaches them:

| verdict | citations | meaning |
|---|---|---|
| observation | 70 | the resource was parsed out of the cited file |
| coverage_absence | 4 | the file enumerated the system without the resource, proving absence |
| pending_document | 1 | a PDF or screenshot awaiting the model extraction step |
| MISS | 0 | a hole in a parser |

### Citations deferred to document extraction

| case_id | evidence_id | resource_id | operation | file |
|---|---|---|---|---|
| case_07 | evidence_58 | support_001 | REPORT_REMAINING_RECORD | delivery_correction_attachment.pdf |

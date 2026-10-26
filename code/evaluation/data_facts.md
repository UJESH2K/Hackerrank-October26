# Data facts

Measured profile of the participant-facing dataset, produced by
`code/scripts/profile_dataset.py` (read-only; not imported by the pipeline).
Regenerate with `python code/scripts/profile_dataset.py`.

Language and request-intent labels in section 4 are coarse keyword heuristics
used only to size the problem. The pipeline classifies intent with the model.

## 1. File shapes

### `deletion_requests.csv`

518 data rows; columns: `case_id`, `email`, `requested_at`, `reported_status`, `reported_at`, `audit_at`, `request_text`

- **reported_status** — 3 distinct: `COMPLETE`(254), `PARTIALLY_COMPLETE`(152), `NOT_STARTED`(112)

### `sample_deletion_requests.csv`

32 data rows; columns: `case_id`, `email`, `requested_at`, `reported_status`, `reported_at`, `audit_at`, `request_text`

- **requested_at** — 30 distinct: `2026-09-04T12:00:00Z`(3), `2026-08-18T09:14:00Z`(1), `2026-08-21T18:06:00Z`(1), `2026-09-13T11:00:00Z`(1), `2026-08-25T12:32:00Z`(1), `2026-08-27T14:17:00Z`(1), `2026-08-29T06:51:00Z`(1), `2026-08-31T11:26:00Z`(1), `2026-08-29T20:03:00Z`(1), `2026-09-05T09:00:00Z`(1), `2026-08-31T15:42:00Z`(1), `2026-09-09T12:00:00Z`(1), `2026-08-24T12:00:00Z`(1), `2026-09-08T12:00:00Z`(1), `2026-08-21T12:00:00Z`(1), `2026-08-29T10:00:00Z`(1), `2026-08-19T12:00:00Z`(1), `2026-09-06T11:00:00Z`(1), `2026-08-17T12:00:00Z`(1), `2026-09-08T11:00:00Z`(1), `2026-08-31T10:00:00Z`(1), `2026-09-02T12:00:00Z`(1), `2026-09-01T12:00:00Z`(1), `2026-08-31T12:00:00Z`(1), `2026-08-30T12:00:00Z`(1), `2026-08-29T12:00:00Z`(1), `2026-08-28T12:00:00Z`(1), `2026-08-27T12:00:00Z`(1), `2026-08-26T12:00:00Z`(1), `2026-09-03T12:00:00Z`(1)
- **reported_status** — 3 distinct: `COMPLETE`(17), `NOT_STARTED`(10), `PARTIALLY_COMPLETE`(5)
- **reported_at** — 22 distinct: `2026-09-06T12:00:00Z`(3), `2026-09-02T12:00:00Z`(3), `2026-08-30T12:00:00Z`(3), `2026-08-22T12:00:00Z`(2), `2026-09-01T10:00:00Z`(2), `2026-09-09T11:00:00Z`(2), `2026-09-05T12:00:00Z`(2), `2026-08-20T16:42:00Z`(1), `2026-08-22T07:25:00Z`(1), `2026-09-15T11:00:00Z`(1), `2026-08-27T08:54:00Z`(1), `2026-08-28T09:03:00Z`(1), `2026-08-30T17:12:00Z`(1), `2026-09-01T19:30:00Z`(1), `2026-08-31T13:44:00Z`(1), `2026-09-07T09:00:00Z`(1), `2026-09-02T10:08:00Z`(1), `2026-09-12T12:00:00Z`(1), `2026-08-25T12:00:00Z`(1), `2026-09-09T12:00:00Z`(1), `2026-08-19T12:00:00Z`(1), `2026-08-27T12:00:00Z`(1)
- **audit_at** — 13 distinct: `2026-09-15T12:00:00Z`(15), `2026-09-26T11:00:00Z`(3), `2026-10-01T12:00:00Z`(3), `2026-09-21T10:00:00Z`(2), `2026-09-03T11:30:00Z`(1), `2026-09-04T15:00:00Z`(1), `2026-09-07T10:20:00Z`(1), `2026-09-08T13:40:00Z`(1), `2026-09-10T09:15:00Z`(1), `2026-09-12T08:00:00Z`(1), `2026-09-14T16:10:00Z`(1), `2026-09-16T09:00:00Z`(1), `2026-09-18T12:25:00Z`(1)

### `sample_output.csv`

32 data rows; columns: `case_id`, `deletion_status`, `steps_json`

- **deletion_status** — 2 distinct: `INCOMPLETE`(22), `COMPLETE`(10)

### `user_identities.csv`

850 data rows; columns: `user_id`, `name`, `email`, `phone`, `email_hash`, `updated_at`


### `user_audit_log.csv`

306 data rows; columns: `audit_id`, `user_id`, `event_time`, `field_name`, `old_value`, `new_value`

- **field_name** — 3 distinct: `email`(107), `email_hash`(107), `phone`(92)

### `system_activity.csv`

4011 data rows; columns: `activity_id`, `event_time`, `event_type`, `source_system`, `target_system`, `resource_id`, `reported_result`, `details`

- **event_type** — 17 distinct: `DELETE_RECORD`(890), `UPDATE_RECORD`(672), `SYNC_RECORD`(447), `INDEX_RECORD`(400), `SEND_EMAIL`(368), `REFRESH_CACHE`(284), `RETAIN_RECORD`(206), `ANONYMIZE_RECORD`(204), `REDACT_RECORD`(173), `CREATE_EXPORT`(155), `CHECK_DATA_FLOW`(89), `DISABLE_DATA_FLOW`(36), `BLOCK_RESTORE`(36), `CHECK_RESTORE_ACCESS`(28), `LINK_RESOURCE_IDENTITY`(12), `CLASSIFY_REQUEST`(10), `RESTORE_BACKUP`(1)
- **source_system** — 11 distinct: `deletion_worker`(1466), `account_service`(1100), `profile_service`(440), `notification_service`(368), `support_platform`(232), `security_service`(196), `export_service`(155), `integration_service`(31), `data_catalog`(12), `privacy_intake`(10), `backup_service`(1)
- **target_system** — 19 distinct: `account_service`(980), `search_service`(954), `analytics_platform`(566), `email_delivery_service`(368), `cache_service`(284), `archive_service`(196), `support_platform`(193), `object_store`(155), `integration_service`(126), `backup_service`(65), `account_replication_cluster`(55), `export_service`(34), `identity_service`(11), `legal_service`(10), `privacy_case_management`(10), `account_failover_replica`(1), `object_storage`(1), `marketing_platform`(1), `security_service`(1)
- **reported_result** — 3 distinct: `SUCCESS`(2914), `FAILED`(721), `PARTIAL`(376)

### `deletion_rules.csv`

15 data rows; columns: `rule_id`, `data_category`, `condition`, `required_action`

- **condition** — 9 distinct: `Always`(6), `Record is still attributable to a user`(2), `Conversation contains shared customer, agent, or order context that must be retained`(1), `Attachment belongs only to the deletion subject`(1), `Log contains a user email address`(1), `Record is within the supplied retention period`(1), `A supplied legal hold is active`(1), `Restore access can be blocked`(1), `Account is deleted and only a stable internal user_id is needed to preserve references to retained records`(1)
- **required_action** — 4 distinct: `DELETE`(7), `REDACT`(3), `RETAIN`(3), `ANONYMIZE`(2)

### `evidence_files.csv`

94 data rows; columns: `file_id`, `file_path`, `origin`, `captured_at`

- **origin** — 30 distinct: `central_observability_export`(12), `records_assurance_snapshot_bundle`(12), `service_resource_export`(11), `customer_data_inventory_capture`(11), `service_readback_logs`(11), `customer_data_read_replica_capture`(5), `support_platform`(3), `customer_data_regional_replicas_capture`(3), `analytics_platform`(2), `export_service`(2), `security_service`(2), `customer_data_reporting_replica_capture`(2), `customer_account_service`(1), `account_admin_portal`(1), `search_index_service`(1), `search_index_admin`(1), `integration_management_service`(1), `integration_admin`(1), `account_history_service`(1), `legal_retention_service`(1), `backup_service`(1), `customer_export_service`(1), `downstream_delivery_service`(1), `identity_service`(1), `object_storage`(1), `security_audit_service`(1), `customer_data_failover_replica_capture`(1), `customer_account_failover_replica_capture`(1), `customer_search_primary_capture`(1), `marketing_platform`(1)

## 2. `system_activity.csv`

4011 rows. This stream is global: rows concern many users.

### 2.1 event_type x source_system -> target_system

| event_type | source_system | target_system | rows |
|---|---|---|---|
| DELETE_RECORD | deletion_worker | account_service | 539 |
| UPDATE_RECORD | profile_service | account_service | 440 |
| INDEX_RECORD | account_service | search_service | 400 |
| SEND_EMAIL | notification_service | email_delivery_service | 368 |
| SYNC_RECORD | account_service | analytics_platform | 360 |
| DELETE_RECORD | deletion_worker | search_service | 289 |
| REFRESH_CACHE | account_service | cache_service | 284 |
| UPDATE_RECORD | support_platform | search_service | 232 |
| ANONYMIZE_RECORD | deletion_worker | analytics_platform | 204 |
| RETAIN_RECORD | security_service | archive_service | 196 |
| REDACT_RECORD | deletion_worker | support_platform | 163 |
| CREATE_EXPORT | export_service | object_store | 155 |
| CHECK_DATA_FLOW | deletion_worker | integration_service | 89 |
| SYNC_RECORD | account_service | account_replication_cluster | 55 |
| BLOCK_RESTORE | deletion_worker | backup_service | 36 |
| DISABLE_DATA_FLOW | deletion_worker | integration_service | 36 |
| DELETE_RECORD | deletion_worker | export_service | 32 |
| SYNC_RECORD | integration_service | search_service | 31 |
| DELETE_RECORD | deletion_worker | support_platform | 29 |
| CHECK_RESTORE_ACCESS | deletion_worker | backup_service | 28 |
| CLASSIFY_REQUEST | privacy_intake | privacy_case_management | 10 |
| REDACT_RECORD | deletion_worker | identity_service | 10 |
| RETAIN_RECORD | deletion_worker | legal_service | 10 |
| LINK_RESOURCE_IDENTITY | data_catalog | export_service | 2 |
| DELETE_RECORD | deletion_worker | analytics_platform | 1 |
| LINK_RESOURCE_IDENTITY | data_catalog | account_service | 1 |
| LINK_RESOURCE_IDENTITY | data_catalog | analytics_platform | 1 |
| LINK_RESOURCE_IDENTITY | data_catalog | backup_service | 1 |
| LINK_RESOURCE_IDENTITY | data_catalog | identity_service | 1 |
| LINK_RESOURCE_IDENTITY | data_catalog | integration_service | 1 |
| LINK_RESOURCE_IDENTITY | data_catalog | marketing_platform | 1 |
| LINK_RESOURCE_IDENTITY | data_catalog | object_storage | 1 |
| LINK_RESOURCE_IDENTITY | data_catalog | search_service | 1 |
| LINK_RESOURCE_IDENTITY | data_catalog | security_service | 1 |
| LINK_RESOURCE_IDENTITY | data_catalog | support_platform | 1 |
| RESTORE_BACKUP | backup_service | search_service | 1 |
| SYNC_RECORD | account_service | account_failover_replica | 1 |

### 2.2 reported_result distribution

- `SUCCESS` — 2914
- `FAILED` — 721
- `PARTIAL` — 376

Per event_type:

| event_type | FAILED | PARTIAL | SUCCESS |
|---|---|---|---|
| ANONYMIZE_RECORD | 64 | 8 | 132 |
| BLOCK_RESTORE | 0 | 0 | 36 |
| CHECK_DATA_FLOW | 0 | 0 | 89 |
| CHECK_RESTORE_ACCESS | 0 | 0 | 28 |
| CLASSIFY_REQUEST | 0 | 0 | 10 |
| CREATE_EXPORT | 9 | 5 | 141 |
| DELETE_RECORD | 368 | 220 | 302 |
| DISABLE_DATA_FLOW | 0 | 0 | 36 |
| INDEX_RECORD | 32 | 20 | 348 |
| LINK_RESOURCE_IDENTITY | 0 | 0 | 12 |
| REDACT_RECORD | 117 | 9 | 47 |
| REFRESH_CACHE | 22 | 16 | 246 |
| RESTORE_BACKUP | 0 | 0 | 1 |
| RETAIN_RECORD | 15 | 6 | 185 |
| SEND_EMAIL | 21 | 14 | 333 |
| SYNC_RECORD | 20 | 46 | 381 |
| UPDATE_RECORD | 53 | 32 | 587 |

### 2.3 `details` sentence templates (digits masked)

45 distinct templates across 4011 rows.

- `Deletion worker submitted primary profile account_# to account_service.` — 532
- `Profile preference update processed account_#.` — 440
- `Customer search-index refresh processed search_#.` — 400
- `Transactional email delivery processed email_delivery_#.` — 368
- `Customer profile synchronization processed analytics_#.` — 360
- `Deletion worker received the final result for search_# from search_service.` — 288
- `Profile cache refresh processed cache_#.` — 284
- `Support-case index update processed support_#.` — 232
- `Security-event archival processed audit_record_#.` — 196
- `Scheduled account export processed export_#.` — 155
- `Deletion worker received the final result for support_# from support_platform.` — 155
- `Policy action completed for analytics_#.` — 125
- `Connector status check confirmed that data flow data_flow_# remains enabled.` — 89
- `Deletion worker received the final result for analytics_# from analytics_platform.` — 79
- `Replication worker reported its final checkpoint for account_#.` — 55
- `Policy action completed for support_#.` — 36
- `Policy action completed for data_flow_#.` — 36
- `Policy action completed for backup_#.` — 36
- `Active connector repopulated downstream resource search_#.` — 31
- `Deletion worker received the final result for export_# from export_service.` — 31
- `Restore-access check confirmed that archive backup_# can still be restored.` — 28
- `Policy action completed for legal_hold_#.` — 10
- `Privacy intake classified request for account_# as non-deletion; no destructive action was authorized.` — 10
- `Deletion worker reported the requested operation as completed. Target resource: account_#.` — 6
- `Policy action completed for tombstone_#.` — 5
- `Deletion worker received the final result for tombstone_# from identity_service.` — 5
- `Deletion worker completed some internal work but could not confirm the final state. Target resource: account_#.` — 1
- `Restore job repopulated downstream resource search_#.` — 1
- `Deletion worker reported the requested operation as completed. Target resource: analytics_#.` — 1
- `Replication worker reported its final checkpoint. Target resource: account_#.` — 1
- `Deletion worker stopped after the target system returned an error. Target resource: search_#.` — 1
- `Deletion worker stopped after the target system returned an error. Target resource: export_#.` — 1
- `Deletion worker reported the requested operation as completed. Target resource: support_#.` — 1
- `Data catalog linked account_# (account_#) to haruka.s#@zoho.com and user_#.` — 1
- `Data catalog linked search_# (search_#) to g.silva#@gmail.com and user_#.` — 1
- `Data catalog linked data_flow_# (data_flow_#) to jain.aditi@gmail.com and user_#.` — 1
- `Data catalog linked analytics_# (analytics_#) to darwish.omar@icloud.com and user_#.` — 1
- `Data catalog linked export_# (export_#) to ingrid.dahl@outlook.com and user_#.` — 1
- `Data catalog linked support_# (support_#) to kagyeman#@fastmail.com and user_#.` — 1
- `Data catalog linked backup_# (backup_#) to areed#@gmail.com and user_#.` — 1
- `Attachment catalog confirmed object_# (delivery_confirmation_#.pdf) belongs only to priya.raman@gmail.com and user_#; no other subject is linked.` — 1
- `Data catalog linked marketing_# (marketing_#) to kwame-mensah@proton.me and user_#.` — 1
- `Data catalog linked export_# (NST-#-#-#) to ingrid.dahl@outlook.com and user_#.` — 1
- `Data catalog linked audit_record_# (audit_record_#) to oliver-bennett@gmail.com and user_#.` — 1
- `Data catalog linked tombstone_# (user_#) to leonie.g#@outlook.com and user_#.` — 1

### 2.4 resource_id prefix x systems

| resource prefix | rows | target systems (rows) |
|---|---|---|
| `account` | 1046 | account_service (980), account_replication_cluster (55), privacy_case_management (10), account_failover_replica (1) |
| `search` | 690 | search_service (690) |
| `analytics` | 566 | analytics_platform (566) |
| `support` | 425 | search_service (232), support_platform (193) |
| `email_delivery` | 368 | email_delivery_service (368) |
| `cache` | 284 | cache_service (284) |
| `audit_record` | 197 | archive_service (196), security_service (1) |
| `export` | 189 | object_store (155), export_service (34) |
| `data_flow` | 157 | integration_service (126), search_service (31) |
| `backup` | 66 | backup_service (65), search_service (1) |
| `tombstone` | 11 | identity_service (11) |
| `legal_hold` | 10 | legal_service (10) |
| `object` | 1 | object_storage (1) |
| `marketing` | 1 | marketing_platform (1) |

## 3. `user_audit_log.csv`

306 changes affecting 196 of 850 users.

### 3.1 Fields that change

- `email` — 107
- `email_hash` — 107
- `phone` — 92

### 3.2 Changes per user

| changes | users |
|---|---|
| 1 | 91 |
| 2 | 102 |
| 3 | 1 |
| 4 | 2 |

105 users have more than one change.

### 3.3 Do field changes arrive in pairs?

107 (user, event_time) pairs carry more than one field change, so an identity change can touch several fields at once.

### 3.4 Identifier collisions across users

- historical email of one user equal to **another** user's current email: **0**
- historical phone of one user equal to **another** user's current phone: **0**

- historical email values appearing in more than one change row: **0**

## 4. `deletion_requests.csv`

### 4.1 reported_status distribution

- `COMPLETE` — 254
- `PARTIALLY_COMPLETE` — 152
- `NOT_STARTED` — 112

Sample file for comparison: `COMPLETE`(17), `NOT_STARTED`(10), `PARTIALLY_COMPLETE`(5)

### 4.2 Timestamp ordering

- rows where `requested_at` > `reported_at`: **0**
- rows where `audit_at` < `reported_at`: **0**
- `audit_at` range: 2026-01-29T12:00:00Z .. 2026-10-01T12:00:00Z

### 4.3 Languages present (heuristic)

- `English (or undetermined Latin)` — 456
- `Portuguese` — 14
- `Korean` — 12
- `Spanish` — 12
- `Japanese` — 12
- `French` — 12

### 4.4 Requests that do not look like deletion requests (heuristic)

- rows with **no** deletion cue at all: **7**
- rows carrying a non-deletion cue (access / export / correction / opt-out): **1**
- rows declaring the account must survive: **10**
- **negation traps** — a deletion keyword *and* a keep-the-account statement in the same text: **3**
- union to review: **10**

The union is the candidate `NOT_APPLICABLE` set. Every row in it is listed below, with its reported status.

- `case_533` (status `NOT_STARTED`, cues: leave the account, account active) — The mobile number on my profile is out of date. Please replace it with the new one and leave the account active.
- `case_534` (status `NOT_STARTED`, cues: not asking for deletion, still need the account) **[negation trap]** — Please stop the promotional emails. I still need the account and my order history, so I am not asking for deletion.
- `case_535` (status `NOT_STARTED`, cues: download my, keep the account, as it is) — I'd like to download my account information in a portable format. Please keep the account as it is.
- `case_536` (status `NOT_STARTED`, cues: do not close) — I can't access my profile and need help resetting the login. Do not close the account.
- `case_537` (status `NOT_STARTED`, cues: without removing) — My surname is misspelled on the account. Can you correct it without removing any of my records?
- `case_538` (status `NOT_STARTED`, cues: still want) — Please remove me from marketing lists only. I still want receipts and service messages for this account.
- `case_539` (status `NOT_STARTED`, cues: not a request to) **[negation trap]** — Can you tell me what personal information you hold about me? This is an information request, not a request to erase it.
- `case_540` (status `NOT_STARTED`, cues: leave my profile) — I need a copy of last year's invoices for my records. Please leave my profile and purchase history in place.
- `case_541` (status `NOT_STARTED`, cues: continue using the service) — Please change the email address used for this account. I want to continue using the service.
- `case_542` (status `NOT_STARTED`, cues: don't delete, still use it) **[negation trap]** — Could you send me a copy of the information held on my account? I still use it, so please don't delete or change anything.

A keyword rule cannot decide the negation traps: the deletion keyword is the thing being refused. This is the reason request intent goes to the model rather than to a cue list.

### 4.5 How many distinct request texts are there?

375 distinct texts (emails and digits masked) across 518 requests. This bounds the cost of classifying intent: a cache keyed on the normalised text needs at most that many model calls.

Most frequent texts (truncated to 150 characters):

- 14 x — Solicito a exclusão permanente da conta associada a este e-mail. Verifiquem os identificadores atuais e anteriores conforme o processo de privacidade.
- 12 x — 이 이메일 주소에 연결된 계정의 영구 삭제를 요청합니다. 개인정보 처리 절차에 따라 현재 및 이전 식별자를 검토해 주세요. 검토가 완료되면 결과를 확인해 주세요.
- 12 x — Solicito la eliminación permanente de la cuenta asociada a este correo. Revisen los identificadores actuales y anteriores según su política de privaci
- 12 x — このメールアドレスに関連するアカウントの完全な削除を依頼します。 プライバシー手続きに従い、現在および過去の識別情報を確認してください。 確認が完了したら結果をお知らせください。
- 12 x — Je demande la suppression définitive du compte associé à cette adresse e-mail. Veuillez examiner les identifiants actuels et antérieurs conformément à
- 3 x — Please erase the account linked to this email address. Please handle any copies that fall within the scope of the deletion request. Let me know when t
- 3 x — Please treat this message as a request for permanent account deletion. Please review any identifiers connected to this account as part of the request.
- 3 x — Please permanently delete the account associated with this email address. Please apply the correct treatment to records covered by the request. Please
- 3 x — Please treat this message as a request for permanent account deletion. Please apply the request across the systems covered by your deletion policy. Pl
- 3 x — I would like to submit a request to delete this account and its personal data. Please process this under the standard personal-data deletion procedure
- 3 x — Please treat this message as a request for permanent account deletion. Please apply the request across the systems covered by your deletion policy. I 
- 3 x — Please erase the account linked to this email address. Please check the records connected to the account before confirming completion. Please contact 

Texts appearing exactly once: **295**.

## 5. `evidence_files.csv`

94 evidence artefacts.

### 5.1 By file type

- `.json` — 28
- `.log` — 26
- `.sql` — 24
- `.png` — 11
- `.pdf` — 4
- `.txt` — 1

### 5.2 By folder

- `records` — 29
- `logs` — 26
- `databases` — 24
- `images` — 11
- `documents` — 4

### 5.3 By origin

- `central_observability_export` — 12
- `records_assurance_snapshot_bundle` — 12
- `service_resource_export` — 11
- `customer_data_inventory_capture` — 11
- `service_readback_logs` — 11
- `customer_data_read_replica_capture` — 5
- `support_platform` — 3
- `customer_data_regional_replicas_capture` — 3
- `analytics_platform` — 2
- `export_service` — 2
- `security_service` — 2
- `customer_data_reporting_replica_capture` — 2
- `customer_account_service` — 1
- `account_admin_portal` — 1
- `search_index_service` — 1
- `search_index_admin` — 1
- `integration_management_service` — 1
- `integration_admin` — 1
- `account_history_service` — 1
- `legal_retention_service` — 1
- `backup_service` — 1
- `customer_export_service` — 1
- `downstream_delivery_service` — 1
- `identity_service` — 1
- `object_storage` — 1
- `security_audit_service` — 1
- `customer_data_failover_replica_capture` — 1
- `customer_account_failover_replica_capture` — 1
- `customer_search_primary_capture` — 1
- `marketing_platform` — 1

### 5.4 captured_at range

- earliest: 2026-01-29T11:44:59Z
- latest: 2026-10-01T12:00:00Z

- captured after the latest `audit_at` (2026-10-01T12:00:00Z), so inadmissible everywhere: **0**
- captured at or before the earliest `audit_at` (2026-01-29T12:00:00Z), so admissible everywhere: **1**

## 6. `sample_output.csv`

32 sample answers: `INCOMPLETE`(22), `COMPLETE`(10)

### 6.1 Steps per case

| deletion_status | cases | total steps | min | max | mean |
|---|---|---|---|---|---|
| COMPLETE | 10 | 34 | 1 | 6 | 3.4 |
| INCOMPLETE | 22 | 159 | 3 | 14 | 7.2 |

### 6.2 Operation distribution

| phase | operation | state | steps |
|---|---|---|---|
| FINDING | REPORT_REMAINING_RECORD | PRESENT | 21 |
| FINDING | REPORT_EXPOSED_PII | PRESENT | 11 |
| FINDING | REPORT_ACTIVE_DATA_FLOW | ACTIVE | 6 |
| FINDING | REPORT_RESTORE_RISK | ACTIVE | 3 |
| REMEDIATION | DELETE_RECORD | ABSENT | 21 |
| REMEDIATION | DISABLE_DATA_FLOW | DISABLED | 6 |
| REMEDIATION | REDACT_RECORD | REDACTED | 6 |
| REMEDIATION | ANONYMIZE_RECORD | ANONYMIZED | 5 |
| REMEDIATION | BLOCK_RESTORE | BLOCKED | 3 |
| VERIFICATION | VERIFY_RECORD_ABSENT | ABSENT | 50 |
| VERIFICATION | VERIFY_RECORD_PRESENT | PRESENT | 24 |
| VERIFICATION | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | 12 |
| VERIFICATION | VERIFY_RECORD_REDACTED | REDACTED | 10 |
| VERIFICATION | VERIFY_DATA_FLOW_DISABLED | DISABLED | 9 |
| VERIFICATION | VERIFY_RESTORE_BLOCKED | BLOCKED | 6 |

### 6.3 Operation distribution by case status

| operation | COMPLETE | INCOMPLETE |
|---|---|---|
| ANONYMIZE_RECORD | 0 | 5 |
| BLOCK_RESTORE | 0 | 3 |
| DELETE_RECORD | 0 | 21 |
| DISABLE_DATA_FLOW | 0 | 6 |
| REDACT_RECORD | 0 | 6 |
| REPORT_ACTIVE_DATA_FLOW | 0 | 6 |
| REPORT_EXPOSED_PII | 0 | 11 |
| REPORT_REMAINING_RECORD | 0 | 21 |
| REPORT_RESTORE_RISK | 0 | 3 |
| VERIFY_DATA_FLOW_DISABLED | 3 | 6 |
| VERIFY_RECORD_ABSENT | 10 | 40 |
| VERIFY_RECORD_ANONYMIZED | 7 | 5 |
| VERIFY_RECORD_PRESENT | 7 | 17 |
| VERIFY_RECORD_REDACTED | 4 | 6 |
| VERIFY_RESTORE_BLOCKED | 3 | 3 |

### 6.4 Which steps carry an `evidence_id`

| deletion_status | phase | with evidence_id | empty evidence_id |
|---|---|---|---|
| COMPLETE | VERIFICATION | 34 | 0 |
| INCOMPLETE | FINDING | 41 | 0 |
| INCOMPLETE | REMEDIATION | 0 | 41 |
| INCOMPLETE | VERIFICATION | 0 | 77 |

### 6.5 Evidence citation frequency

75 citations referencing 13 distinct ids.

Where cited ids resolve (distinct ids):

- `evidence_files.file_id` — 13

Most-cited ids:

- `evidence_92` — 36
- `evidence_94` — 9
- `evidence_03` — 7
- `evidence_01` — 5
- `evidence_54` — 4
- `evidence_05` — 4
- `evidence_51` — 4
- `evidence_56` — 1
- `evidence_57` — 1
- `evidence_58` — 1
- `evidence_59` — 1
- `evidence_93` — 1
- `evidence_61` — 1

Cases citing more than one distinct evidence id: **1** of 32.

### 6.6 Systems and resource prefixes used in steps

| resource prefix | steps | system_id(s) |
|---|---|---|
| `account` | 38 | account_service (38) |
| `search` | 36 | search_service (36) |
| `support` | 27 | support_platform (27) |
| `analytics` | 25 | analytics_platform (25) |
| `audit_record` | 24 | security_service (24) |
| `data_flow` | 21 | integration_service (21) |
| `backup` | 12 | backup_service (12) |
| `export` | 6 | export_service (6) |
| `tombstone` | 4 | identity_service (4) |

### 6.7 Step ordering check

- cases whose phases are out of order: **0**
- cases whose `step_no` is not 1..n consecutive: **0**

## 7. Observations to verify later

### Settled by this profile

1. **Every cited evidence id in `sample_output.csv` is a `file_id`.** All 75
   citations resolve against `evidence_files.csv`; none is an `activity_id`
   or `audit_id`. The contract permits all three, but the planner should
   cite a `file_id` and treat the other two as fallbacks.
2. **A case is anchored on one evidence file.** 31 of 32 sample cases cite a
   single distinct evidence id. Evidence fusion across many files is not the
   normal path; finding the one authoritative snapshot per case is.
3. **`resource_id` prefix determines `system_id` in steps, one-to-one.**
   `account`->`account_service`, `search`->`search_service`,
   `support`->`support_platform`, `analytics`->`analytics_platform`,
   `audit_record`->`security_service`, `data_flow`->`integration_service`,
   `backup`->`backup_service`, `export`->`export_service`,
   `tombstone`->`identity_service`. Note this is the resource's **owning**
   system, not the activity `target_system`: activity rows route
   `support_*` through `search_service` and `export_*` through
   `object_store`, but steps never do.
4. **Phase order and step numbering in the samples are exactly as specified.**
   Zero cases have out-of-order phases or non-consecutive `step_no`.
5. **Evidence-id discipline is absolute.** `COMPLETE`: 34/34 verification
   steps cite evidence. `INCOMPLETE`: 41/41 findings cite evidence, and all
   41 remediation plus all 77 verification steps carry `""`.
6. **No identifier collisions across users.** No historical email or phone of
   one user equals another user's current value, and no historical email
   appears in two change rows. Resolving the request email to a subject is
   therefore unambiguous, which removes the need for a tie-break rule.
7. **Identity changes arrive in pairs.** `email` and `email_hash` both change
   107 times, and 107 (user, event_time) pairs carry more than one field, so
   an email change always rewrites the hash at the same instant.
8. **No evidence file is globally inadmissible.** Capture times run
   2026-01-29 to 2026-10-01 and `audit_at` runs the same window, so
   admissibility must be decided per case, not filtered once up front.
9. **`reported_result` is demonstrably unreliable as a state signal.**
   `DELETE_RECORD` activity is 302 `SUCCESS`, 368 `FAILED`, 220 `PARTIAL`:
   a claimed success is barely a third of deletion attempts.

### Still open

10. **No `NOT_APPLICABLE` example exists in public ground truth.**
    `sample_output.csv` is 10 `COMPLETE` and 22 `INCOMPLETE`. The
    non-deletion class is unexercised and must be covered by our own
    evaluation cases. Two independent signals agree on its size: the
    candidate set in section 4.4, and the 10 `CLASSIFY_REQUEST` rows from
    `privacy_intake` stating a request was classified as non-deletion with
    no destructive action authorised. Whether the two sets are the same ten
    cases is the open question, and whether a non-deletion case can exist
    *without* a `CLASSIFY_REQUEST` row is the risk: relying on that row
    would miss any case the intake system never classified.
17. **Negation traps are real.** Some non-deletion requests state the
    deletion keyword in order to refuse it ('I am not asking for deletion',
    'This is an information request, not a request to erase it', 'please
    don't delete or change anything'). Section 4.4 counts them. Any keyword
    rule decides these backwards, which is the concrete justification for
    sending request intent to the model.
11. **Data categories are named in no participant file.** Steps carry
    `system_id` and `resource_id`; `deletion_rules.csv` is keyed by
    `data_category`. The resource-prefix-to-category mapping must be inferred
    and justified from evidence contents (for example whether `audit_record`
    is `SECURITY_AUDIT_LOG` and `email_delivery` is `EMAIL_DELIVERY_LOG`).
12. **Conditional rules need evaluable conditions.** `SUPPORT_MESSAGE`,
    `SUPPORT_ATTACHMENT`, `ANALYTICS_PROFILE`, `PRODUCT_TELEMETRY`,
    `SECURITY_AUDIT_LOG`, `LEGAL_HOLD_RECORD` and `BACKUP_ARCHIVE` all carry
    conditions. Which evidence establishes each one is unresolved. The
    activity stream hints at two: an attachment-catalog row states an object
    'belongs only to' one subject, and `CHECK_RESTORE_ACCESS` rows state an
    archive 'can still be restored'.
13. **`LINK_RESOURCE_IDENTITY` is a direct attribution channel, but tiny.**
    12 `data_catalog` rows name a resource, an email and a `user_id`
    together. 12 rows cannot attribute 518 cases, so the main attribution
    path has to come from evidence contents. Determine what those 12 rows
    are for.
14. **Replica drift as a mechanism.** `databases/` evidence names primary,
    regional, reporting and failover replicas, and the activity stream has
    55 `SYNC_RECORD` rows into `account_replication_cluster` plus one into
    `account_failover_replica`. Confirm whether drift between replicas is
    what `REPORT_REMAINING_RECORD` findings are built on.
15. **Recreation after deletion.** 31 rows read 'Active connector
    repopulated downstream resource search_#' and one reads 'Restore job
    repopulated downstream resource search_#'. These look like the
    `REPORT_ACTIVE_DATA_FLOW` and `REPORT_RESTORE_RISK` mechanisms; verify
    against the sample answers for those cases.
16. **Tombstones are rare in activity but present in steps.** 11
    `tombstone_*` activity rows and 4 tombstone steps in the samples. Check
    whether `REDACT_RECORD` on `identity_service` is always a tombstone.

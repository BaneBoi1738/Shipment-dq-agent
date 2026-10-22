# Data contract: shipment quality audit

What goes in, what comes out, and what you can rely on. This is the "data as a product" view of the pipeline.

## Input

| | |
|---|---|
| Where | `s3://<bucket>/incoming/*.csv` |
| Format | UTF-8 CSV with a header row (a UTF-8 BOM from Excel is fine) |
| Required columns | `shipment_id`, `carrier`, `origin`, `destination`, `status`, `picked_up_at`, `eta`, `last_update` |
| Optional column | `delivered_at` |
| Column names | Matched by exact name, known synonyms, fuzzy match (e.g. `Tracking Number` -> `shipment_id`). Extra columns are ignored and reported. |
| Timestamps | Any common format. No timezone means UTC. |
| Allowed `status` | `created, picked_up, in_transit, out_for_delivery, delivered, delayed, exception, cancelled` |

Synonyms, statuses and thresholds live in `src/dq_agent/default_config.yaml`.

## Output (per audited file)

All written to `s3://<bucket>/reports/`, named `<input-name>-<UTC timestamp>`:

| File | Purpose |
|---|---|
| `.md` | Human-readable report |
| `.json` | Same results plus `metrics`, machine-readable |
| `-errors.csv` | One line per bad row and problem: `row, shipment_id, check, severity`. `row` is the spreadsheet row number (header = row 1), so data owners can fix the source. |
| `.error.json` | Only when the input itself could not be audited (see failure behaviour) |

## Quality metrics (in the `.json` under `metrics`)

| Metric | Definition |
|---|---|
| `total_records` | Data rows in the file |
| `invalid_records` | Rows with at least one row-level problem. A row with three problems counts once. |
| `valid_records` | `total_records - invalid_records` |
| `error_rate` | `invalid_records / total_records` |
| `clean_rate` | `1 - error_rate` |
| `errors_by_type` | Check name -> number of affected rows. One row can appear under several checks, so these can add up to more than `invalid_records`. |
| `dataset_issues` | File-level problems: `data_freshness`, `missing_required_columns` |
| `findings_by_severity` | Count of critical and warning findings |
| `schema` | Columns renamed / ignored / required-but-missing |
| `sla` | `{min_clean_rate, met}` |

## SLA

A run **meets SLA** when `clean_rate >= 95%` (configurable under `sla.min_clean_rate`) **and** there is no dataset-level critical issue (stale data, or a required column missing). A file can have 100% clean rows and still miss the SLA if it is stale.

## Failure behaviour

| Situation | What happens | Retried? |
|---|---|---|
| Empty file, header only, not UTF-8 text, unparseable CSV | `.error.json` written, `input_rejected` logged (WARNING), invocation succeeds with `status: ERROR` | No, retrying cannot fix the file |
| Key not under `incoming/`, not `.csv`, or inside `reports/` | Skipped, `skipped` logged | No |
| Unexpected error (S3 outage, permissions, bug) | `audit_failed` logged (ERROR) with error type, then the exception is re-raised. Nothing partial is written. | Yes, by Lambda's retry behaviour |

Trade-off: swallowing every error would hide real outages, and raising on every error would retry bad files pointlessly. Splitting them by cause avoids both.

## Logging

One JSON object per line. Events: `audit_started`, `audit_complete`, `skipped`, `input_rejected`, `audit_failed`.

```json
{"ts": "2026-10-20T09:14:02+00:00", "level": "INFO", "event": "audit_complete", "request_id": "…",
 "key": "incoming/sample.csv", "status": "FAIL", "sla_met": false, "total_records": 505,
 "invalid_records": 61, "error_rate": 0.1208, "errors_by_type": {"duplicate_shipment_id": 10, "…": 0}}
```

CloudWatch Logs Insights examples (run them on the `/aws/lambda/dq-audit` log group):

```
fields @timestamp, key, status, sla_met, error_rate
| filter event = "audit_complete"
| sort @timestamp desc
```

```
fields @timestamp, key, error_type, error
| filter level = "ERROR" or event = "input_rejected"
```

## Not covered yet

Historical trends across runs, per-carrier thresholds, and automatic retries of rejected files. See the README roadmap.

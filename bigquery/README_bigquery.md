# Shipment Data Quality on Google BigQuery

The same validation logic as the AWS pipeline (S3 → Lambda → Athena), rebuilt as a SQL-first workflow on BigQuery.

## Data
`shipments_raw.csv`: about 100,400 synthetic shipment records with realistic data quality problems built in (missing tracking numbers, delivered-before-pickup dates, invalid statuses, negative weights, duplicate IDs and more). Carrier names are fictional.

## What the SQL does (`shipment_dq_bigquery.sql`)
1. **Validation view:** flags every record against 8 rules and collects the failures in an `ARRAY` column
2. **Silver and quarantine tables:** clean records go to `shipments_silver`, failed records to `shipments_quarantine`
3. **Carrier DQ scorecard:** failure rate per carrier, ranked with a window function
4. **Top failing rules:** `UNNEST` of the failure array with each rule's share of total failures
5. **Weekly trend:** failure rate per carrier with a 4-week rolling average
6. **KPI distortion:** on-time delivery % on raw vs clean data, showing how bad records skew a headline metric
7. **Lane performance:** average delay and late % by origin → destination on clean data

## Concepts used
CTEs, window functions (`ROW_NUMBER`, `RANK`, rolling `AVG`), arrays and `UNNEST`, `COUNTIF`, `DATE_TRUNC`, `DATE_DIFF`, views, and `CREATE TABLE AS SELECT`.

## Run it yourself
1. Open the BigQuery sandbox (free, no billing needed) and create a dataset called `shipment_dq`
2. Load `shipments_raw.csv` as table `shipments_raw` (schema auto-detect, skip 1 header row)
3. Run the blocks in `shipment_dq_bigquery.sql` in order

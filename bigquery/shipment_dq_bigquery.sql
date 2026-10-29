-- =====================================================================
-- Shipment Data Quality on BigQuery
-- Dataset: shipment_dq   |   Source table: shipment_dq.shipments_raw
-- Load shipments_raw.csv with schema auto-detect, header row skipped.
-- Run each block separately in the BigQuery console.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. VALIDATION VIEW: one row per shipment, every rule as a flag
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW shipment_dq.shipments_validated AS
WITH dedup AS (
  SELECT
    *,
    ROW_NUMBER() OVER (PARTITION BY shipment_id ORDER BY pickup_date) AS rn
  FROM shipment_dq.shipments_raw
),
flags AS (
  SELECT
    *,
    rn > 1                                                     AS is_duplicate,
    tracking_number IS NULL                                    AS missing_tracking,
    status IS NULL OR status NOT IN
      ('CREATED','PICKED_UP','IN_TRANSIT','OUT_FOR_DELIVERY',
       'DELIVERED','RTO','CANCELLED')                          AS invalid_status,
    status = 'DELIVERED' AND delivered_date IS NULL            AS delivered_no_date,
    delivered_date < pickup_date                               AS delivered_before_pickup,
    eta_date < pickup_date                                     AS eta_before_pickup,
    weight_kg <= 0                                             AS invalid_weight,
    origin_city = destination_city                             AS same_origin_dest
  FROM dedup
)
SELECT
  *,
  ARRAY(
    SELECT rule FROM UNNEST([
      IF(is_duplicate,                         'duplicate_id',            NULL),
      IF(missing_tracking,                     'missing_tracking',        NULL),
      IF(invalid_status,                       'invalid_status',          NULL),
      IF(delivered_no_date,                    'delivered_no_date',       NULL),
      IF(IFNULL(delivered_before_pickup,FALSE),'delivered_before_pickup', NULL),
      IF(IFNULL(eta_before_pickup,FALSE),      'eta_before_pickup',       NULL),
      IF(invalid_weight,                       'invalid_weight',          NULL),
      IF(same_origin_dest,                     'same_origin_dest',        NULL)
    ]) AS rule
    WHERE rule IS NOT NULL
  ) AS failed_rules
FROM flags;


-- ---------------------------------------------------------------------
-- 2. SILVER + QUARANTINE TABLES (same pattern as the S3/Athena lake)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE shipment_dq.shipments_silver AS
SELECT * EXCEPT (rn, failed_rules)
FROM shipment_dq.shipments_validated
WHERE ARRAY_LENGTH(failed_rules) = 0;

CREATE OR REPLACE TABLE shipment_dq.shipments_quarantine AS
SELECT shipment_id, carrier, status, pickup_date, failed_rules
FROM shipment_dq.shipments_validated
WHERE ARRAY_LENGTH(failed_rules) > 0;


-- ---------------------------------------------------------------------
-- 3. DATA QUALITY SCORECARD BY CARRIER
-- ---------------------------------------------------------------------
SELECT
  carrier,
  COUNT(*)                                                    AS total_records,
  COUNTIF(ARRAY_LENGTH(failed_rules) > 0)                     AS failed_records,
  ROUND(100 * COUNTIF(ARRAY_LENGTH(failed_rules) > 0) / COUNT(*), 2)
                                                              AS failure_rate_pct,
  RANK() OVER (ORDER BY COUNTIF(ARRAY_LENGTH(failed_rules) > 0) / COUNT(*) DESC)
                                                              AS worst_rank
FROM shipment_dq.shipments_validated
GROUP BY carrier
ORDER BY failure_rate_pct DESC;


-- ---------------------------------------------------------------------
-- 4. WHICH RULES FAIL MOST (UNNEST the failed_rules array)
-- ---------------------------------------------------------------------
SELECT
  rule,
  COUNT(*)                                                    AS failures,
  ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)            AS share_pct
FROM shipment_dq.shipments_validated,
     UNNEST(failed_rules) AS rule
GROUP BY rule
ORDER BY failures DESC;


-- ---------------------------------------------------------------------
-- 5. WEEKLY FAILURE-RATE TREND + 4-WEEK ROLLING AVERAGE PER CARRIER
-- ---------------------------------------------------------------------
WITH weekly AS (
  SELECT
    carrier,
    DATE_TRUNC(pickup_date, WEEK(MONDAY))                     AS week,
    COUNT(*)                                                  AS records,
    COUNTIF(ARRAY_LENGTH(failed_rules) > 0) / COUNT(*)        AS failure_rate
  FROM shipment_dq.shipments_validated
  GROUP BY carrier, week
)
SELECT
  carrier,
  week,
  records,
  ROUND(100 * failure_rate, 2)                                AS failure_rate_pct,
  ROUND(100 * AVG(failure_rate) OVER (
    PARTITION BY carrier ORDER BY week
    ROWS BETWEEN 3 PRECEDING AND CURRENT ROW), 2)             AS rolling_4wk_pct
FROM weekly
ORDER BY carrier, week;


-- ---------------------------------------------------------------------
-- 6. THE BUSINESS POINT: bad data distorts the on-time KPI
--    On-time delivery % on ALL delivered records vs CLEAN records only
-- ---------------------------------------------------------------------
WITH delivered_all AS (
  SELECT carrier, delivered_date, eta_date
  FROM shipment_dq.shipments_validated
  WHERE status = 'DELIVERED' AND delivered_date IS NOT NULL
),
delivered_clean AS (
  SELECT carrier, delivered_date, eta_date
  FROM shipment_dq.shipments_silver
  WHERE status = 'DELIVERED'
)
SELECT
  a.carrier,
  ROUND(100 * a.on_time / a.n, 2)                             AS on_time_pct_raw,
  ROUND(100 * c.on_time / c.n, 2)                             AS on_time_pct_clean,
  ROUND(100 * (a.on_time / a.n - c.on_time / c.n), 2)         AS kpi_distortion_pts
FROM (
  SELECT carrier, COUNT(*) AS n, COUNTIF(delivered_date <= eta_date) AS on_time
  FROM delivered_all GROUP BY carrier
) a
JOIN (
  SELECT carrier, COUNT(*) AS n, COUNTIF(delivered_date <= eta_date) AS on_time
  FROM delivered_clean GROUP BY carrier
) c USING (carrier)
ORDER BY ABS(a.on_time / a.n - c.on_time / c.n) DESC;


-- ---------------------------------------------------------------------
-- 7. LANE PERFORMANCE ON CLEAN DATA: avg delay by origin -> destination
-- ---------------------------------------------------------------------
SELECT
  origin_city,
  destination_city,
  COUNT(*)                                                    AS deliveries,
  ROUND(AVG(DATE_DIFF(delivered_date, eta_date, DAY)), 2)     AS avg_delay_days,
  ROUND(100 * COUNTIF(delivered_date > eta_date) / COUNT(*), 1)
                                                              AS late_pct
FROM shipment_dq.shipments_silver
WHERE status = 'DELIVERED'
GROUP BY origin_city, destination_city
HAVING deliveries >= 50
ORDER BY late_pct DESC
LIMIT 15;

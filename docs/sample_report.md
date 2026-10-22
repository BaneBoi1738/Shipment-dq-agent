# Shipment data quality: 🔴 FAIL

- **Source:** data/sample_shipments_drift.csv
- **Run:** 2026-10-13 03:10 UTC
- **Rows audited:** 505
- **Clean rows:** 87.9%
- **Findings:** 2 critical, 8 warning

## Schema drift

- `Tracking Number` → `shipment_id` (synonym, 0.97)
- `Carrier Name` → `carrier` (synonym, 0.97)
- `Origin City` → `origin` (synonym, 0.97)
- `Destination City` → `destination` (synonym, 0.97)
- `Shipment Status` → `status` (synonym, 0.97)
- `Pickup Time` → `picked_up_at` (synonym, 0.97)
- `Estimated Delivery` → `eta` (synonym, 0.97)
- `Delivery Date` → `delivered_at` (synonym, 0.97)
- `Last Updated` → `last_update` (synonym, 0.97)
- New/unrecognised columns (ignored): `Internal Notes`

## Findings

| | Check | Rows | Rate | Example IDs |
|---|---|---:|---:|---|
| 🔴 | `duplicate_shipment_id`: Shipment IDs that appear more than once | 10 | 2.0% | SHP-000074, SHP-000105, SHP-000156, SHP-000362, SHP-000375 |
| 🔴 | `delivered_before_pickup`: Delivered timestamp is earlier than pickup | 5 | 1.0% | SHP-000048, SHP-000066, SHP-000415, SHP-000434, SHP-000464 |
| 🟠 | `missing_required_values`: Rows with empty required fields | 11 | 2.2% | SHP-000044, SHP-000133, SHP-000191, SHP-000299, SHP-000301 |
| 🟠 | `stale_in_transit`: In-transit shipments with no update for over 48h | 11 | 2.2% | SHP-000016, SHP-000026, SHP-000087, SHP-000196, SHP-000203 |
| 🟠 | `timestamp_on_undelivered`: delivered_at is filled but status is not delivered | 9 | 1.8% | SHP-000012, SHP-000026, SHP-000034, SHP-000087, SHP-000102 |
| 🟠 | `invalid_status`: Status values outside the allowed list | 7 | 1.4% | SHP-000012, SHP-000034, SHP-000039, SHP-000102, SHP-000228 |
| 🟠 | `delivered_status_missing_timestamp`: Status is delivered but delivered_at is empty | 6 | 1.2% | SHP-000022, SHP-000063, SHP-000170, SHP-000237, SHP-000425 |
| 🟠 | `unparseable_timestamps`: Timestamps that could not be parsed | 4 | 0.8% | SHP-000089, SHP-000110, SHP-000182, SHP-000219 |
| 🟠 | `eta_before_pickup`: ETA is earlier than pickup | 4 | 0.8% | SHP-000018, SHP-000077, SHP-000229, SHP-000315 |
| 🟠 | `future_timestamps`: Timestamps set in the future | 3 | 0.6% | SHP-000096, SHP-000141, SHP-000144 |

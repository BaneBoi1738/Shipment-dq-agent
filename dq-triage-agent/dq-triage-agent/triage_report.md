# Shipment Data Quality: Triage Report

I saved 4 fix notes. Of 100,400 records, 8,618 are quarantined.

- **Nimbus Couriers (high):** It has the highest failure rate, 15.66%. It fails all eight rules at similar volume, and its weekly rate never dropped below 10.86%. This points to weak validation across its whole feed.
- **RapidMile (high):** Its rate is 13.55% and flat. It has the most `delivered_before_pickup` failures of any carrier (347), out of 1,844 platform-wide.
- **NorthStar Freight (medium):** It has 103 of the 400 `duplicate_id` failures, about 26%. Its weekly rate also rose from 5.13% to 7.08%.
- **Peak Transit (low):** All 25 sampled `invalid_weight` failures had negative weights, which looks like a sign error. Negative weights may not be specific to Peak Transit.

I didn't write notes for Kestrel, Orbit, Harbor or SwiftLine. I saw no carrier-specific pattern for them. Failures are spread evenly across rules and weeks, so I'd treat most of this as baseline noise. I also couldn't see per-carrier volumes, so the rates are the only comparison I had.

## Fix notes

- Nimbus Couriers — HIGH
**Root cause:** Carrier-wide, chronic data quality problem rather than one broken field. Nimbus has the highest quarantine rate and fails all eight rules at similar volume, which points to weak upstream validation or integration in its feed.
**Evidence:** Quarantine rate is 15.66%, the highest of 8 carriers (platform total 8,618 of 100,400 quarantined). Failures by rule: delivered_before_pickup 179, invalid_status 176, missing_tracking 160, invalid_weight 157, same_origin_dest 157, eta_before_pickup 147, delivered_no_date 131, duplicate_id 17. The weekly rate is persistently high and never fell below 10.86% in 21 weeks (range 10.86% to 26.83%, 19.03% in the week of 2026-10-19). Sample failures include the misspelled status 'delivred', null statuses, and negative weights.
**Recommended fix:** Ask Nimbus for a feed-level remediation plan. Their payload needs schema validation before submission: a status enum, a non-null tracking number, a positive weight, and date ordering (pickup <= eta, pickup <= delivered). Agree a target of under 5% within 4 weeks and review weekly.

- RapidMile — HIGH
**Root cause:** Persistent date-ordering errors, mainly delivered_date earlier than pickup_date, on top of generally weak validation. RapidMile has the largest absolute count of date-logic failures of any carrier.
**Evidence:** Quarantine rate is 13.55%, second highest, and flat at 11.3% to 16.05% across all 21 weeks, so it is not a one-off incident. Failures: delivered_before_pickup 347 (the largest count for any carrier, out of 1,844 platform-wide), invalid_weight 274, same_origin_dest 260, invalid_status 255, eta_before_pickup 248, delivered_no_date 247, missing_tracking 227, duplicate_id 69. Example: SHP0022823 has pickup 2026-07-12 and delivered 2026-07-10. SHP0040916 has an ETA a day before pickup.
**Recommended fix:** Have RapidMile check how delivered_date and pickup_date are populated: timezone conversion, swapped fields, or stale scan timestamps. Add a pre-submission check that rejects delivered < pickup and eta < pickup. Resend corrected records for the quarantined shipments.

- NorthStar Freight — MEDIUM
**Root cause:** NorthStar is over-represented in duplicate_id failures, which suggests shipment IDs are being reused or records are resent without dedup. Its failure rate is also trending up slightly.
**Evidence:** NorthStar has 103 of the 400 duplicate_id failures (about 26%), versus 69 for RapidMile, 38 for Peak Transit, 37 for Kestrel and 17 for Nimbus. Duplicates are only 7.5% of NorthStar's 1,378 failures but about 26% of all duplicate failures. The overall rate is only 6.21%, but the weekly rate rose from 5.13% (2026-06-01) to 7.08% (2026-10-19), and 7.00% the week before. delivered_before_pickup is also its top rule at 336. The sampled duplicates span every status (DELIVERED, IN_TRANSIT, RTO, CANCELLED and others), which suggests resent records rather than status updates.
**Recommended fix:** Ask NorthStar whether shipment events are being resent as new records or IDs are being reused. Have them send updates as upserts keyed on shipment_id and make IDs unique per shipment. On our side, confirm whether duplicates are exact copies, and dedupe on ingest if they are.

- Peak Transit — LOW
**Root cause:** Negative weight_kg values, likely a sign or unit error at the source. All 25 sampled invalid_weight failures were negative numbers rather than zero or null.
**Evidence:** Peak Transit has 119 invalid_weight failures. All 25 sampled records had negative weights (for example -30.13, -17.54, -1.1, -37.12) across statuses from CREATED to DELIVERED. The sampled absolute values (1 to 40 kg) are in the normal range, so the sign is probably flipped. Its overall quarantine rate is 9.36% with a flat weekly trend (4.35% to 11.18%). Platform-wide there are 1,068 invalid_weight failures, so this may not be specific to Peak Transit.
**Recommended fix:** Ask Peak Transit to check weight calculation and export for sign errors. Add a positive-weight constraint at the source. Once they confirm, we could normalize abs(weight) for their records on a case-by-case basis. Do not auto-correct without confirmation.

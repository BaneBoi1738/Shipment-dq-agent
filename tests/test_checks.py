import pandas as pd

from conftest import make_clean
from dq_agent.checks import run_checks


def names(findings):
    return {f.check for f in findings}


def test_clean_data_has_no_findings(cfg, now):
    findings, clean = run_checks(make_clean(), cfg, now=now)
    assert findings == [] and clean == 1.0


def test_duplicates_are_critical(cfg, now):
    df = make_clean()
    df.loc[5, "shipment_id"] = df.loc[4, "shipment_id"]
    findings, _ = run_checks(df, cfg, now=now)
    f = next(f for f in findings if f.check == "duplicate_shipment_id")
    assert f.severity == "critical" and f.count == 2


def test_missing_values_and_severity_escalation(cfg, now):
    df = make_clean(100)
    df.loc[0, "carrier"] = ""
    f1, _ = run_checks(df, cfg, now=now)
    assert next(f for f in f1 if f.check == "missing_required_values").severity == "warning"
    df.loc[:9, "carrier"] = "N/A"  # 10% >= 5% critical_rate
    f2, _ = run_checks(df, cfg, now=now)
    assert next(f for f in f2 if f.check == "missing_required_values").severity == "critical"


def test_invalid_status(cfg, now):
    df = make_clean()
    df.loc[2, "status"] = "deliverd"
    f = next(f for f in run_checks(df, cfg, now=now)[0] if f.check == "invalid_status")
    assert f.details["top_values"] == {"deliverd": 1}


def test_status_normalisation_accepts_case_and_hyphens(cfg, now):
    df = make_clean()
    df.loc[0, "status"] = "In-Transit"
    df.loc[0, "delivered_at"] = ""
    assert "invalid_status" not in names(run_checks(df, cfg, now=now)[0])


def test_unparseable_timestamps(cfg, now):
    df = make_clean()
    df.loc[1, "last_update"] = "TBD"
    f = next(f for f in run_checks(df, cfg, now=now)[0] if f.check == "unparseable_timestamps")
    assert f.details["by_column"] == {"last_update": 1}


def test_delivered_before_pickup_and_eta(cfg, now):
    df = make_clean()
    df.loc[3, "delivered_at"] = "2026-10-07 08:00:00"
    df.loc[4, "eta"] = "2026-10-01 08:00:00"
    found = names(run_checks(df, cfg, now=now)[0])
    assert {"delivered_before_pickup", "eta_before_pickup"} <= found


def test_delivered_status_consistency(cfg, now):
    df = make_clean()
    df.loc[0, "delivered_at"] = ""
    df.loc[1, "status"] = "in_transit"
    found = names(run_checks(df, cfg, now=now)[0])
    assert {"delivered_status_missing_timestamp", "timestamp_on_undelivered"} <= found


def test_future_timestamps(cfg, now):
    df = make_clean()
    df.loc[0, "last_update"] = "2026-10-20 00:00:00"
    assert "future_timestamps" in names(run_checks(df, cfg, now=now)[0])


def test_stale_in_transit(cfg, now):
    df = make_clean()
    df.loc[0, ["status", "delivered_at", "last_update"]] = ["in_transit", "", "2026-10-08 00:00:00"]
    assert "stale_in_transit" in names(run_checks(df, cfg, now=now)[0])


def test_freshness(cfg, now):
    df = make_clean()
    df["last_update"] = "2026-10-01 00:00:00"
    f = next(f for f in run_checks(df, cfg, now=now)[0] if f.check == "data_freshness")
    assert f.severity == "critical"


def test_missing_required_columns_is_critical(cfg, now):
    df = make_clean().drop(columns=["status"])
    findings, _ = run_checks(df, cfg, missing_required_columns=["status"], now=now)
    f = next(f for f in findings if f.check == "missing_required_columns")
    assert f.severity == "critical" and f.details["columns"] == ["status"]


def test_check_can_be_disabled(cfg, now):
    cfg["checks"]["duplicate_shipment_id"]["enabled"] = False
    df = make_clean()
    df.loc[5, "shipment_id"] = df.loc[4, "shipment_id"]
    assert "duplicate_shipment_id" not in names(run_checks(df, cfg, now=now)[0])


def test_clean_rate_and_sample_rows(cfg, now):
    df = make_clean(10)
    df.loc[2, "carrier"] = ""
    findings, clean = run_checks(df, cfg, now=now)
    assert clean == 0.9
    assert findings[0].sample_ids == ["SHP-002"] and findings[0].sample_rows == [4]  # sheet row = index + 2


def test_empty_frame(cfg, now):
    findings, clean = run_checks(make_clean(0), cfg, now=now)
    assert clean == 1.0

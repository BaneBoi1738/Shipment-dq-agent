import csv
import io

import pytest

from conftest import make_clean
from dq_agent.audit import run_audit
from dq_agent.generate_data import generate


@pytest.fixture
def result(cfg, now):
    return run_audit(generate(rows=500, seed=21, drift=True), cfg, source="t", now=None)


def test_metrics_are_internally_consistent(result):
    m = result.metrics
    assert m["total_records"] == result.rows
    assert m["valid_records"] + m["invalid_records"] == m["total_records"]
    assert m["error_rate"] == round(m["invalid_records"] / m["total_records"], 4)
    assert round(m["error_rate"] + m["clean_rate"], 3) == 1.0
    assert m["findings_by_severity"] == {"critical": len(result.critical), "warning": len(result.warnings)}


def test_errors_by_type_matches_findings(result):
    by_type = result.metrics["errors_by_type"]
    assert by_type == {f.check: f.count for f in result.findings if f.scope == "row"}
    assert "duplicate_shipment_id" in by_type and by_type["duplicate_shipment_id"] >= 2


def test_a_row_with_several_problems_counts_once(cfg, now):
    df = make_clean(10)
    # Empty carrier + misspelt status. The row still has a delivered_at, so a third check
    # (timestamp_on_undelivered) also fires: three problems, but only ONE invalid record.
    df.loc[3, ["carrier", "status"]] = ["", "deliverd"]
    r = run_audit(df, cfg, now=now)
    assert r.metrics["invalid_records"] == 1
    assert r.metrics["errors_by_type"] == {"missing_required_values": 1, "invalid_status": 1,
                                           "timestamp_on_undelivered": 1}


def test_sla_met_on_clean_data_and_not_on_dirty_data(cfg, now, result):
    assert run_audit(make_clean(), cfg, now=now).sla_met is True
    assert result.metrics["sla"] == {"min_clean_rate": 0.95, "met": False}


def test_sla_threshold_is_configurable(cfg, now):
    df = make_clean(100)
    df.loc[:2, "carrier"] = ""                              # 3% dirty
    assert run_audit(df, cfg, now=now).sla_met is True      # 97% >= 95%
    cfg["sla"]["min_clean_rate"] = 0.99
    assert run_audit(df, cfg, now=now).sla_met is False


def test_dataset_level_critical_breaks_sla_even_if_rows_are_clean(cfg, now):
    df = make_clean()
    df["last_update"] = "2026-09-01 00:00:00"               # stale file
    r = run_audit(df, cfg, now=now)
    assert r.clean_rate == 1.0 and r.sla_met is False
    assert r.metrics["dataset_issues"] == ["data_freshness"]
    assert r.metrics["errors_by_type"] == {}


def test_error_details_list_every_bad_row(result):
    details = result.error_details()
    assert len(details) == sum(result.metrics["errors_by_type"].values())
    assert {"row", "shipment_id", "check", "severity"} == set(details[0])
    assert details == sorted(details, key=lambda d: (d["row"], d["check"]))


def test_error_details_csv_round_trips(result):
    rows = list(csv.DictReader(io.StringIO(result.error_details_csv())))
    assert len(rows) == len(result.error_details())
    assert rows[0]["shipment_id"].startswith("SHP-")
    assert all(int(r["row"]) >= 2 for r in rows)            # row 1 is the header


def test_metrics_in_json_markdown_and_slack(result, cfg):
    from dq_agent.report import to_markdown
    from dq_agent.slack import summary_payload
    assert result.to_dict()["metrics"]["total_records"] == result.rows
    md = to_markdown(result)
    assert "## Quality metrics" in md and "Error rate" in md and "SLA" in md
    fields = [f["text"] for b in summary_payload(result, cfg)["blocks"] if "fields" in b for f in b["fields"]]
    assert any(t.startswith("*SLA") and "not met" in t for t in fields)


def test_cli_writes_error_csv(tmp_path):
    from dq_agent import cli
    src, out = tmp_path / "s.csv", tmp_path / "errors.csv"
    cli.main(["generate", "--rows", "100", "--out", str(src)])
    cli.main(["audit", "--path", str(src), "--output-errors", str(out)])
    assert out.read_text().startswith("row,shipment_id,check,severity")

import json

import pandas as pd

from dq_agent import cli
from dq_agent.audit import run_audit
from dq_agent.generate_data import generate


def summary(result):
    return {f.check: f.count for f in result.findings}


def test_generated_data_trips_the_planted_checks(cfg):
    result = run_audit(generate(rows=500, seed=7), cfg, source="test")
    found = summary(result)
    for check in ["duplicate_shipment_id", "missing_required_values", "invalid_status", "unparseable_timestamps",
                  "delivered_before_pickup", "delivered_status_missing_timestamp", "timestamp_on_undelivered",
                  "stale_in_transit", "eta_before_pickup", "future_timestamps"]:
        assert check in found, check
    assert result.status == "FAIL"
    assert 0.8 < result.clean_rate < 1.0


def test_schema_drift_gives_identical_findings(cfg):
    base = run_audit(generate(rows=400, seed=11), cfg, source="a")
    drifted = run_audit(generate(rows=400, seed=11, drift=True), cfg, source="b")
    assert summary(base) == summary(drifted)
    assert len(drifted.schema.renamed) == 9
    assert drifted.schema.unknown_columns == ["Internal Notes"]
    assert not drifted.schema.missing_required


def test_dropped_column_fails_loudly(cfg):
    df = generate(rows=100, seed=3, drift=True).drop(columns=["Shipment Status"])
    result = run_audit(df, cfg, source="x")
    assert "missing_required_columns" in summary(result)
    assert result.status == "FAIL"


def test_cli_generate_and_audit(tmp_path, capsys):
    csv = tmp_path / "s.csv"
    assert cli.main(["generate", "--rows", "200", "--drift", "--out", str(csv)]) == 0
    md, js = tmp_path / "r.md", tmp_path / "r.json"
    code = cli.main(["audit", "--path", str(csv), "--output-md", str(md), "--output-json", str(js), "--fail-on", "critical"])
    assert code == 1  # planted critical issues
    assert "Schema drift" in md.read_text()
    data = json.loads(js.read_text())
    assert data["status"] == "FAIL" and data["rows"] > 200


def test_cli_exit_codes(tmp_path):
    assert cli.main(["audit", "--source", "csv"]) == 2
    assert cli.main(["audit", "--path", str(tmp_path / "missing.csv")]) == 2
    csv = tmp_path / "s.csv"
    cli.main(["generate", "--rows", "50", "--out", str(csv)])
    assert cli.main(["audit", "--path", str(csv), "--fail-on", "never"]) == 0


def test_read_csv_keeps_blanks_as_text(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("a,b\n1,\n,2\n")
    from dq_agent.sources import read_csv
    df = read_csv(str(p))
    assert df.loc[0, "b"] == "" and df.loc[1, "a"] == ""
    assert isinstance(df, pd.DataFrame)


def test_output_files_written_even_if_stdout_closes(tmp_path, monkeypatch):
    csv = tmp_path / "s.csv"
    cli.main(["generate", "--rows", "50", "--out", str(csv)])
    js = tmp_path / "r.json"

    def broken_print(*a, **k):
        raise BrokenPipeError

    monkeypatch.setattr("builtins.print", broken_print)
    cli.main(["audit", "--path", str(csv), "--output-json", str(js)])
    monkeypatch.undo()
    assert js.exists()

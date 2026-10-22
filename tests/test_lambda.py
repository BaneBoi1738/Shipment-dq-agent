from dq_agent.generate_data import generate
from dq_agent.lambda_handler import lambda_handler


def test_lambda_handler_csv_dry_run(tmp_path, monkeypatch):
    csv = tmp_path / "s.csv"
    generate(rows=200, seed=1, drift=True).to_csv(csv, index=False)
    out = lambda_handler({"source": "csv", "path": str(csv), "dry_run": True})
    assert out["status"] == "FAIL" and out["critical"] >= 1
    assert all(d["status"] == "dry-run" for d in out["deliveries"])

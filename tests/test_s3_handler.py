import io
import json

import pytest

from dq_agent import s3_handler
from dq_agent.generate_data import generate


class FakeS3:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.puts = {}
        self.types = {}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.puts[(Bucket, Key)] = Body
        self.types[Key] = ContentType


def csv_bytes():
    return generate(rows=200, seed=9, drift=True).to_csv(index=False).encode()


def s3_event(bucket, key):
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


@pytest.fixture
def fake(monkeypatch):
    fake = FakeS3({("demo-bucket", "incoming/shipments.csv"): csv_bytes(),
                   ("demo-bucket", "incoming/my file.csv"): csv_bytes()})
    monkeypatch.setattr(s3_handler, "_s3_client", lambda: fake)
    for var in ("REPORT_PREFIX", "INPUT_PREFIX", "REPORT_BUCKET", "DQ_SEND_SLACK"):
        monkeypatch.delenv(var, raising=False)
    return fake


def test_audits_and_writes_report_to_s3(fake):
    out = s3_handler.lambda_handler(s3_event("demo-bucket", "incoming/shipments.csv"))
    r = out["results"][0]
    assert r["status"] == "FAIL" and r["critical"] >= 1 and r["schema_renamed"] == 9
    keys = sorted(k for (_, k) in fake.puts)
    assert len(keys) == 3 and all(k.startswith("reports/shipments-") for k in keys)
    assert sum(k.endswith("-errors.csv") for k in keys) == 1
    md = next(v for (_, k), v in fake.puts.items() if k.endswith(".md")).decode()
    assert "Schema drift" in md
    data = json.loads(next(v for (_, k), v in fake.puts.items() if k.endswith(".json") and not k.endswith(".error.json")))
    assert data["source"] == "s3://demo-bucket/incoming/shipments.csv"


def test_url_encoded_keys_are_decoded(fake):
    out = s3_handler.lambda_handler(s3_event("demo-bucket", "incoming/my+file.csv"))
    assert out["results"][0]["status"] == "FAIL"


def test_never_audits_its_own_reports(fake):
    out = s3_handler.lambda_handler(s3_event("demo-bucket", "reports/shipments-x.csv"))
    assert "skipped" in out["results"][0] and not fake.puts


def test_skips_non_csv_and_other_prefixes(fake):
    a = s3_handler.lambda_handler(s3_event("demo-bucket", "incoming/readme.txt"))
    b = s3_handler.lambda_handler(s3_event("demo-bucket", "elsewhere/shipments.csv"))
    assert "skipped" in a["results"][0] and "skipped" in b["results"][0] and not fake.puts


def test_manual_test_event(fake):
    out = s3_handler.lambda_handler({"bucket": "demo-bucket", "key": "incoming/shipments.csv"})
    assert out["results"][0]["metrics"]["total_records"] > 200


def test_bad_event_raises(fake):
    with pytest.raises(ValueError):
        s3_handler.lambda_handler({"hello": "world"})


def test_custom_report_bucket(fake, monkeypatch):
    monkeypatch.setenv("REPORT_BUCKET", "reports-bucket")
    s3_handler.lambda_handler(s3_event("demo-bucket", "incoming/shipments.csv"))
    assert all(b == "reports-bucket" for (b, _) in fake.puts)


def test_reports_declare_utf8_so_emoji_render_in_browsers(fake):
    s3_handler.lambda_handler(s3_event("demo-bucket", "incoming/shipments.csv"))
    assert all("charset=utf-8" in t for t in fake.types.values())


# ---------- structured logging ----------
def _log_lines(capsys):
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    return [json.loads(l) for l in lines]        # every line must be valid JSON


class Ctx:
    aws_request_id = "req-123"


def test_logs_are_json_lines_with_request_id(fake, capsys):
    s3_handler.lambda_handler(s3_event("demo-bucket", "incoming/shipments.csv"), Ctx())
    events = _log_lines(capsys)
    assert [e["event"] for e in events] == ["audit_started", "audit_complete"]
    done = events[-1]
    assert done["request_id"] == "req-123" and done["level"] == "INFO"
    assert done["total_records"] > 200 and "errors_by_type" in done and done["sla_met"] is False


def test_skips_are_logged(fake, capsys):
    s3_handler.lambda_handler(s3_event("demo-bucket", "reports/x.csv"))
    ev = _log_lines(capsys)[0]
    assert ev["event"] == "skipped" and "reports" in ev["reason"]


# ---------- failure handling ----------
@pytest.mark.parametrize("body, reason", [
    (b"", "empty"),
    (b"   \n  ", "empty"),
    (b"shipment_id,carrier,status\n", "no data rows"),
    (b"\xff\xfe\x00\x01 not text \x80\x81", "UTF-8"),
])
def test_bad_input_is_recorded_not_raised(fake, capsys, body, reason):
    fake.objects[("demo-bucket", "incoming/bad.csv")] = body
    out = s3_handler.lambda_handler(s3_event("demo-bucket", "incoming/bad.csv"))
    r = out["results"][0]
    assert r["status"] == "ERROR" and reason in r["error"]
    keys = [k for (_, k) in fake.puts]
    assert len(keys) == 1 and keys[0].endswith(".error.json")
    payload = json.loads(next(iter(fake.puts.values())))
    assert payload["status"] == "ERROR" and payload["retryable"] is False
    ev = _log_lines(capsys)
    assert ev[-1]["event"] == "input_rejected" and ev[-1]["level"] == "WARNING"


def test_utf8_bom_from_excel_exports_is_handled(fake):
    raw = generate(rows=50, seed=4, drift=True).to_csv(index=False).encode("utf-8-sig")
    fake.objects[("demo-bucket", "incoming/excel.csv")] = raw
    out = s3_handler.lambda_handler(s3_event("demo-bucket", "incoming/excel.csv"))
    assert out["results"][0]["schema_renamed"] == 9


def test_unexpected_errors_are_logged_then_reraised(fake, capsys, monkeypatch):
    def boom(Bucket, Key):
        raise RuntimeError("s3 is down")
    monkeypatch.setattr(fake, "get_object", boom)
    with pytest.raises(RuntimeError):
        s3_handler.lambda_handler(s3_event("demo-bucket", "incoming/shipments.csv"))
    last = _log_lines(capsys)[-1]
    assert last["event"] == "audit_failed" and last["level"] == "ERROR"
    assert last["error_type"] == "RuntimeError" and not fake.puts   # nothing half-written


def test_error_csv_content_is_in_the_output_bucket(fake):
    s3_handler.lambda_handler(s3_event("demo-bucket", "incoming/shipments.csv"))
    body = next(v for (_, k), v in fake.puts.items() if k.endswith("-errors.csv")).decode()
    assert body.splitlines()[0] == "row,shipment_id,check,severity" and len(body.splitlines()) > 10

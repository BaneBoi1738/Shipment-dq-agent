import json

from dq_agent import slack
from dq_agent.audit import run_audit
from dq_agent.generate_data import generate


def make_result(cfg):
    return run_audit(generate(rows=400, seed=5, drift=True), cfg, source="demo")


def test_summary_fits_slack_limits(cfg):
    p = slack.summary_payload(make_result(cfg), cfg)
    assert p["blocks"][0]["type"] == "header" and len(p["blocks"][0]["text"]["text"]) <= 150
    for b in p["blocks"]:
        if b["type"] == "section" and "text" in b:
            assert len(b["text"]["text"]) <= 3000
    json.dumps(p)  # serialisable


def test_routing_sends_findings_to_the_right_channels(cfg):
    msgs = slack.build_messages(make_result(cfg), cfg)
    routes = {m.route: m for m in msgs}
    assert {"default", "data-ops", "carrier-ops", "oncall"} <= set(routes)
    assert "duplicate_shipment_id" in json.dumps(routes["data-ops"].payload)
    assert "stale_in_transit" in json.dumps(routes["carrier-ops"].payload)
    assert "delivered_before_pickup" in json.dumps(routes["oncall"].payload)  # critical rule


def test_no_message_on_pass_unless_enabled(cfg):
    from conftest import make_clean
    from dq_agent.audit import AuditResult
    from dq_agent.schema import SchemaReport
    clean = AuditResult("x", "now", 10, 1.0, SchemaReport(), [])
    assert slack.build_messages(clean, cfg) == []
    cfg["report"]["post_on_pass"] = True
    assert [m.route for m in slack.build_messages(clean, cfg)] == ["default"]


def test_send_alerts_posts_skips_and_survives_failures(cfg, monkeypatch):
    sent = []

    def fake_post(url, payload, timeout=10):
        if "broken" in url:
            raise RuntimeError("500")
        sent.append(url)

    monkeypatch.setattr(slack, "post", fake_post)
    env = {"SLACK_WEBHOOK_URL": "https://hooks.test/default", "SLACK_WEBHOOK_DATA_OPS": "https://hooks.test/broken"}
    log = slack.send_alerts(make_result(cfg), cfg, env=env)
    status = {e["route"]: e["status"] for e in log}
    assert status["default"] == "sent"
    assert status["data-ops"].startswith("failed")
    assert status["carrier-ops"].startswith("skipped")
    assert sent == ["https://hooks.test/default"]


def test_dry_run_sends_nothing(cfg, monkeypatch):
    monkeypatch.setattr(slack, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not post")))
    log = slack.send_alerts(make_result(cfg), cfg, env={}, dry_run=True)
    assert log and all(e["status"] == "dry-run" for e in log)


def test_post_raises_on_non_200(monkeypatch):
    class R:
        status_code, text = 404, "no_service"
    monkeypatch.setattr(slack.requests, "post", lambda *a, **k: R())
    try:
        slack.post("https://hooks.test/x", {})
    except RuntimeError as e:
        assert "404" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_escape():
    assert slack._esc("a<b>&c") == "a&lt;b&gt;&amp;c"

from dq_agent.schema import map_schema, normalize

CANON = ["shipment_id", "carrier", "origin", "destination", "status", "picked_up_at", "eta", "delivered_at", "last_update"]


def test_normalize_variants():
    assert normalize("Shipment ID") == "shipment_id"
    assert normalize("shipmentId") == "shipment_id"
    assert normalize("  SHIPMENT-ID ") == "shipment_id"


def test_exact_match_has_no_drift(cfg):
    r = map_schema(CANON, cfg)
    assert not r.has_drift
    assert all(m.method == "exact" for m in r.mappings)


def test_synonyms_and_case(cfg):
    r = map_schema(["Tracking Number", "Carrier Name", "Origin City", "Destination City", "Shipment Status",
                    "Pickup Time", "Estimated Delivery", "Delivery Date", "Last Updated"], cfg)
    assert r.rename_map["Tracking Number"] == "shipment_id"
    assert r.rename_map["Estimated Delivery"] == "eta"
    assert r.rename_map["Last Updated"] == "last_update"
    assert not r.missing_required and not r.missing_optional


def test_fuzzy_typo(cfg):
    r = map_schema(["shipmnt_id", "carrier", "origin", "destination", "status", "picked_up_at", "eta", "last_update"], cfg)
    assert r.rename_map["shipmnt_id"] == "shipment_id"
    assert r.missing_optional == ["delivered_at"]


def test_contains_match(cfg):
    r = map_schema(["shipment_id_internal"] + CANON[1:], cfg)
    assert r.rename_map["shipment_id_internal"] == "shipment_id"


def test_short_names_are_not_fuzzy_matched(cfg):
    r = map_schema(["beta"] + [c for c in CANON if c != "eta"], cfg)
    assert "beta" in r.unknown_columns
    assert "eta" in r.missing_required


def test_one_to_one_and_unknown_columns(cfg):
    r = map_schema(CANON + ["Internal Notes", "carrier_id"], cfg)
    assert set(r.unknown_columns) == {"Internal Notes", "carrier_id"}
    assert len(set(m.canonical for m in r.mappings)) == len(r.mappings)


def test_missing_required_reported(cfg):
    r = map_schema([c for c in CANON if c != "status"], cfg)
    assert r.missing_required == ["status"]


def test_llm_fallback_used_only_for_leftovers(cfg):
    cols = [c for c in CANON if c != "carrier"] + ["Haulier"]
    calls = []

    def fake_llm(src, canon, samples):
        calls.append((src, canon))
        return {"Haulier": "carrier"}

    r = map_schema(cols, cfg, llm_fn=fake_llm)
    assert calls == [(["Haulier"], ["carrier"])]
    assert r.rename_map["Haulier"] == "carrier"
    assert r.renamed[0].method == "llm"


def test_llm_bad_answers_are_ignored(cfg):
    cols = [c for c in CANON if c != "carrier"] + ["Haulier"]
    r = map_schema(cols, cfg, llm_fn=lambda *_: {"Haulier": "status", "ghost": "carrier"})
    assert "Haulier" in r.unknown_columns and "carrier" in r.missing_required


def test_llm_errors_do_not_break_mapping(cfg):
    def boom(*_):
        raise RuntimeError("api down")

    r = map_schema(["Haulier"] + CANON[1:], cfg, llm_fn=boom)
    assert "shipment_id" in r.missing_required


def test_ship_id_maps_to_shipment_id():
    """Regression (Day 12): a file with `ship_id` used to fail with missing required column."""
    import pandas as pd
    from dq_agent.config import load_config
    from dq_agent.schema import map_schema
    rep = map_schema(pd.Index(["ship_id", "carrier", "origin", "dest_city", "status"]), load_config())
    assert "shipment_id" not in rep.missing_required

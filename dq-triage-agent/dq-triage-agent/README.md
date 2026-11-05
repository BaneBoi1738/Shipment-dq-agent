# Shipment DQ Triage Agent

Validation rules tell you **what** failed. This agent works out **why**.

My pipeline flags bad shipment records against 8 rules and quarantines them. At Shippeo, finding the failures was the easy part. The slow part was investigating each one, working out which carrier and which root cause was behind it, and writing it up so someone could fix it. This agent does that triage.

## How it works

```
shipments_raw.csv
      │
      ▼
validate.py ── 8 rules ──► clean records + quarantined records
                                  │
                                  ▼
                    agent.py (Claude, tool-use loop)
                      │  get_quarantine_summary
                      │  get_failed_records
                      │  get_carrier_breakdown
                      │  write_fix_note
                      ▼
               reports/triage_report.md
```

- **validate.py**: the same 8 checks as my AWS and BigQuery versions (duplicates, missing tracking, invalid status, delivered before pickup, and so on).
- **tools.py**: 4 tools the model can call. The model never sees the raw dataset. It has to investigate through the tools, which keeps every claim tied to a real number.
- **agent.py**: a plain tool-use loop on the Anthropic API. Claude decides which carriers and rules to dig into, looks at real failed records and weekly trends, then writes 3 to 6 prioritised fix notes with evidence.

## Design choices

- **Custom loop, no framework.** About 60 lines, so every step is visible and easy to debug.
- **Grounded output.** The system prompt forbids numbers that didn't come from a tool, and tool errors go back to the model instead of crashing the run.
- **Bounded.** Hard cap of 20 turns. The model is configurable with `TRIAGE_MODEL`.

## Run it

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key
python agent.py ../bigquery/shipments_raw.csv
```

Output: `reports/triage_report.md`.

The data is a 100K-row synthetic shipment dataset with realistic failure patterns built in. Carrier names are fictional.

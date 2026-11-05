"""Shipment DQ Triage Agent.

Validation rules tell you WHAT failed. This agent works out WHY:
it investigates the quarantined records with tools, decides which
carriers and issues matter most, and writes a fix note for each one.

Usage:
    export ANTHROPIC_API_KEY=...
    python agent.py ../bigquery/shipments_raw.csv
"""
import argparse
import os
import sys

import anthropic

from tools import TOOL_SCHEMAS, TriageTools
from validate import load, validate

MODEL = os.environ.get("TRIAGE_MODEL", "claude-sonnet-5-5")
MAX_TURNS = 20

SYSTEM = """You are a data quality analyst for a shipment visibility platform.
Records that failed validation are in quarantine. Your job is to triage them:

1. Start with the overall summary.
2. Pick the carriers and rules that matter most (high failure rate or high volume).
3. Look at real failed records and carrier trends before concluding anything.
4. For each real issue, call write_fix_note with a specific root cause, evidence
   that cites actual numbers from the tools, a concrete fix, and a priority.
   Write 3 to 6 notes in total. Only escalate what the data supports.
5. Finish with a short summary (under 150 words) of what you found.

Be specific and skeptical. Do not invent numbers you did not get from a tool."""


def run(csv_path: str, out_dir: str, verbose: bool = True) -> str:
    clean, quarantine = validate(load(csv_path))
    tools = TriageTools(clean, quarantine, out_dir)
    client = anthropic.Anthropic()

    messages = [{"role": "user", "content": "Triage the quarantined shipment records."}]

    for turn in range(MAX_TURNS):
        resp = client.messages.create(
            model=MODEL, max_tokens=2000, system=SYSTEM,
            tools=TOOL_SCHEMAS, messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            summary = "".join(b.text for b in resp.content if b.type == "text")
            path = tools.save_report(summary)
            if verbose:
                print(f"\nDone in {turn + 1} turns. {len(tools.notes)} fix notes -> {path}")
            return str(path)

        results = []
        for block in resp.content:
            if block.type == "tool_use":
                if verbose:
                    print(f"[turn {turn + 1}] {block.name}({block.input})")
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": tools.run(block.name, block.input),
                })
        messages.append({"role": "user", "content": results})

    path = tools.save_report("Stopped: reached the turn limit before finishing.")
    return str(path)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("csv", nargs="?", default="../bigquery/shipments_raw.csv")
    p.add_argument("--out", default="reports")
    args = p.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first.")
    run(args.csv, args.out)

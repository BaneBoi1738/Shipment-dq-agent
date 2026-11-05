#!/bin/bash
cd "$(dirname "$0")"
echo "Installing requirements..."
python3 -m pip install -q -r requirements.txt
if [ -z "$ANTHROPIC_API_KEY" ]; then
  read -rsp "Paste your Anthropic API key and press Enter: " ANTHROPIC_API_KEY; echo
  export ANTHROPIC_API_KEY
fi
python3 agent.py
[ -f reports/triage_report.md ] && open reports/triage_report.md

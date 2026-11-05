@echo off
cd /d "%~dp0"
echo Installing requirements...
python -m pip install -q -r requirements.txt
if "%ANTHROPIC_API_KEY%"=="" set /p ANTHROPIC_API_KEY=Paste your Anthropic API key and press Enter: 
python agent.py
if exist reports\triage_report.md start "" reports\triage_report.md
pause

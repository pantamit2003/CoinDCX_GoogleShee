name: Live V1-V6 Scanner (2H)

on:
  workflow_dispatch:
  schedule:
    # GitHub cron is UTC. Runs every 2 hours at even UTC hours, which
    # corresponds to 05:30, 07:30, 09:30 ... IST candle-close checkpoints.
    - cron: "0 0,2,4,6,8,10,12,14,16,18,20,22 * * *"

jobs:
  scan:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
      - name: Run 2H V1-V6 scanner
        env:
          PYTHONUNBUFFERED: "1"
        run: python live_v1_v6_scanner_2h.py

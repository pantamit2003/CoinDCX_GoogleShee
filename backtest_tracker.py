"""
backtest_tracker.py
====================
NAYA STANDALONE MODULE — koi existing script touch nahi karta.

MAQSAD: Jab intraday_spike_monitor.py mein koi spike detect ho, uska
outcome track karna — "spike ke N candles baad price upar gayi ya
neeche" (N = 1, 3, 5 candles = 15min / 45min / 1h15min).

intraday_spike_monitor.py isse sirf 2 jagah use karta hai:
    1. resolve_pending(dry_run=DRY_RUN)  — har run ki SHURUAAT mein call
       karo. Purani pending spikes check karta hai, jinke horizons
       close ho chuke hain unka result nikaal ke Sheet mein likh deta
       hai aur pending list se hata deta hai.
    2. add_pending(...)                   — jab naya spike detect ho,
       use tracking mein daalne ke liye.

DATA FILE: backtest_pending.json (isi folder mein, auto-create hota hai)
    [
      {
        "pair": "B-BTC_USDT",
        "trigger_type": "BOTH",
        "candle_color": "RED",
        "spike_time": "2026-08-11T06:45:00+00:00",
        "spike_close": 61234.5,
        "resolved": {"1": null, "3": null, "5": null}
      },
      ...
    ]

RESULT SHEET TAB: "Spike_Backtest_Results"
    Pair | Spike_Time_UTC | Candle_Color | Trigger_Type | Spike_Close |
    Price_After_1 | PctChg_1 | Price_After_3 | PctChg_3 |
    Price_After_5 | PctChg_5

IMPORTANT — CLOSED CANDLE ASSUMPTION:
    Yeh module maan ke chalta hai ki data.candles.get_candles() sirf
    CLOSED (poori ban chuki) candles deta hai — yeh fix data/candles.py
    mein already laga diya gaya hai (pehle sirf daily ke liye tha, ab
    saari intraday resolutions ke liye bhi hai). Agar yeh assumption
    galat nikle, toh yahan resolve hone wale outcomes bhi 1 candle
    aage-peeche shift ho sakte hain — is module ko badalne ki zaroorat
    nahi padegi, sirf candles.py ka fix sahi hona chahiye.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from data.candles import get_candles
import config

PENDING_FILE = os.path.join(os.path.dirname(__file__), "backtest_pending.json")
HORIZONS = [1, 3, 5]           # candles baad check karna hai (15/45/75 min)
RESOLUTION = "15"
RESOLUTION_MINUTES = 15
MAX_AGE_HOURS = 6              # itne ghante baad bhi resolve na ho paaye toh discard (pair delist/data-gap)
RESULTS_WORKSHEET_NAME = "Spike_Backtest_Results"

RESULTS_HEADER = [
    "Pair", "Spike_Time_UTC", "Candle_Color", "Trigger_Type", "Spike_Close",
    "Price_After_1", "PctChg_1", "Price_After_3", "PctChg_3",
    "Price_After_5", "PctChg_5",
]


# ============================================
# PENDING FILE HELPERS
# ============================================
def _load_pending():
    if not os.path.exists(PENDING_FILE):
        return []
    try:
        with open(PENDING_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_pending(entries):
    with open(PENDING_FILE, "w") as f:
        json.dump(entries, f, indent=2, default=str)


def _to_utc_dt(value):
    """String ya pandas Timestamp ko tz-aware UTC datetime mein convert karta hai."""
    dt = pd.to_datetime(value)
    if dt.tzinfo is None:
        dt = dt.tz_localize("UTC")
    return dt.to_pydatetime()


# ============================================
# PUBLIC: add_pending — naya spike track karna shuru karo
# ============================================
def add_pending(pair, trigger_type, candle_color, spike_time, spike_close):
    entries = _load_pending()
    entries.append({
        "pair": pair,
        "trigger_type": trigger_type,
        "candle_color": candle_color,
        "spike_time": str(_to_utc_dt(spike_time)),
        "spike_close": float(spike_close),
        "resolved": {str(h): None for h in HORIZONS},
    })
    _save_pending(entries)


# ============================================
# GOOGLE SHEETS — results tab
# ============================================
_sheets_client = None
_results_ws = None


def _get_results_worksheet():
    global _sheets_client, _results_ws
    if _results_ws is not None:
        return _results_ws
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(config.CREDENTIALS_FILE), scopes=scopes)
    _sheets_client = gspread.authorize(creds)
    spreadsheet = _sheets_client.open_by_key(config.SHEET_ID)
    try:
        _results_ws = spreadsheet.worksheet(RESULTS_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        _results_ws = spreadsheet.add_worksheet(
            title=RESULTS_WORKSHEET_NAME, rows=5000, cols=len(RESULTS_HEADER)
        )
        _results_ws.append_row(RESULTS_HEADER)
    return _results_ws


def _write_result_row(entry, dry_run=False):
    r = entry["resolved"]
    row = [
        entry["pair"],
        entry["spike_time"],
        entry["candle_color"],
        entry["trigger_type"],
        entry["spike_close"],
    ]
    for h in HORIZONS:
        val = r.get(str(h))
        if val is None:
            row += ["", ""]
        else:
            row += [val["price"], val["pct_change"]]

    if dry_run:
        print(f"  [backtest_tracker][DRY_RUN] Result row: {row}")
        return

    try:
        ws = _get_results_worksheet()
        ws.append_row(row)
    except Exception as e:
        print(f"  [backtest_tracker] Sheet mein result likhne mein error: {e}")


# ============================================
# PUBLIC: resolve_pending — har run ki shuruaat mein call karo
# ============================================
def resolve_pending(dry_run=False):
    entries = _load_pending()
    if not entries:
        return

    now_utc = datetime.now(timezone.utc)
    still_pending = []

    # Pair-wise ek hi baar candles fetch karo (efficient)
    pairs_needed = {e["pair"] for e in entries}
    candle_cache = {}
    for pair in pairs_needed:
        try:
            candle_cache[pair] = get_candles(pair=pair, resolution=RESOLUTION, days=2)
        except Exception as e:
            print(f"  [backtest_tracker] {pair} candles fetch error: {e}")
            candle_cache[pair] = None

    for entry in entries:
        spike_time = _to_utc_dt(entry["spike_time"])

        # Bahut purana ho gaya (pair delist ho gaya ho sakta hai, ya data-gap) -> discard
        if now_utc - spike_time > timedelta(hours=MAX_AGE_HOURS):
            print(f"  [backtest_tracker] {entry['pair']} @ {entry['spike_time']} stale ho gaya (>{MAX_AGE_HOURS}h), discard.")
            continue

        df = candle_cache.get(entry["pair"])
        if df is None or df.empty:
            still_pending.append(entry)
            continue

        df_times = df["Time"]
        if df_times.dt.tz is None:
            df_times = df_times.dt.tz_localize("UTC")

        for h in HORIZONS:
            key = str(h)
            if entry["resolved"].get(key) is not None:
                continue
            target_time = spike_time + timedelta(minutes=RESOLUTION_MINUTES * h)
            match_idx = df_times[df_times == target_time].index
            if len(match_idx) == 0:
                continue  # candle abhi tak aayi nahi (ya resolve hone ka wait hai)
            price_after = float(df.loc[match_idx[0], "Close"])
            pct_change = round(
                (price_after - entry["spike_close"]) / entry["spike_close"] * 100, 3
            )
            entry["resolved"][key] = {"price": price_after, "pct_change": pct_change}

        if all(v is not None for v in entry["resolved"].values()):
            print(f"  [backtest_tracker] RESOLVED: {entry['pair']} @ {entry['spike_time']}")
            _write_result_row(entry, dry_run=dry_run)
        else:
            still_pending.append(entry)

    _save_pending(still_pending)

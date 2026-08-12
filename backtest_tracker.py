"""
backtest_tracker.py  (v2 — Google Sheet based, GitHub Actions-safe)
=====================================================================
PEHLE (v1) local backtest_pending.json file use karta tha — lekin
GitHub Actions har run mein NAYI, FRESH virtual machine deta hai, isliye
wo file kabhi persist nahi hoti thi (har run ke baad gayab ho jaati thi).

FIX: Ab "pending spikes" Google Sheet ke ek tab mein store hoti hain
("Pending_Spikes") — jo hamesha persistent hai, GitHub Actions ke
ephemeral-VM problem se bilkul bach jaate hain.

POORA FLOW:
    1. intraday_spike_monitor.py mein jab naya spike detect ho:
       add_pending(...) call hota hai → row "Pending_Spikes" tab mein
       likha jaata hai.

    2. Har naye run ki SHURUAAT mein:
       resolve_pending(dry_run=...) call hota hai →
         - "Pending_Spikes" tab se saari pending spikes padhta hai
         - Har spike ke liye check karta hai ki 15/45/75 min baad ki
           candle data mein aa chuki hai ya nahi
         - Agar aa chuki hai (matlab poora 75-min tak track ho chuka):
             → result "Spike_Backtest_Results" tab mein likh deta hai
             → us spike ko "Pending_Spikes" se hata deta hai
         - Agar abhi tak nahi aayi:
             → wapas "Pending_Spikes" mein rakh deta hai (agle run mein
               phir check hoga)
         - Agar spike bahut purani ho gayi (MAX_AGE_HOURS se zyada,
           jaise pair delist ho gaya ho ya data-gap ho) → discard

IMPORTANT FIX (tolerance matching):
    v1 mein candle-time ka EXACT match dhoondte the, jo GitHub Actions
    ke late/early runs mein kabhi match hi nahi karta tha. Ab hum
    "closest candle within tolerance" dhoondte hain — zyada robust hai.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from data.candles import get_candles
from notifications.telegram_bot import send_telegram_message
import config


HORIZONS = [1, 3, 5]           # candles baad check karna hai (15/45/75 min)
RESOLUTION = "15"
RESOLUTION_MINUTES = 15
MAX_AGE_HOURS = 6               # itne ghante baad bhi resolve na ho paaye toh discard
MATCH_TOLERANCE_MINUTES = 7     # candle-time match karte waqt itni tolerance rakho

PENDING_WORKSHEET_NAME = "Pending_Spikes"
RESULTS_WORKSHEET_NAME = "Spike_Backtest_Results"

PENDING_HEADER = ["Pair", "Trigger_Type", "Candle_Color", "Spike_Time_UTC", "Spike_Close",
                   "Price_Position", "RVOL_20", "Confirmation_Status"]

RESULTS_HEADER = [
    "Pair", "Spike_Time_UTC", "Candle_Color", "Trigger_Type", "Spike_Close", "Price_Position",
    "Price_After_1", "PctChg_1", "Price_After_3", "PctChg_3",
    "Price_After_5", "PctChg_5", "Confirmation_Status",
]

CONFIRMATION_ALERT_RVOL_THRESHOLD = 6.0   # sirf strong-signal spikes ko hi confirmation-alert bhejo


# ============================================
# GOOGLE SHEETS CONNECTION (dono tabs ke liye shared)
# ============================================

_client = None
_pending_ws = None
_results_ws = None


def _connect():
    global _client
    if _client is None:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(str(config.CREDENTIALS_FILE), scopes=scopes)
        _client = gspread.authorize(creds)
    return _client


def _get_pending_worksheet():
    global _pending_ws
    if _pending_ws is not None:
        return _pending_ws

    client = _connect()
    spreadsheet = client.open_by_key(config.SHEET_ID)

    try:
        _pending_ws = spreadsheet.worksheet(PENDING_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        _pending_ws = spreadsheet.add_worksheet(
            title=PENDING_WORKSHEET_NAME, rows=500, cols=len(PENDING_HEADER) + 2
        )
        _pending_ws.append_row(PENDING_HEADER)

    return _pending_ws


def _get_results_worksheet():
    global _results_ws
    if _results_ws is not None:
        return _results_ws

    client = _connect()
    spreadsheet = client.open_by_key(config.SHEET_ID)

    try:
        _results_ws = spreadsheet.worksheet(RESULTS_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        _results_ws = spreadsheet.add_worksheet(
            title=RESULTS_WORKSHEET_NAME, rows=5000, cols=len(RESULTS_HEADER) + 2
        )
        _results_ws.append_row(RESULTS_HEADER)

    return _results_ws


# ============================================
# HELPERS
# ============================================

def _to_utc_dt(value):
    """String ya pandas Timestamp ko tz-aware UTC datetime mein convert karta hai."""
    dt = pd.to_datetime(value)
    if dt.tzinfo is None:
        dt = dt.tz_localize("UTC")
    return dt.to_pydatetime()


def _find_closest_candle(df, target_time, tolerance_minutes=MATCH_TOLERANCE_MINUTES):
    """
    df ke 'Time' column mein target_time ke sabse paas wali candle dhoondta
    hai, agar tolerance ke andar ho. Exact-match ki jagah ye zyada robust
    hai (GitHub Actions ke late-run scenarios mein bhi kaam karega).

    Return: (found: bool, close_price: float ya None)
    """
    found, row = _find_closest_row(df, target_time, tolerance_minutes)
    if found:
        return True, float(row["Close"])
    return False, None


def _find_closest_row(df, target_time, tolerance_minutes=MATCH_TOLERANCE_MINUTES):
    """
    Same as _find_closest_candle, lekin poori row (Open/High/Low/Close)
    return karta hai — confirmation-candle logic ko High/Low bhi chahiye,
    sirf Close nahi.

    Return: (found: bool, row ya None)
    """
    df_times = df["Time"]
    if df_times.dt.tz is None:
        df_times = df_times.dt.tz_localize("UTC")

    diffs = (df_times - target_time).abs()
    min_idx = diffs.idxmin()
    min_diff = diffs.loc[min_idx]

    if min_diff <= timedelta(minutes=tolerance_minutes):
        return True, df.loc[min_idx]

    return False, None


def _check_breakout_confirmation(df, spike_time, candle_color):
    """
    NAYA — 'confirmation candle' logic:
    - Candle N+1 (spike ke 15 min baad) = "indecision candle", uska High/Low note karo
    - Candle N+2 (spike ke 30 min baad) = "confirmation candle"
    - Agar N+2 ki High, N+1 ki High se upar nikal gayi -> CONFIRMED_CONTINUATION
    - Agar N+2 ki Low, N+1 ki Low se neeche gayi -> FAILED_BREAKOUT
    - Dono nahi tuti -> STILL_UNDECIDED
    - Agar N+2 ki candle abhi data mein nahi aayi -> None (abhi wait karo)

    Return: confirmation_status string, ya None agar abhi determine nahi ho sakta
    """
    target_n1 = spike_time + timedelta(minutes=RESOLUTION_MINUTES * 1)
    target_n2 = spike_time + timedelta(minutes=RESOLUTION_MINUTES * 2)

    found_n1, row_n1 = _find_closest_row(df, target_n1)
    found_n2, row_n2 = _find_closest_row(df, target_n2)

    if not found_n1 or not found_n2:
        return None  # abhi itna time nahi guzra

    high_n1, low_n1 = float(row_n1["High"]), float(row_n1["Low"])
    high_n2, low_n2 = float(row_n2["High"]), float(row_n2["Low"])

    broke_high = high_n2 > high_n1
    broke_low = low_n2 < low_n1

    if candle_color == "GREEN":
        if broke_high:
            return "CONFIRMED_CONTINUATION"
        elif broke_low:
            return "FAILED_BREAKOUT"
        else:
            return "STILL_UNDECIDED"
    else:
        if broke_low:
            return "CONFIRMED_CONTINUATION"
        elif broke_high:
            return "FAILED_BREAKOUT"
        else:
            return "STILL_UNDECIDED"


# ============================================
# PUBLIC: add_pending — naya spike track karna shuru karo
# ============================================

def add_pending(pair, trigger_type, candle_color, spike_time, spike_close,
                 price_position="UNKNOWN", rvol_20=0.0):
    ws = _get_pending_worksheet()
    ws.append_row([
        pair,
        trigger_type,
        candle_color,
        str(_to_utc_dt(spike_time)),
        float(spike_close),
        price_position,
        round(float(rvol_20), 2),
        "PENDING",   # Confirmation_Status shuru mein hamesha PENDING
    ])


# ============================================
# PUBLIC: resolve_pending — har run ki shuruaat mein call karo
# ============================================

def resolve_pending(dry_run=False):
    pending_ws = _get_pending_worksheet()
    records = pending_ws.get_all_records()

    if not records:
        print("  [backtest_tracker] Koi pending spike nahi hai.")
        return

    now_utc = datetime.now(timezone.utc)
    max_horizon = max(HORIZONS)

    # Pair-wise ek hi baar candles fetch karo (efficient)
    pairs_needed = {r["Pair"] for r in records}
    candle_cache = {}
    for pair in pairs_needed:
        try:
            candle_cache[pair] = get_candles(pair=pair, resolution=RESOLUTION, days=2)
        except Exception as e:
            print(f"  [backtest_tracker] {pair} candles fetch error: {e}")
            candle_cache[pair] = None

    still_pending = []
    resolved_count = 0
    discarded_count = 0
    confirmation_sent_count = 0

    for row in records:
        pair = row["Pair"]
        spike_time = _to_utc_dt(row["Spike_Time_UTC"])
        spike_close = float(row["Spike_Close"])
        candle_color = row["Candle_Color"]
        rvol_20 = float(row.get("RVOL_20", 0) or 0)
        confirmation_status = row.get("Confirmation_Status", "PENDING")

        # Bahut purana ho gaya (pair delist ho gaya ho sakta hai, data-gap) -> discard
        if now_utc - spike_time > timedelta(hours=MAX_AGE_HOURS):
            print(f"  [backtest_tracker] {pair} @ {row['Spike_Time_UTC']} stale ho gaya "
                  f"(>{MAX_AGE_HOURS}h), discard.")
            discarded_count += 1
            continue

        df = candle_cache.get(pair)
        if df is None or df.empty:
            still_pending.append(row)
            continue

        # ---- CONFIRMATION-CANDLE CHECK (agar abhi tak nahi hua) ----
        if confirmation_status == "PENDING":
            new_status = _check_breakout_confirmation(df, spike_time, candle_color)
            if new_status is not None:
                confirmation_status = new_status
                row["Confirmation_Status"] = new_status

                # Sirf strong-signal spikes (RVOL_20 >= threshold) ko hi
                # confirmation Telegram alert bhejo
                if rvol_20 >= CONFIRMATION_ALERT_RVOL_THRESHOLD:
                    confirmation_sent_count += 1
                    emoji = {"CONFIRMED_CONTINUATION": "✅", "FAILED_BREAKOUT": "❌",
                             "STILL_UNDECIDED": "⚪"}.get(new_status, "")
                    msg = (
                        f"{emoji} <b>CONFIRMATION UPDATE — {pair}</b>\n\n"
                        f"Spike Time: {row['Spike_Time_UTC']}\n"
                        f"Status: <b>{new_status}</b>\n\n"
                        f"(Indecision candle ke High/Low ke against 2nd candle ka result)"
                    )
                    if dry_run:
                        print(f"  [DRY_RUN] Confirmation Telegram: {msg}")
                    else:
                        try:
                            send_telegram_message(msg)
                        except Exception as e:
                            print(f"  [backtest_tracker] Confirmation Telegram error: {e}")

        # Poore max_horizon (75 min) tak ki candle chahiye tabhi fully resolve hoga
        max_target_time = spike_time + timedelta(minutes=RESOLUTION_MINUTES * max_horizon)
        found_max, _ = _find_closest_candle(df, max_target_time)

        if not found_max:
            # Abhi itna time nahi guzra, agle run mein try karenge
            still_pending.append(row)
            continue

        # Sab horizons ke liye price nikal lo
        result_row = [
            pair,
            row["Spike_Time_UTC"],
            row["Candle_Color"],
            row["Trigger_Type"],
            spike_close,
            row.get("Price_Position", "UNKNOWN"),
        ]

        all_found = True
        for h in HORIZONS:
            target_time = spike_time + timedelta(minutes=RESOLUTION_MINUTES * h)
            found, price = _find_closest_candle(df, target_time)
            if not found:
                all_found = False
                break
            pct_change = round((price - spike_close) / spike_close * 100, 3)
            result_row += [price, pct_change]

        if not all_found:
            still_pending.append(row)
            continue

        result_row.append(confirmation_status)

        # Fully resolved — result likh do
        if dry_run:
            print(f"  [backtest_tracker][DRY_RUN] RESOLVED: {result_row}")
        else:
            try:
                _get_results_worksheet().append_row(result_row)
            except Exception as e:
                print(f"  [backtest_tracker] Results likhne mein error: {e}")
                still_pending.append(row)  # fail hua to pending mein wapas rakho
                continue

        resolved_count += 1

    # Pending_Spikes tab ko naye (updated) list se overwrite karo
    if not dry_run:
        try:
            clean_rows = [[r["Pair"], r["Trigger_Type"], r["Candle_Color"],
                            r["Spike_Time_UTC"], r["Spike_Close"],
                            r.get("Price_Position", "UNKNOWN"),
                            r.get("RVOL_20", 0),
                            r.get("Confirmation_Status", "PENDING")] for r in still_pending]
            pending_ws.clear()
            pending_ws.update([PENDING_HEADER] + clean_rows)
        except Exception as e:
            print(f"  [backtest_tracker] Pending_Spikes update karne mein error: {e}")
    else:
        print(f"  [backtest_tracker][DRY_RUN] Pending list update hoti: "
              f"{len(still_pending)} still pending.")

    print(f"  [backtest_tracker] Resolved: {resolved_count} | "
          f"Still pending: {len(still_pending)} | Discarded (stale): {discarded_count} | "
          f"Confirmation alerts sent: {confirmation_sent_count}")

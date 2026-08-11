"""
backtest_historical.py
========================
SIMPLE STANDALONE BACKTEST — GitHub Actions/persistence ka koi jhanjhat nahi.

KYA KARTA HAI:
Pichle N dino ka 15-min candle data ek hi baar laata hai, poore data pe
RVOL_20 aur RVOL_96 calculate karta hai, jahan bhi threshold cross hua
wahan "spike" mark karta hai, aur dekhta hai ki spike ke 1/3/5 candles
baad price kitna upar/neeche gaya.

Sab kuch EK HI SCRIPT RUN mein ho jaata hai — kyunki saara data (spike +
uske baad ka price) already available hai (past ka data hai), live
tracking ki zaroorat nahi.

CHALANE KA TARIKA:
    python backtest_historical.py

OUTPUT:
- Console mein summary table (har trigger-type ka win-rate, avg % move)
- backtest_results.csv file (detailed row-by-row data, Excel mein khol
  sakte ho)
"""

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from data.candles import get_candles
from exchange.coindcx import get_active_pairs
import config


# ============================================
# CONFIG
# ============================================

DAYS_OF_HISTORY = 30       # kitne purane din ka data backtest karna hai
RESOLUTION = "15"          # 15-min candles
LOOKBACK_SHORT = 20
LOOKBACK_LONG = 96

RVOL_SHORT_THRESHOLD = 5.0
RVOL_LONG_THRESHOLD = 3.0

HORIZONS = [1, 3, 5]       # spike ke kitne candles baad check karna hai

# Testing ke liye pehle sirf kuch coins pe try karo, phir saaron pe
PAIRS_TO_TEST = ["B-SQD_USDT", "B-VELODROME_USDT"]
# Saare active pairs pe backtest karne ke liye ye line use karo (dhire chalega):
# PAIRS_TO_TEST = get_active_pairs()

WORKSHEET_NAME = "Backtest_Historical_Results"
WRITE_TO_SHEET = True   # False karo agar sirf CSV/console chahiye, Sheet pe nahi likhna


# ============================================
# RVOL CALCULATION (same function jo live monitor mein hai)
# ============================================

def get_intraday_rvol(df, lookback_periods):
    df = df.copy()
    for period in lookback_periods:
        avg_col = f"avg_vol_{period}"
        df[avg_col] = df["Volume"].rolling(window=period).mean().shift(1)
        rvol_col = f"RVOL_{period}"
        df[rvol_col] = df["Volume"] / df[avg_col]
        df.drop(columns=[avg_col], inplace=True)
    return df


def classify_trigger_type(rvol_20, rvol_96):
    cond_short = rvol_20 >= RVOL_SHORT_THRESHOLD
    cond_long = rvol_96 >= RVOL_LONG_THRESHOLD
    if cond_short and cond_long:
        return "BOTH"
    elif cond_short:
        return "SHORT_ONLY"
    elif cond_long:
        return "LONG_ONLY"
    return None


# ============================================
# EK PAIR KA BACKTEST
# ============================================

def backtest_pair(pair):
    """
    Ek pair ka poora history laata hai, saari spikes dhoondhta hai,
    aur har spike ke baad ka outcome (1/3/5 candles) nikaal ke
    list of dicts return karta hai.
    """
    results = []

    try:
        df = get_candles(pair=pair, resolution=RESOLUTION, days=DAYS_OF_HISTORY)
    except Exception as e:
        print(f"  {pair}: candles fetch error - {e}")
        return results

    if df.empty or len(df) < LOOKBACK_LONG + max(HORIZONS) + 1:
        print(f"  {pair}: itna data nahi hai backtest ke liye, skip.")
        return results

    df = df.reset_index(drop=True)
    df = get_intraday_rvol(df, lookback_periods=[LOOKBACK_SHORT, LOOKBACK_LONG])

    max_horizon = max(HORIZONS)

    # Last 'max_horizon' candles chhod do kyunki unka future-outcome
    # nikalne ke liye pura data nahi hoga
    for i in range(LOOKBACK_LONG, len(df) - max_horizon):
        row = df.iloc[i]
        rvol_20 = row[f"RVOL_{LOOKBACK_SHORT}"]
        rvol_96 = row[f"RVOL_{LOOKBACK_LONG}"]

        if pd.isna(rvol_20) or pd.isna(rvol_96):
            continue

        trigger_type = classify_trigger_type(rvol_20, rvol_96)
        if trigger_type is None:
            continue

        spike_open = row["Open"]
        spike_close = row["Close"]
        spike_time = row["Time"]

        # Spike candle KHUD kis direction mein bani - UP (green) ya DOWN (red)
        spike_direction = "UP" if spike_close >= spike_open else "DOWN"

        entry = {
            "Pair": pair,
            "Spike_Time": spike_time,
            "Trigger_Type": trigger_type,
            "RVOL_20": round(rvol_20, 2),
            "RVOL_96": round(rvol_96, 2),
            "Spike_Open": spike_open,
            "Spike_Close": spike_close,
            "Spike_Direction": spike_direction,
        }

        for h in HORIZONS:
            future_close = df.iloc[i + h]["Close"]
            future_time = df.iloc[i + h]["Time"]
            pct_change = round((future_close - spike_close) / spike_close * 100, 3)

            # CONTINUED = spike jis direction mein bani, price usi direction
            #             mein aage bhi gaya
            # REVERSED  = ulti direction mein chala gaya
            # FLAT      = mushkil se hila (0.05% se kam move)
            if abs(pct_change) < 0.05:
                outcome = "FLAT"
            elif (spike_direction == "UP" and pct_change > 0) or \
                 (spike_direction == "DOWN" and pct_change < 0):
                outcome = "CONTINUED"
            else:
                outcome = "REVERSED"

            minutes_later = h * 15
            # Naam jaan-bujh kar "Check_15min" jaisa rakha hai — taaki ye
            # exactly wahi dikhaye jo live automation karegi: candle close
            # hone ke X minute baad wapas check karna
            entry[f"Check_{minutes_later}min_Time"] = future_time
            entry[f"Check_{minutes_later}min_Close"] = future_close
            entry[f"Check_{minutes_later}min_PctChg"] = pct_change
            entry[f"Check_{minutes_later}min_Status"] = outcome

        results.append(entry)

    return results


# ============================================
# SUMMARY BANAO
# ============================================

def print_summary(all_results_df):
    print(f"\n{'=' * 70}")
    print("BACKTEST SUMMARY")
    print('=' * 70)

    if all_results_df.empty:
        print("Koi spike nahi mila diye gaye thresholds pe. Thresholds kam karke dekho.")
        return

    for trigger_type in ["BOTH", "SHORT_ONLY", "LONG_ONLY"]:
        for direction in ["UP", "DOWN"]:
            subset = all_results_df[
                (all_results_df["Trigger_Type"] == trigger_type) &
                (all_results_df["Spike_Direction"] == direction)
            ]
            if subset.empty:
                continue

            print(f"\n--- {trigger_type} | Spike Direction = {direction} "
                  f"({len(subset)} spikes) ---")

            for h in HORIZONS:
                minutes_later = h * 15
                pct_col = f"Check_{minutes_later}min_PctChg"
                status_col = f"Check_{minutes_later}min_Status"

                continued_pct = (subset[status_col] == "CONTINUED").mean() * 100
                reversed_pct = (subset[status_col] == "REVERSED").mean() * 100
                flat_pct = (subset[status_col] == "FLAT").mean() * 100
                avg_move = subset[pct_col].mean()

                print(f"  {minutes_later} min baad: "
                      f"CONTINUED={continued_pct:.1f}% | REVERSED={reversed_pct:.1f}% | "
                      f"FLAT={flat_pct:.1f}% | Avg_Move={avg_move:+.2f}%")


# ============================================
# GOOGLE SHEET MEIN LIKHNA
# ============================================

def write_to_google_sheet(df_results):
    """
    Backtest results ko Google Sheet ke naye tab mein likhta hai.
    Har run pehle poora tab clear karta hai, phir fresh data daalta hai
    (taaki purana test-data mix na ho naye ke saath).
    """
    if df_results.empty:
        print("Koi results nahi hain, Sheet update skip kar rahe hain.")
        return

    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(str(config.CREDENTIALS_FILE), scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(config.SHEET_ID)

        try:
            ws = spreadsheet.worksheet(WORKSHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(
                title=WORKSHEET_NAME, rows=5000, cols=len(df_results.columns) + 2
            )

        # Sab kuch string mein convert karo (timestamps/NaN Google Sheets
        # ko crash kar sakte hain agar raw object type gaye)
        clean = df_results.copy()
        clean = clean.astype(str)

        ws.clear()
        ws.update([clean.columns.tolist()] + clean.values.tolist())

        print(f"\nGoogle Sheet '{WORKSHEET_NAME}' tab update ho gaya "
              f"({len(df_results)} rows).")

    except Exception as e:
        print(f"\nGoogle Sheet mein likhne mein error: {e}")


# ============================================
# MAIN
# ============================================

if __name__ == "__main__":
    print(f"Backtest shuru: {len(PAIRS_TO_TEST)} pairs, pichle {DAYS_OF_HISTORY} din, "
          f"resolution={RESOLUTION}min\n")

    all_results = []

    for pair in PAIRS_TO_TEST:
        print(f"Processing {pair}...")
        pair_results = backtest_pair(pair)
        print(f"  {len(pair_results)} spikes mile.")
        all_results.extend(pair_results)

    df_results = pd.DataFrame(all_results)

    if not df_results.empty:
        df_results.to_csv("backtest_results.csv", index=False)
        print(f"\nDetailed results 'backtest_results.csv' mein save ho gaye "
              f"({len(df_results)} rows).")

        if WRITE_TO_SHEET:
            write_to_google_sheet(df_results)

    print_summary(df_results)

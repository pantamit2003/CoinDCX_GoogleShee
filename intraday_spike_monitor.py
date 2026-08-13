"""
intraday_spike_monitor.py
==========================
NAYA STANDALONE SCRIPT — existing check_watchlist.py / daily_breakout_scan.py /
volume_watch.py ko bilkul touch nahi karta.

KYA KARTA HAI:
- Har 15 minute pe (GitHub Actions cron ke through) chalta hai
- Saare active futures pairs ke 15-min candles CoinDCX se laata hai
- Sirf abhi-abhi CLOSE hui candle ka RVOL check karta hai (RVOL_20 aur RVOL_96)
- Ab Support/Resistance ke against bhi price ki position classify karta hai
  (support_resistance.py module se) — taaki pata chale spike breakout wali
  hai ya kisi level ke paas ruki hui

BACKTESTING KE LIYE — 3 INDEPENDENT CONDITIONS:
Ab hum ek hi combined (AND) condition pe bharosa nahi karte. Teeno alag-alag
check hote hain, taaki baad mein backtest karke pata chale kaunsa rule
sabse zyada reliable/profitable hai:
    1. SHORT_ONLY — sirf RVOL_20 >= threshold pass hua, RVOL_96 nahi
    2. LONG_ONLY  — sirf RVOL_96 >= threshold pass hua, RVOL_20 nahi
    3. BOTH       — dono RVOL_20 aur RVOL_96 pass hue (sabse strict)

Har category ka Sheet mein record hota hai (Trigger_Type column ke saath) —
lekin TELEGRAM sirf tabhi jaata hai jab RVOL_20 >= RVOL_20_ALERT_THRESHOLD
ho (taaki phone pe sirf strong signals ke notifications aayein, weak wale
sirf Sheet mein log ho jaayein backtesting ke liye).

NAYA — PRICE_POSITION column:
Har spike ke saath ab ye bhi record hota hai ki price kis S/R zone mein
hai: NEAR_RESISTANCE, BREAKOUT_ABOVE_RESISTANCE, NEAR_SUPPORT,
BREAKDOWN_BELOW_SUPPORT, ya MID_RANGE. Isse baad mein backtest se pata
chalega ki breakout-wali spikes zyada reliable hain ya nahi.

GITHUB ACTIONS VERSION:
Ye script ek baar scan karke exit ho jaati hai. GitHub Actions ka cron
schedule khud har 15 minute pe naya run trigger karega.

CHALANE KA TARIKA (local testing ke liye bhi):
    python intraday_spike_monitor.py
"""

import time
from datetime import datetime, timezone, timedelta

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from data.candles import get_candles
from exchange.coindcx import get_active_pairs
from notifications.telegram_bot import send_telegram_message
from support_resistance import get_support_resistance, classify_price_position
import backtest_tracker
import config


# ============================================
# CONFIG — pehle inhe apni marzi se set karo
# ============================================

DRY_RUN = False         # True = sirf console print, Telegram/Sheet pe kuch nahi jayega
RESOLUTION = "15"       # 15-min candles
LOOKBACK_SHORT = 20     # ~5 hours ka baseline
LOOKBACK_LONG = 96      # ~1 din ka baseline

RVOL_SHORT_THRESHOLD = 5.0   # RVOL_20 kam se kam itna hona chahiye (Sheet-logging ke liye)
RVOL_LONG_THRESHOLD = 6.0    # RVOL_96 kam se kam itna hona chahiye (Sheet-logging ke liye)

RVOL_20_ALERT_THRESHOLD = 6.0   # Telegram alert SIRF tabhi jayega jab RVOL_20 isse zyada ho
                                  # (RVOL_96 kuch bhi ho, koi restriction nahi)

SR_LOOKBACK = 50              # kitne candles se S/R levels nikaalne hain
SR_CLUSTER_TOLERANCE_PCT = 0.5   # itne % ke andar wale swing points ek level maane jaayenge
SR_PROXIMITY_PCT = 0.5           # itne % ke andar ho to "NEAR" maana jaayega

MAX_PAIRS_TO_SCAN = 10    # None = saare active pairs, ya testing ke liye number daalo jaise 20
SLEEP_BETWEEN_PAIRS = 0.3    # API ko overload na karein, har pair ke beech thoda ruk jao (seconds)

WORKSHEET_NAME = "Intraday_Spike_Alerts"

# Agar sirf specific coins pe test karna hai (jaise abhi), yahan list daal do.
TEST_ONLY_PAIRS = []  # example: ["B-SQD_USDT", "B-VELODROME_USDT"]

# Sheet header — ab "Price_Position" column bhi add hua hai
SHEET_HEADER = [
    "Detected_At_IST", "Candle_Time_UTC", "Pair", "Trigger_Type",
    "Close", "Volume", "RVOL_20", "RVOL_96", "Price_Position",
]


# ============================================
# TIME HELPERS
# ============================================

def to_ist(utc_dt):
    """UTC candle time ko IST (Indian Standard Time) string mein convert karta hai."""
    dt = pd.to_datetime(utc_dt)
    if dt.tzinfo is None:
        dt = dt.tz_localize("UTC")
    ist_dt = dt + pd.Timedelta(hours=5, minutes=30)
    return ist_dt.strftime("%Y-%m-%d %H:%M:%S")


# ============================================
# RVOL CALCULATION
# ============================================

def get_intraday_rvol(df, lookback_periods):
    """
    Har candle ka RVOL nikalta hai, uske pichle N candles ke average se
    compare karke. shift(1) isliye taaki current candle apne khud ke
    average mein shaamil na ho.
    """
    df = df.copy()

    for period in lookback_periods:
        avg_col = f"avg_vol_{period}"
        df[avg_col] = df["Volume"].rolling(window=period).mean().shift(1)

        rvol_col = f"RVOL_{period}"
        df[rvol_col] = df["Volume"] / df[avg_col]

        df.drop(columns=[avg_col], inplace=True)

    return df


# ============================================
# 3 INDEPENDENT CONDITIONS — backtesting ke liye alag-alag
# ============================================

def classify_trigger_type(rvol_20, rvol_96):
    """
    Teen mutually-exclusive categories mein classify karta hai.

    SHORT_ONLY = sirf short-term threshold pass hua
    LONG_ONLY  = sirf long-term threshold pass hua
    BOTH       = dono pass hue (sabse strict/reliable)
    """
    cond_short = rvol_20 >= RVOL_SHORT_THRESHOLD
    cond_long = rvol_96 >= RVOL_LONG_THRESHOLD

    if cond_short and cond_long:
        return "BOTH"
    elif cond_short:
        return "SHORT_ONLY"
    elif cond_long:
        return "LONG_ONLY"
    else:
        return None


# ============================================
# GOOGLE SHEETS — append helper (naya tab, existing tabs ko touch nahi karta)
# ============================================

_sheets_client = None
_worksheet = None


def _get_worksheet():
    global _sheets_client, _worksheet

    if _worksheet is not None:
        return _worksheet

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(config.CREDENTIALS_FILE), scopes=scopes)
    _sheets_client = gspread.authorize(creds)

    spreadsheet = _sheets_client.open_by_key(config.SHEET_ID)

    try:
        _worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        _worksheet = spreadsheet.add_worksheet(title=WORKSHEET_NAME, rows=2000, cols=10)
        _worksheet.append_row(SHEET_HEADER)

    return _worksheet


def log_to_sheet(row_values):
    if DRY_RUN:
        print(f"  [DRY_RUN] Sheet mein ye row jaati: {row_values}")
        return
    try:
        ws = _get_worksheet()
        ws.append_row(row_values)
    except Exception as e:
        print(f"  Google Sheet mein likhne mein error: {e}")


# ============================================
# ALERT MESSAGE
# ============================================

def build_alert_message(pair, trigger_type, candle_time_ist, close, volume,
                          rvol_20, rvol_96, price_position):
    trigger_note = {
        "BOTH": "Dono short aur medium-term baseline confirm kar rahe hain — sabse strong signal.",
        "SHORT_ONLY": "Sirf short-term (5 ghante) baseline confirm kar raha hai — medium-term abhi weak. Zyada risky, careful check karo.",
        "LONG_ONLY": "Sirf medium-term (1 din) baseline confirm kar raha hai — short-term abhi threshold cross nahi kiya. Dheere-dheere build ho raha volume ho sakta hai.",
    }.get(trigger_type, "")

    position_note = {
        "BREAKOUT_ABOVE_RESISTANCE": "🟢 Resistance todke upar nikla — genuine breakout ho sakta hai.",
        "BREAKDOWN_BELOW_SUPPORT": "🔴 Support todke neeche gaya — genuine breakdown ho sakta hai.",
        "NEAR_RESISTANCE": "⚠️ Resistance ke paas hai — reversal ka risk zyada.",
        "NEAR_SUPPORT": "⚠️ Support ke paas hai — bounce ya breakdown, dono possible.",
        "MID_RANGE": "Kisi bhi major level ke paas nahi — range ke beech mein.",
    }.get(price_position, "")

    return (
        f"🚨 <b>INTRADAY VOLUME SPIKE — {trigger_type}</b>\n\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Candle Time (IST):</b> {candle_time_ist}\n"
        f"<b>Close:</b> {close}\n"
        f"<b>Volume:</b> {volume:,.0f}\n"
        f"<b>RVOL_20:</b> {rvol_20:.2f}x\n"
        f"<b>RVOL_96:</b> {rvol_96:.2f}x\n"
        f"<b>Price Position:</b> {price_position}\n\n"
        f"{trigger_note}\n"
        f"{position_note}\n"
        f"Khud verify karke decide karo."
    )


# ============================================
# EK SCAN CYCLE
# ============================================

def run_one_scan():
    print(f"\n{'=' * 60}")
    print(f"SCAN STARTED: {datetime.now()}")
    print('=' * 60)

    # ---- BACKTEST: sabse pehle purani pending spikes check karo ----
    try:
        backtest_tracker.resolve_pending(dry_run=DRY_RUN)
    except Exception as e:
        print(f"  [backtest_tracker] resolve_pending mein error: {e}")

    if TEST_ONLY_PAIRS:
        pairs = TEST_ONLY_PAIRS
    else:
        try:
            pairs = get_active_pairs()
        except Exception as e:
            print(f"Active pairs laane mein error: {e}")
            return

        if MAX_PAIRS_TO_SCAN:
            pairs = pairs[:MAX_PAIRS_TO_SCAN]

    print(f"Scanning {len(pairs)} pairs...")

    counts = {"SHORT_ONLY": 0, "LONG_ONLY": 0, "BOTH": 0}
    telegram_sent_count = 0

    for pair in pairs:
        try:
            df = get_candles(pair=pair, resolution=RESOLUTION, days=2)

            if df.empty or len(df) < LOOKBACK_LONG + 1:
                continue

            result = get_intraday_rvol(df, lookback_periods=[LOOKBACK_SHORT, LOOKBACK_LONG])
            last_row = result.iloc[-1]

            rvol_20 = last_row[f"RVOL_{LOOKBACK_SHORT}"]
            rvol_96 = last_row[f"RVOL_{LOOKBACK_LONG}"]

            if pd.isna(rvol_20) or pd.isna(rvol_96):
                continue

            trigger_type = classify_trigger_type(rvol_20, rvol_96)

            if trigger_type is not None:
                counts[trigger_type] += 1

                candle_time = last_row["Time"]
                close = last_row["Close"]
                volume = last_row["Volume"]
                prev_close = result.iloc[-2]["Close"]

                # ---- SUPPORT/RESISTANCE: price ki position nikaalo ----
                try:
                    resistance_levels, support_levels = get_support_resistance(
                        df, lookback=SR_LOOKBACK,
                        cluster_tolerance_pct=SR_CLUSTER_TOLERANCE_PCT
                    )
                    price_position = classify_price_position(
                        close, prev_close, resistance_levels, support_levels,
                        proximity_pct=SR_PROXIMITY_PCT
                    )
                except Exception as e:
                    print(f"  [S/R] {pair} pe error: {e}")
                    price_position = "UNKNOWN"

                print(f"  🚨 [{trigger_type}] {pair} | RVOL_20={rvol_20:.2f} RVOL_96={rvol_96:.2f} "
                      f"| Close={close} | Position={price_position} | Time={candle_time}")

                candle_time_ist = to_ist(candle_time)
                message = build_alert_message(
                    pair, trigger_type, candle_time_ist, close, volume,
                    rvol_20, rvol_96, price_position
                )

                # ---- TELEGRAM: sirf tabhi bhejo jab RVOL_20 >= 6.0 ----
                if rvol_20 >= RVOL_20_ALERT_THRESHOLD:
                    telegram_sent_count += 1
                    if DRY_RUN:
                        print(f"  [DRY_RUN] Telegram message bhejta:\n{message}\n")
                    else:
                        send_telegram_message(message)
                else:
                    print(f"  (RVOL_20={rvol_20:.2f} < {RVOL_20_ALERT_THRESHOLD}, "
                          f"Telegram skip — sirf Sheet mein log hua)")

                # ---- SHEET LOGGING ----
                detected_at_ist = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                log_to_sheet([
                    detected_at_ist,
                    str(candle_time),
                    pair,
                    trigger_type,
                    float(close),
                    float(volume),
                    round(float(rvol_20), 2),
                    round(float(rvol_96), 2),
                    price_position,
                ])

                # ---- BACKTEST: is spike ko track karna shuru karo ----
                try:
                    candle_open = last_row["Open"]
                    candle_color = "GREEN" if close >= candle_open else "RED"
                    backtest_tracker.add_pending(
                        pair=pair,
                        trigger_type=trigger_type,
                        candle_color=candle_color,
                        spike_time=candle_time,
                        spike_close=close,
                        price_position=price_position,
                        rvol_20=rvol_20,
                    )
                except Exception as e:
                    print(f"  [backtest_tracker] add_pending mein error: {e}")

            time.sleep(SLEEP_BETWEEN_PAIRS)

        except Exception as e:
            print(f"  {pair} pe error aaya, skip kar rahe hain: {e}")
            continue

    total_alerts = sum(counts.values())
    print(f"\nScan complete. {total_alerts} spike(s) mile — "
          f"BOTH: {counts['BOTH']}, SHORT_ONLY: {counts['SHORT_ONLY']}, LONG_ONLY: {counts['LONG_ONLY']}")
    print(f"Telegram bheja gaya: {telegram_sent_count} (RVOL_20 >= {RVOL_20_ALERT_THRESHOLD} wale)")


# ============================================
# ENTRY POINT
# ============================================
if __name__ == "__main__":
    print("Intraday Spike Monitor — single scan (GitHub Actions mode)")
    print(f"DRY_RUN = {DRY_RUN}")
    print(f"RVOL_SHORT_THRESHOLD={RVOL_SHORT_THRESHOLD} | RVOL_LONG_THRESHOLD={RVOL_LONG_THRESHOLD}")
    print(f"Telegram sirf RVOL_20 >= {RVOL_20_ALERT_THRESHOLD} pe jayega")
    print(f"Support/Resistance tracking: ON\n")
    run_one_scan()

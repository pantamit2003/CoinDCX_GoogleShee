"""
intraday_spike_monitor.py
==========================
NAYA STANDALONE SCRIPT — existing check_watchlist.py / daily_breakout_scan.py /
volume_watch.py ko bilkul touch nahi karta.

KYA KARTA HAI:
- Har 15 minute pe (cron-job.org ke external trigger ke through) chalta hai
- Saare active futures pairs ke 15-min candles CoinDCX se laata hai
- Sirf abhi-abhi CLOSE hui candle ka RVOL check karta hai (RVOL_20 aur RVOL_96)
- Support/Resistance ke against price ki position classify karta hai
  (support_resistance.py module se) — AB touch-count bhi milta hai,
  taaki pata chale wo S/R level kitni baar proven/tested hai
- Trendline (diagonal support/resistance) ke against bhi context nikalta
  hai (trendline.py module se) — trend direction, touch points, break/retest
- Khud spike-candle ka shape bhi classify karta hai
  (candle_shape.py module se) — DOMINANCE / REJECTION / CONFUSION

BACKTESTING KE LIYE — 3 INDEPENDENT CONDITIONS:
    1. SHORT_ONLY — sirf RVOL_20 >= threshold pass hua, RVOL_96 nahi
    2. LONG_ONLY  — sirf RVOL_96 >= threshold pass hua, RVOL_20 nahi
    3. BOTH       — dono RVOL_20 aur RVOL_96 pass hue (sabse strict)

Har category ka Sheet mein record hota hai (Trigger_Type column ke saath) —
lekin TELEGRAM sirf tabhi jaata hai jab RVOL_20 >= RVOL_20_ALERT_THRESHOLD
ho.

NAYA — DUAL TELEGRAM BOT:
    Purana bot (send_telegram_message) — SAARE qualifying signals yahan
    jaate hain, jaisa pehle hota tha.
    Naya bot (send_strong_telegram_message) — SIRF DOMINANCE shape wale
    AUR kisi na kisi S/R level ke relevant (BREAKOUT_ABOVE_RESISTANCE,
    BREAKDOWN_BELOW_SUPPORT, NEAR_RESISTANCE, NEAR_SUPPORT) signals yahan
    bhi jaate hain — sirf MID_RANGE wale skip hote hain.

NAYA — COOLDOWN LOGIC (v2):
    Agar koi pair already Pending_Spikes_V2 mein PENDING hai (matlab
    pichla signal abhi resolve nahi hua, ~60-75 min effective cooldown),
    to us pair ke liye naya signal:
    - Telegram pe nahi jayega
    - Sheet mein log nahi hoga
    - backtest mein nahi jayega
    Ye same-event-multiple-count distortion solve karta hai.

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
from notifications.telegram_bot import send_telegram_message, send_strong_telegram_message
from support_resistance import get_support_resistance, classify_price_position
from trendline import get_trendline_context
from candle_shape import classify_candle_shape
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

SR_LOOKBACK = 50
SR_CLUSTER_TOLERANCE_PCT = 0.5
SR_PROXIMITY_PCT = 0.5

TRENDLINE_LOOKBACK = 60

MAX_PAIRS_TO_SCAN = 250
SLEEP_BETWEEN_PAIRS = 0.3

WORKSHEET_NAME = "Intraday_Spike_Alerts_V2"

TEST_ONLY_PAIRS = []

STRONG_BOT_ALLOWED_POSITIONS = (
    "BREAKOUT_ABOVE_RESISTANCE",
    "BREAKDOWN_BELOW_SUPPORT",
    "NEAR_RESISTANCE",
    "NEAR_SUPPORT",
)

SHEET_HEADER = [
    "Detected_At_IST", "Candle_Time_UTC", "Pair", "Trigger_Type",
    "Close", "Volume", "RVOL_20", "RVOL_96",
    "Price_Position", "SR_Level_Price", "SR_Touch_Count",
    "Trend_Type", "Trend_Detail", "Candle_Shape",
]


# ============================================
# COOLDOWN — pending pairs cache
# ============================================
# Har scan run ke start mein Pending_Spikes_V2 se ek baar fetch karke
# ye set banate hain — taaki har pair ke liye alag-alag Sheet API call
# na ho (efficient hai).
_pending_pairs_cache = None


def _load_pending_pairs_cache():
    """
    Pending_Spikes_V2 tab se saare PENDING pairs ek baar fetch karta hai.
    Result ek set mein store hota hai — O(1) lookup ke liye.
    Ye function sirf ek baar (run ke start mein) call hoga.
    """
    global _pending_pairs_cache
    if _pending_pairs_cache is not None:
        return _pending_pairs_cache

    try:
        ws = backtest_tracker._get_pending_worksheet()
        records = ws.get_all_records()
        _pending_pairs_cache = {
            r["Pair"]
            for r in records
            if str(r.get("Confirmation_Status", "")).upper() == "PENDING"
        }
        print(f"  [cooldown] Pending pairs loaded: {len(_pending_pairs_cache)} pairs currently in cooldown.")
    except Exception as e:
        print(f"  [cooldown] Pending pairs fetch error (cooldown disabled this run): {e}")
        _pending_pairs_cache = set()  # empty set — koi cooldown nahi, safe fallback

    return _pending_pairs_cache


def _is_in_cooldown(pair):
    """
    Check karta hai ki ye pair abhi cooldown mein hai ya nahi.
    Cooldown tab khatam hota hai jab pair ka signal resolve ho jata hai
    (CONFIRMED_CONTINUATION / FAILED_BREAKOUT / STILL_UNDECIDED).
    Effective cooldown ~60-75 min hota hai (N+1 + N+2 + resolve cycle).
    """
    return pair in _load_pending_pairs_cache()


# ============================================
# TIME HELPERS
# ============================================
def to_ist(utc_dt):
    dt = pd.to_datetime(utc_dt)
    if dt.tzinfo is None:
        dt = dt.tz_localize("UTC")
    ist_dt = dt + pd.Timedelta(hours=5, minutes=30)
    return ist_dt.strftime("%Y-%m-%d %H:%M:%S")


# ============================================
# RVOL CALCULATION
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


# ============================================
# 3 INDEPENDENT CONDITIONS
# ============================================
def classify_trigger_type(rvol_20, rvol_96):
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
# GOOGLE SHEETS
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
        _worksheet = spreadsheet.add_worksheet(
            title=WORKSHEET_NAME, rows=2000, cols=len(SHEET_HEADER) + 2
        )
        _worksheet.append_row(SHEET_HEADER, table_range="A1")
    return _worksheet


def log_to_sheet(row_values):
    if DRY_RUN:
        print(f"  [DRY_RUN] Sheet mein ye row jaati: {row_values}")
        return
    try:
        ws = _get_worksheet()
        ws.append_row(row_values, table_range="A1")
    except Exception as e:
        print(f"  Google Sheet mein likhne mein error: {e}")


# ============================================
# ALERT MESSAGE
# ============================================
def build_alert_message(pair, trigger_type, candle_time_ist, close, volume,
                         rvol_20, rvol_96, price_position_label, sr_level_price,
                         sr_touch_count, trend_type, trend_detail, candle_shape_label):
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
    }.get(price_position_label, "")

    sr_line = f"<b>Price Position:</b> {price_position_label}"
    if sr_level_price is not None:
        sr_line += f" (Level: {sr_level_price}, tested {sr_touch_count}x pehle)"

    trend_line = f"<b>Trend:</b> {trend_type}"
    if trend_detail:
        trend_line += f" ({trend_detail})"

    return (
        f"🚨 <b>INTRADAY VOLUME SPIKE — {trigger_type}</b>\n\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Candle Time (IST):</b> {candle_time_ist}\n"
        f"<b>Close:</b> {close}\n"
        f"<b>Volume:</b> {volume:,.0f}\n"
        f"<b>RVOL_20:</b> {rvol_20:.2f}x\n"
        f"<b>RVOL_96:</b> {rvol_96:.2f}x\n"
        f"{sr_line}\n"
        f"{trend_line}\n"
        f"<b>Candle Shape:</b> {candle_shape_label}\n\n"
        f"{trigger_note}\n"
        f"{position_note}\n"
        f"Khud verify karke decide karo."
    )


# ============================================
# EK SCAN CYCLE
# ============================================
def run_one_scan():
    global _pending_pairs_cache

    print(f"\n{'=' * 60}")
    print(f"SCAN STARTED: {datetime.now()}")
    print('=' * 60)

    # ---- Cache reset karo har naye run mein ----
    # (taaki fresh pending list mile, stale cache se kaam na ho)
    _pending_pairs_cache = None

    # ---- BACKTEST: sabse pehle purani pending spikes check karo ----
    try:
        backtest_tracker.resolve_pending(dry_run=DRY_RUN)
    except Exception as e:
        print(f"  [backtest_tracker] resolve_pending mein error: {e}")

    # ---- Cooldown cache load karo (resolve ke baad, taaki fresh data mile) ----
    _load_pending_pairs_cache()

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
    strong_telegram_sent_count = 0
    cooldown_skipped_count = 0

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

                # ---- COOLDOWN CHECK — sabse pehle ----
                # Agar pair already pending mein hai, poora block skip karo.
                # Telegram nahi, Sheet nahi, backtest nahi.
                if _is_in_cooldown(pair):
                    cooldown_skipped_count += 1
                    print(f"  [cooldown] {pair} skip — already pending/cooldown mein hai.")
                    continue

                candle_time = last_row["Time"]
                candle_open = last_row["Open"]
                candle_high = last_row["High"]
                candle_low = last_row["Low"]
                close = last_row["Close"]
                volume = last_row["Volume"]
                prev_close = result.iloc[-2]["Close"]

                candle_color = "GREEN" if close >= candle_open else "RED"

                # ---- SUPPORT/RESISTANCE ----
                try:
                    resistance_levels, support_levels = get_support_resistance(
                        df, lookback=SR_LOOKBACK,
                        cluster_tolerance_pct=SR_CLUSTER_TOLERANCE_PCT
                    )
                    sr_result = classify_price_position(
                        close, prev_close, resistance_levels, support_levels,
                        proximity_pct=SR_PROXIMITY_PCT
                    )
                    price_position = sr_result["label"]
                    sr_level_price = sr_result["level_price"]
                    sr_touch_count = sr_result["touch_count"]
                except Exception as e:
                    print(f"  [S/R] {pair} pe error: {e}")
                    price_position = "UNKNOWN"
                    sr_level_price = None
                    sr_touch_count = 0

                # ---- TRENDLINE ----
                try:
                    trend_ctx = get_trendline_context(df, lookback=TRENDLINE_LOOKBACK)
                    trend_type = trend_ctx["trend"]
                    trend_detail = (
                        f"{trend_ctx['touch_points']} touches | "
                        f"{trend_ctx['position']} ({trend_ctx['deviation_pct']:+.2f}%) | "
                        f"{'JUST_BROKE_' + trend_ctx['break_direction'] if trend_ctx['just_broke'] else 'no-break'}"
                    )
                except Exception as e:
                    print(f"  [Trendline] {pair} pe error: {e}")
                    trend_type = "UNKNOWN"
                    trend_detail = ""

                # ---- CANDLE SHAPE ----
                try:
                    shape_ctx = classify_candle_shape(candle_open, candle_high, candle_low, close)
                    candle_shape_label = f"{shape_ctx['shape']} ({shape_ctx['strength']}, body={shape_ctx['body_pct']}%)"
                except Exception as e:
                    print(f"  [CandleShape] {pair} pe error: {e}")
                    shape_ctx = {"shape": "UNKNOWN"}
                    candle_shape_label = "UNKNOWN"

                print(f"  🚨 [{trigger_type}] {pair} | RVOL_20={rvol_20:.2f} RVOL_96={rvol_96:.2f} "
                      f"| Close={close} | Position={price_position} (touched {sr_touch_count}x) | "
                      f"Trend={trend_type} | Shape={candle_shape_label} | Time={candle_time}")

                candle_time_ist = to_ist(candle_time)
                message = build_alert_message(
                    pair, trigger_type, candle_time_ist, close, volume,
                    rvol_20, rvol_96, price_position, sr_level_price, sr_touch_count,
                    trend_type, trend_detail, candle_shape_label
                )

                # ---- TELEGRAM ----
                if rvol_20 >= RVOL_20_ALERT_THRESHOLD:
                    telegram_sent_count += 1
                    if DRY_RUN:
                        print(f"  [DRY_RUN] Telegram message bhejta:\n{message}\n")
                    else:
                        send_telegram_message(message)

                        is_strong_shape = shape_ctx["shape"] == "DOMINANCE"
                        is_level_relevant = price_position in STRONG_BOT_ALLOWED_POSITIONS
                        if is_strong_shape and is_level_relevant:
                            strong_telegram_sent_count += 1
                            send_strong_telegram_message(message)
                else:
                    print(f"  (RVOL_20={rvol_20:.2f} < {RVOL_20_ALERT_THRESHOLD}, "
                          f"Telegram skip — sirf Sheet mein log hua)")

                # ---- SHEET LOGGING ----
                detected_at_ist = (
                    datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
                ).strftime("%Y-%m-%d %H:%M:%S")

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
                    sr_level_price if sr_level_price is not None else "",
                    sr_touch_count,
                    trend_type,
                    trend_detail,
                    candle_shape_label,
                ])

                # ---- BACKTEST ----
                try:
                    backtest_tracker.add_pending(
                        pair=pair,
                        trigger_type=trigger_type,
                        candle_color=candle_color,
                        spike_time=candle_time,
                        spike_close=close,
                        price_position=price_position,
                        rvol_20=rvol_20,
                        trend_type=trend_type,
                        trend_detail=trend_detail,
                        candle_shape=candle_shape_label,
                        sr_level_price=sr_level_price,
                        sr_touch_count=sr_touch_count,
                    )
                except Exception as e:
                    print(f"  [backtest_tracker] add_pending mein error: {e}")

            time.sleep(SLEEP_BETWEEN_PAIRS)

        except Exception as e:
            print(f"  {pair} pe error aaya, skip kar rahe hain: {e}")
            continue

    total_alerts = sum(counts.values())
    print(f"\nScan complete. {total_alerts} spike(s) mile — "
          f"BOTH: {counts['BOTH']}, SHORT_ONLY: {counts['SHORT_ONLY']}, "
          f"LONG_ONLY: {counts['LONG_ONLY']}")
    print(f"Cooldown ke wajah se skip hue: {cooldown_skipped_count}")
    print(f"Telegram (main) bheja gaya: {telegram_sent_count} "
          f"(RVOL_20 >= {RVOL_20_ALERT_THRESHOLD} wale)")
    print(f"Telegram (strong/DOMINANCE + level-relevant) bheja gaya: {strong_telegram_sent_count}")


# ============================================
# ENTRY POINT
# ============================================
if __name__ == "__main__":
    print("Intraday Spike Monitor — single scan")
    print(f"DRY_RUN = {DRY_RUN}")
    print(f"RVOL_SHORT_THRESHOLD={RVOL_SHORT_THRESHOLD} | RVOL_LONG_THRESHOLD={RVOL_LONG_THRESHOLD}")
    print(f"Telegram sirf RVOL_20 >= {RVOL_20_ALERT_THRESHOLD} pe jayega")
    print(f"Support/Resistance: ON | Trendline: ON | Candle Shape: ON")
    print(f"Cooldown: ON (Pending_Spikes_V2 se) | Dual Telegram: ON\n")
    run_one_scan()

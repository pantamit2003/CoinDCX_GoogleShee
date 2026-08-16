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

NAYA — S/R MINIMUM TOUCH FILTER:
    Sirf wahi S/R levels valid maane jayenge jinka touch_count >= MIN_SR_TOUCHES
    (default: 5) ho. Weak levels (1-4 touches) ko completely ignore kiya
    jayega — classification, consolidation, liquidity zone, aur Telegram
    alerts sabke liye.

NAYA — PATTERN-BACKTEST DATA COLLECTION (standalone, alag module):
    Breakout/breakdown spikes ke liye ek ALAG data-collection pipeline
    bhi chalta hai (pattern_backtest.py) — Breakout → Dominance →
    Confirmation → Confusion candle(s) → Zone-break → Outcome. Yeh
    koi Telegram nahi bhejta, koi trading-signal classify nahi karta —
    sirf research ke liye raw data collect karta hai, poori tarah
    backtest_tracker.py se independent (alag Sheet tabs: Pattern_Pending,
    Pattern_Backtest_Data).

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
from support_resistance import (
    get_support_resistance, classify_price_position,
    count_pre_breakout_consolidation, find_next_liquidity_zone,
)
from trendline import get_trendline_context
from candle_shape import classify_candle_shape
import backtest_tracker
import pattern_backtest
import config


# ============================================
# CONFIG — pehle inhe apni marzi se set karo
# ============================================
DRY_RUN = False         # True = sirf console print, Telegram/Sheet pe kuch nahi jayega
RESOLUTION = "15"       # 15-min candles
LOOKBACK_SHORT = 20     # ~5 hours ka baseline
LOOKBACK_LONG = 96      # ~1 din ka baseline

RVOL_SHORT_THRESHOLD = 5.0
RVOL_LONG_THRESHOLD = 6.0
RVOL_20_ALERT_THRESHOLD = 6.0

SR_LOOKBACK = 50
SR_CLUSTER_TOLERANCE_PCT = 0.5
SR_PROXIMITY_PCT = 0.5

# ---- NAYA: S/R minimum touch filter ----
# Sirf wahi S/R levels valid maane jayenge jinka touch_count >= MIN_SR_TOUCHES ho.
# 1-4 touches wale levels weak/coincidental ho sakte hain — ignore karo.
# 5+ touches = level ne multiple baar market ka attention paya hai = reliable.
MIN_SR_TOUCHES = 5

CONSOLIDATION_BAND_PCT = 1.5
CONSOLIDATION_MAX_LOOKBACK = 20

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
    "Pre_Breakout_Consolidation", "Next_Liquidity_Zone", "Next_Liquidity_Distance_Pct",
]


# ============================================
# COOLDOWN — pending pairs cache
# ============================================
_pending_pairs_cache = None


def _load_pending_pairs_cache():
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
        _pending_pairs_cache = set()
    return _pending_pairs_cache


def _is_in_cooldown(pair):
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
                         sr_touch_count, trend_type, trend_detail, candle_shape_label,
                         pre_breakout_consolidation=None, liquidity_zone=None):
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

    consolidation_line = ""
    if pre_breakout_consolidation is not None:
        n = pre_breakout_consolidation
        if n >= 6:
            note = "STRONG — achhi consolidation hui breakout se pehle, genuine hone ka chance zyada"
        elif n >= 2:
            note = "MODERATE — thodi consolidation hui, kuch confidence deta hai"
        else:
            note = "WEAK — seedha breakout hua bina consolidation ke, fake hone ka risk zyada"
        consolidation_line = f"\n<b>Pre-Breakout Consolidation:</b> {n} candles ({n * 15} min) — {note}"

    liquidity_line = ""
    if liquidity_zone is not None and liquidity_zone.get("zone_price") is not None:
        liquidity_line = (
            f"\n<b>Next Liquidity Zone:</b> {liquidity_zone['zone_price']} "
            f"({liquidity_zone['distance_pct']}% door, tested {liquidity_zone['touch_count']}x pehle) "
            f"— price ab bhi wahan tak khinch sakta hai"
        )

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
        f"<b>Candle Shape:</b> {candle_shape_label}"
        f"{consolidation_line}"
        f"{liquidity_line}\n\n"
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

    _pending_pairs_cache = None

    try:
        backtest_tracker.resolve_pending(dry_run=DRY_RUN)
    except Exception as e:
        print(f"  [backtest_tracker] resolve_pending mein error: {e}")

    try:
        pattern_backtest.resolve_pending_patterns(dry_run=DRY_RUN)
    except Exception as e:
        print(f"  [pattern_backtest] resolve_pending_patterns mein error: {e}")

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
                    resistance_levels_raw, support_levels_raw = get_support_resistance(
                        df, lookback=SR_LOOKBACK,
                        cluster_tolerance_pct=SR_CLUSTER_TOLERANCE_PCT
                    )

                    # ---- NAYA: MIN_SR_TOUCHES filter ----
                    # Sirf 5+ touch wale levels valid hain — weak levels ignore
                    resistance_levels = [r for r in resistance_levels_raw if r[1] >= MIN_SR_TOUCHES]
                    support_levels = [s for s in support_levels_raw if s[1] >= MIN_SR_TOUCHES]

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
                    resistance_levels = []
                    support_levels = []

                # ---- PRE-BREAKOUT CONSOLIDATION + NEXT LIQUIDITY ZONE ----
                # Sirf valid 5+ touch S/R level ke against calculate karo
                pre_breakout_consolidation = None
                liquidity_zone = None
                if price_position in ("BREAKOUT_ABOVE_RESISTANCE", "BREAKDOWN_BELOW_SUPPORT"):
                    try:
                        pre_breakout_consolidation = count_pre_breakout_consolidation(
                            df, sr_level_price,
                            band_pct=CONSOLIDATION_BAND_PCT,
                            max_lookback=CONSOLIDATION_MAX_LOOKBACK,
                        )
                    except Exception as e:
                        print(f"  [Consolidation] {pair} pe error: {e}")
                    try:
                        direction = "UP" if price_position == "BREAKOUT_ABOVE_RESISTANCE" else "DOWN"
                        liquidity_zone = find_next_liquidity_zone(
                            close, resistance_levels, support_levels,
                            broken_level_price=sr_level_price, direction=direction,
                        )
                    except Exception as e:
                        print(f"  [LiquidityZone] {pair} pe error: {e}")

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
                    trend_type, trend_detail, candle_shape_label,
                    pre_breakout_consolidation=pre_breakout_consolidation,
                    liquidity_zone=liquidity_zone,
                )

                # ---- TELEGRAM ----
                # Main bot: RVOL_20 >= threshold
                # NAYA: S/R-based alerts sirf tabhi jayenge jab sr_touch_count >= MIN_SR_TOUCHES
                # Agar price_position MID_RANGE hai (matlab koi valid 5+ touch level nahi mila),
                # to S/R condition automatically fail hogi (sr_touch_count = 0 hoga)
                sr_is_valid = sr_touch_count >= MIN_SR_TOUCHES

                if rvol_20 >= RVOL_20_ALERT_THRESHOLD:
                    # Sirf tabhi Telegram bhejo jab:
                    # 1. MID_RANGE hai (S/R relevant nahi, volume spike pe alert dena theek hai)
                    # 2. Ya S/R-based position hai AUR touch_count >= MIN_SR_TOUCHES
                    should_send_main = (
                        price_position == "MID_RANGE"
                        or sr_is_valid
                    )

                    if should_send_main:
                        telegram_sent_count += 1
                        if DRY_RUN:
                            print(f"  [DRY_RUN] Telegram message bhejta:\n{message}\n")
                        else:
                            send_telegram_message(message)

                            # Strong bot: DOMINANCE + valid S/R level (5+ touches) + allowed position
                            is_strong_shape = shape_ctx["shape"] == "DOMINANCE"
                            is_level_relevant = price_position in STRONG_BOT_ALLOWED_POSITIONS
                            if is_strong_shape and is_level_relevant and sr_is_valid:
                                strong_telegram_sent_count += 1
                                send_strong_telegram_message(message)
                    else:
                        print(f"  ({pair} S/R level weak — touch_count={sr_touch_count} "
                              f"< {MIN_SR_TOUCHES} — Telegram S/R alert skip)")
                else:
                    print(f"  (RVOL_20={rvol_20:.2f} < {RVOL_20_ALERT_THRESHOLD}, "
                          f"Telegram skip — sirf Sheet mein log hua)")

                # ---- SHEET LOGGING ----
                # Sheet mein SAARI spikes log hoti hain (weak S/R wali bhi)
                # taaki backtest mein compare kar sakein — filter sirf Telegram pe hai
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
                    pre_breakout_consolidation if pre_breakout_consolidation is not None else "",
                    liquidity_zone["zone_price"] if liquidity_zone and liquidity_zone.get("zone_price") is not None else "",
                    liquidity_zone["distance_pct"] if liquidity_zone and liquidity_zone.get("distance_pct") is not None else "",
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

                # ---- PATTERN-BACKTEST ----
                if price_position in ("BREAKOUT_ABOVE_RESISTANCE", "BREAKDOWN_BELOW_SUPPORT"):
                    try:
                        pattern_backtest.add_pending_pattern(
                            pair=pair,
                            direction="UP" if price_position == "BREAKOUT_ABOVE_RESISTANCE" else "DOWN",
                            breakout_time=candle_time,
                            breakout_level=sr_level_price,
                            breakout_open=candle_open,
                            breakout_high=candle_high,
                            breakout_low=candle_low,
                            breakout_close=close,
                            breakout_volume=volume,
                            rvol_20=rvol_20,
                        )
                    except Exception as e:
                        print(f"  [pattern_backtest] add_pending_pattern mein error: {e}")

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
    print(f"S/R minimum touches: {MIN_SR_TOUCHES} (weak levels ignore honge)")
    print(f"Support/Resistance: ON | Trendline: ON | Candle Shape: ON")
    print(f"Cooldown: ON (Pending_Spikes_V2 se) | Dual Telegram: ON | Pattern-Backtest: ON\n")
    run_one_scan()

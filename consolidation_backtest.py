"""
consolidation_backtest.py
==========================
NAYA STANDALONE RESEARCH/BACKTEST SCRIPT — existing intraday_spike_monitor.py,
backtest_tracker.py, pattern_backtest.py, RVOL logic, cooldown, Telegram,
trendline, candle_shape — kuch bhi modify nahi karta. Purani trading/signal
behaviour bilkul waisi hi rahegi.

KYA KARTA HAI:
- Historical 15-min candles pe walk-forward tareeke se breakout/breakdown
  events dhoondta hai (sirf S/R levels jinka touch_count >= MIN_SR_TOUCHES,
  weak 1-4 touch levels completely ignore).
- Har valid breakout ke liye "Pre-Breakout Consolidation" candle count
  (existing support_resistance.count_pre_breakout_consolidation() logic)
  record karta hai.
- Breakout ke baad price movement track karta hai: 15/30/45/60/90/120 min
  pe % move, max favorable move, max adverse move, level ke andar wapas
  aaya ya nahi, aur overall success/failure.
- NAYA — Target-hit analysis: har breakout/breakdown ke liye 120-min
  tracking window ke andar 0.5% / 1% / 2% / 3% / 5% target (breakout
  close se, direction-aware) hit hua ya nahi — YES/NO har target ke liye.
- Consolidation candle count ko buckets mein group karke (0-1, 2-5, 6-10,
  11-20) success-rate, average moves, AUR bucket-wise target-hit-rates
  compare karta hai.

MAIN OBJECTIVE:
    Ye PROVE/DISPROVE karna hai ki pre-breakout consolidation ki length ka
    breakout ke baad ke price-movement/success-rate (ab target-hit-rate
    samet) par statistically meaningful effect hai ya nahi. Code mein
    "6+ candles = genuine breakout" KA ASSUMPTION HARDCODE NAHI KIYA
    GAYA — hum sirf raw data collect + bucket-compare karte hain,
    judgment historical data se aayega.

OUTPUT:
    Google Sheet mein do naye tabs (dono standalone, koi existing tab touch
    nahi hota):
        - Consolidation_Backtest          (raw per-signal rows)
        - Consolidation_Backtest_Summary  (bucket-wise aggregated stats)

CHALANE KA TARIKA:
    python consolidation_backtest.py
"""
import time
from datetime import datetime, timedelta, timezone
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from data.candles import get_candles
from exchange.coindcx import get_active_pairs
from support_resistance import (
    get_support_resistance, classify_price_position,
    count_pre_breakout_consolidation,
)
import config

# ============================================
# CONFIG
# ============================================
DRY_RUN = False                 # True = sirf console print, Sheet mein kuch nahi jayega
RESOLUTION = "15"
RESOLUTION_MINUTES = 15

DAYS_OF_HISTORY = 30            # kitne din peeche tak backtest karna hai
MAX_PAIRS_TO_SCAN = 250
TEST_ONLY_PAIRS = []
SLEEP_BETWEEN_PAIRS = 0.3

# ---- RVOL (candle ke "spike-ness" ka context capture karne ke liye) ----
LOOKBACK_SHORT = 20
LOOKBACK_LONG = 96

# ---- S/R settings (existing live-monitor jaisa hi, consistency ke liye) ----
SR_LOOKBACK = 50
SR_CLUSTER_TOLERANCE_PCT = 0.5
SR_PROXIMITY_PCT = 0.5
MIN_SR_TOUCHES = 5
# Performance: S/R levels har candle pe recompute karna mehenga hai.
# Har N candles mein ek baar recompute karo, beech mein last-known levels reuse karo.
SR_RECALC_EVERY_N_CANDLES = 4   # ~1 ghanta

# ---- Consolidation settings (existing live-monitor jaisa hi) ----
CONSOLIDATION_BAND_PCT = 1.5
CONSOLIDATION_MAX_LOOKBACK = 20

# ---- Forward tracking horizons (minutes) ----
HORIZONS_MINUTES = [15, 30, 45, 60, 90, 120]
MATCH_TOLERANCE_MINUTES = 7

# ---- NAYA: Target-hit analysis ----
# UP breakout: close se +X% kabhi bhi 120-min window ke andar touch hua?
# DOWN breakdown: close se -X% kabhi bhi 120-min window ke andar touch hua?
# High/Low based check (intrabar touch bhi count hoti hai, sirf candle-close nahi).
TARGET_PCTS = [0.5, 1, 2, 3, 5]
TARGET_WINDOW_MINUTES = 120   # spec ke mutabik target-hit sirf 120 min tak dekha jaata hai

# Ek hi level pe baar-baar "naya breakout" na gine — breakout detect hone
# ke baad itni candles tak us pair/level ke liye dobara scan skip karo
# (max tracking horizon jitna, taaki ek hi event double-count na ho).
POST_BREAKOUT_DEBOUNCE_CANDLES = max(HORIZONS_MINUTES) // RESOLUTION_MINUTES

CONSOLIDATION_BUCKETS = [
    ("0-1 candles", 0, 1),
    ("2-5 candles", 2, 5),
    ("6-10 candles", 6, 10),
    ("11-20 candles", 11, 20),
]

RAW_WORKSHEET_NAME = "Consolidation_Backtest"
SUMMARY_WORKSHEET_NAME = "Consolidation_Backtest_Summary"

RAW_HEADER = [
    "Pair", "Breakout_Time_UTC", "Direction", "SR_Level_Price", "SR_Touch_Count",
    "Breakout_Open", "Breakout_High", "Breakout_Low", "Breakout_Close",
    "Breakout_Volume", "RVOL_20", "RVOL_96",
    "Pre_Breakout_Consolidation_Candles", "Consolidation_Duration_Min",
    "Breakout_Price",
    "Move_15m", "Move_30m", "Move_45m", "Move_60m", "Move_90m", "Move_120m",
    "Max_Favorable_Move_Pct", "Max_Adverse_Move_Pct",
    "Returned_Inside_Level", "Breakout_Successful",
    "Target_0.5_Hit", "Target_1_Hit", "Target_2_Hit", "Target_3_Hit", "Target_5_Hit",
    "Consolidation_Bucket",
]

SUMMARY_HEADER = [
    "Consolidation_Bucket", "Total_Signals", "Successful_Breakouts", "Failed_Breakouts",
    "Success_Rate_Pct", "Avg_Move_15m_Pct", "Avg_Move_30m_Pct", "Avg_Move_60m_Pct",
    "Avg_Move_120m_Pct", "Avg_Max_Favorable_Move_Pct", "Avg_Max_Adverse_Move_Pct",
    "Target_0.5_Hit_Rate_Pct", "Target_1_Hit_Rate_Pct", "Target_2_Hit_Rate_Pct",
    "Target_3_Hit_Rate_Pct", "Target_5_Hit_Rate_Pct",
]

# ============================================
# GOOGLE SHEETS
# ============================================
_client = None
_raw_ws = None
_summary_ws = None


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


def _get_raw_worksheet():
    global _raw_ws
    if _raw_ws is not None:
        return _raw_ws
    spreadsheet = _connect().open_by_key(config.SHEET_ID)
    try:
        _raw_ws = spreadsheet.worksheet(RAW_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        _raw_ws = spreadsheet.add_worksheet(
            title=RAW_WORKSHEET_NAME, rows=5000, cols=len(RAW_HEADER) + 2
        )
        _raw_ws.append_row(RAW_HEADER, table_range="A1")
    return _raw_ws


def _get_summary_worksheet():
    global _summary_ws
    if _summary_ws is not None:
        return _summary_ws
    spreadsheet = _connect().open_by_key(config.SHEET_ID)
    try:
        _summary_ws = spreadsheet.worksheet(SUMMARY_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        _summary_ws = spreadsheet.add_worksheet(
            title=SUMMARY_WORKSHEET_NAME, rows=100, cols=len(SUMMARY_HEADER) + 2
        )
        _summary_ws.append_row(SUMMARY_HEADER, table_range="A1")
    return _summary_ws


def log_raw_row(row_values):
    if DRY_RUN:
        print(f"  [DRY_RUN] Raw row: {row_values}")
        return
    try:
        _get_raw_worksheet().append_row(row_values, table_range="A1")
    except Exception as e:
        print(f"  [Sheet] raw row likhne mein error: {e}")


def write_summary(summary_rows):
    if DRY_RUN:
        print("  [DRY_RUN] Summary rows:")
        for r in summary_rows:
            print(f"    {r}")
        return
    try:
        ws = _get_summary_worksheet()
        ws.clear()
        ws.update([SUMMARY_HEADER] + summary_rows)
    except Exception as e:
        print(f"  [Sheet] summary likhne mein error: {e}")


# ============================================
# RVOL
# ============================================
def compute_rvol(df, lookback_periods):
    df = df.copy()
    for period in lookback_periods:
        avg_col = f"avg_vol_{period}"
        df[avg_col] = df["Volume"].rolling(window=period).mean().shift(1)
        rvol_col = f"RVOL_{period}"
        df[rvol_col] = df["Volume"] / df[avg_col]
        df.drop(columns=[avg_col], inplace=True)
    return df


# ============================================
# TIME / CANDLE LOOKUP HELPERS
# ============================================
def _as_utc_series(time_col):
    if time_col.dt.tz is None:
        return time_col.dt.tz_localize("UTC")
    return time_col


def _find_candle_at(df, target_time, tolerance_minutes=MATCH_TOLERANCE_MINUTES):
    times = _as_utc_series(df["Time"])
    diffs = (times - target_time).abs()
    idx = diffs.idxmin()
    if diffs.loc[idx] <= timedelta(minutes=tolerance_minutes):
        return df.loc[idx]
    return None


def _consolidation_bucket(n_candles):
    for label, lo, hi in CONSOLIDATION_BUCKETS:
        if lo <= n_candles <= hi:
            return label
    return None  # 20 se zyada — kisi bucket mein nahi (rare/extreme case, raw row mein rahega bas)


# ============================================
# FORWARD MOVEMENT TRACKING
# ============================================
def _track_forward_movement(df, breakout_idx, breakout_time, breakout_price, direction):
    """
    Breakout candle ke baad ka price-action track karta hai.
    direction: "UP" ya "DOWN"
    UP breakout mein upward move = positive. DOWN breakdown mein downward
    move = positive (jaisa spec mein bataya gaya hai).
    Return: dict — horizon moves, max favorable/adverse, list of forward rows
            (window ke liye), ya None agar poora window abhi available nahi.
    """
    sign = 1.0 if direction == "UP" else -1.0
    max_horizon = max(HORIZONS_MINUTES)
    window_end_time = breakout_time + timedelta(minutes=max_horizon)

    times = _as_utc_series(df["Time"])
    forward_df = df[(times > breakout_time) & (times <= window_end_time + timedelta(minutes=MATCH_TOLERANCE_MINUTES))]
    if forward_df.empty:
        return None

    # Poora window available hai ya nahi — agar last candle window_end se
    # bahut peeche hai to abhi incomplete hai, skip karo (future data nahi hai).
    last_time = _as_utc_series(forward_df["Time"]).max()
    if last_time < window_end_time - timedelta(minutes=MATCH_TOLERANCE_MINUTES):
        return None

    horizon_moves = {}
    for h in HORIZONS_MINUTES:
        target = breakout_time + timedelta(minutes=h)
        row = _find_candle_at(df, target)
        if row is None:
            return None
        pct_move = sign * (float(row["Close"]) - breakout_price) / breakout_price * 100
        horizon_moves[h] = round(pct_move, 3)

    # Max favorable / adverse move within the full tracking window (High/Low based)
    max_favorable = 0.0
    max_adverse = 0.0
    for _, r in forward_df.iterrows():
        high_move = sign * (float(r["High"]) - breakout_price) / breakout_price * 100
        low_move = sign * (float(r["Low"]) - breakout_price) / breakout_price * 100
        candidate_favorable = max(high_move, low_move)
        candidate_adverse = min(high_move, low_move)
        if candidate_favorable > max_favorable:
            max_favorable = candidate_favorable
        if candidate_adverse < max_adverse:
            max_adverse = candidate_adverse

    return {
        "horizon_moves": horizon_moves,
        "max_favorable_pct": round(max_favorable, 3),
        "max_adverse_pct": round(max_adverse, 3),
        "forward_df": forward_df,
    }


def _check_target_hits(forward_df, breakout_price, direction, target_pcts, window_minutes, breakout_time):
    """
    Har target_pct ke liye check karta hai ki TARGET_WINDOW_MINUTES ke andar
    (High/Low based, intrabar touch bhi count) wo target breakout_price se
    hit hua ya nahi.
    UP breakout: +X% hit = High >= breakout_price * (1 + X/100)
    DOWN breakdown: -X% hit = Low <= breakout_price * (1 - X/100)
    Return: dict {pct: True/False}
    """
    window_end = breakout_time + timedelta(minutes=window_minutes)
    times = _as_utc_series(forward_df["Time"])
    window_df = forward_df[times <= window_end + timedelta(minutes=MATCH_TOLERANCE_MINUTES)]

    hits = {pct: False for pct in target_pcts}
    if window_df.empty:
        return hits

    if direction == "UP":
        max_high = float(window_df["High"].max())
        for pct in target_pcts:
            target_price = breakout_price * (1 + pct / 100)
            hits[pct] = max_high >= target_price
    else:
        min_low = float(window_df["Low"].min())
        for pct in target_pcts:
            target_price = breakout_price * (1 - pct / 100)
            hits[pct] = min_low <= target_price

    return hits


def _did_return_inside(forward_df, level, direction):
    """
    Breakout ke baad kya price decisively level ke "andar" (galat side)
    wapas close hua kabhi bhi tracking window mein.
    """
    for _, r in forward_df.iterrows():
        close_ = float(r["Close"])
        if direction == "UP" and close_ < level:
            return True
        if direction == "DOWN" and close_ > level:
            return True
    return False


# ============================================
# EK PAIR KA WALK-FORWARD BACKTEST
# ============================================
def backtest_pair(pair, collected_rows):
    try:
        df = get_candles(pair=pair, resolution=RESOLUTION, days=DAYS_OF_HISTORY)
    except Exception as e:
        print(f"  {pair}: candles fetch error: {e}")
        return

    if df is None or df.empty or len(df) < SR_LOOKBACK + LOOKBACK_LONG + max(HORIZONS_MINUTES) // RESOLUTION_MINUTES + 5:
        return

    df = df.reset_index(drop=True)
    df = compute_rvol(df, [LOOKBACK_SHORT, LOOKBACK_LONG])

    start_idx = max(SR_LOOKBACK, LOOKBACK_LONG) + 1
    max_horizon_candles = max(HORIZONS_MINUTES) // RESOLUTION_MINUTES
    end_idx = len(df) - max_horizon_candles - 1   # itni candles chahiye taaki poora forward window mile

    if end_idx <= start_idx:
        return

    cached_resistance = []
    cached_support = []
    debounce_until_idx = -1
    signals_found = 0

    for i in range(start_idx, end_idx):
        history_df = df.iloc[: i + 1]   # sirf abhi tak ka data — future-leak nahi

        if i <= debounce_until_idx:
            continue

        # ---- S/R recompute (performance ke liye har N candle pe hi) ----
        if (i - start_idx) % SR_RECALC_EVERY_N_CANDLES == 0 or not cached_resistance and not cached_support:
            try:
                res_raw, sup_raw = get_support_resistance(
                    history_df, lookback=SR_LOOKBACK,
                    cluster_tolerance_pct=SR_CLUSTER_TOLERANCE_PCT,
                )
                cached_resistance = [r for r in res_raw if r[1] >= MIN_SR_TOUCHES]
                cached_support = [s for s in sup_raw if s[1] >= MIN_SR_TOUCHES]
            except Exception as e:
                print(f"  [S/R] {pair} idx={i} error: {e}")
                continue

        if not cached_resistance and not cached_support:
            continue   # koi valid (5+ touch) level hi nahi is window mein

        row = df.iloc[i]
        prev_close = float(df.iloc[i - 1]["Close"])
        close = float(row["Close"])

        try:
            sr_result = classify_price_position(
                close, prev_close, cached_resistance, cached_support,
                proximity_pct=SR_PROXIMITY_PCT,
            )
        except Exception as e:
            print(f"  [classify] {pair} idx={i} error: {e}")
            continue

        price_position = sr_result["label"]
        if price_position not in ("BREAKOUT_ABOVE_RESISTANCE", "BREAKDOWN_BELOW_SUPPORT"):
            continue

        sr_level_price = sr_result["level_price"]
        sr_touch_count = sr_result["touch_count"]
        if sr_level_price is None or sr_touch_count < MIN_SR_TOUCHES:
            continue

        direction = "UP" if price_position == "BREAKOUT_ABOVE_RESISTANCE" else "DOWN"
        breakout_time = pd.to_datetime(row["Time"])
        if breakout_time.tzinfo is None:
            breakout_time = breakout_time.tz_localize("UTC")
        breakout_price = close

        # ---- Pre-breakout consolidation (existing function, koi change nahi) ----
        try:
            consolidation_candles = count_pre_breakout_consolidation(
                history_df, sr_level_price,
                band_pct=CONSOLIDATION_BAND_PCT,
                max_lookback=CONSOLIDATION_MAX_LOOKBACK,
            )
        except Exception as e:
            print(f"  [Consolidation] {pair} idx={i} error: {e}")
            continue

        bucket = _consolidation_bucket(consolidation_candles)
        if bucket is None:
            # 20+ candles wala case — abhi ke buckets se bahar, raw row
            # mein record kar denge, summary mein natural tarike se exclude
            # ho jayega.
            pass

        # ---- Forward movement track karo ----
        movement = _track_forward_movement(df, i, breakout_time, breakout_price, direction)
        if movement is None:
            continue   # poora forward window abhi available nahi (data ki end ke paas)

        returned_inside = _did_return_inside(movement["forward_df"], sr_level_price, direction)
        final_move = movement["horizon_moves"][max(HORIZONS_MINUTES)]
        successful = (not returned_inside) and (final_move > 0)

        target_hits = _check_target_hits(
            movement["forward_df"], breakout_price, direction,
            TARGET_PCTS, TARGET_WINDOW_MINUTES, breakout_time,
        )

        rvol_20 = row.get(f"RVOL_{LOOKBACK_SHORT}", float("nan"))
        rvol_96 = row.get(f"RVOL_{LOOKBACK_LONG}", float("nan"))

        raw_row = [
            pair,
            str(breakout_time),
            direction,
            round(float(sr_level_price), 8),
            sr_touch_count,
            float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"]),
            float(row["Volume"]),
            round(float(rvol_20), 2) if pd.notna(rvol_20) else "",
            round(float(rvol_96), 2) if pd.notna(rvol_96) else "",
            consolidation_candles,
            consolidation_candles * RESOLUTION_MINUTES,
            breakout_price,
            movement["horizon_moves"][15], movement["horizon_moves"][30],
            movement["horizon_moves"][45], movement["horizon_moves"][60],
            movement["horizon_moves"][90], movement["horizon_moves"][120],
            movement["max_favorable_pct"], movement["max_adverse_pct"],
            "YES" if returned_inside else "NO",
            "YES" if successful else "NO",
            "YES" if target_hits[0.5] else "NO",
            "YES" if target_hits[1] else "NO",
            "YES" if target_hits[2] else "NO",
            "YES" if target_hits[3] else "NO",
            "YES" if target_hits[5] else "NO",
            bucket if bucket else "20+ candles",
        ]

        log_raw_row(raw_row)
        collected_rows.append({
            "consolidation_candles": consolidation_candles,
            "bucket": bucket if bucket else "20+ candles",
            "successful": successful,
            "moves": movement["horizon_moves"],
            "max_favorable_pct": movement["max_favorable_pct"],
            "max_adverse_pct": movement["max_adverse_pct"],
            "target_hits": target_hits,
        })

        signals_found += 1
        debounce_until_idx = i + POST_BREAKOUT_DEBOUNCE_CANDLES

    if signals_found:
        print(f"  {pair}: {signals_found} breakout signal(s) mile.")


# ============================================
# BUCKET-WISE SUMMARY
# ============================================
def build_summary(collected_rows):
    bucket_labels = [b[0] for b in CONSOLIDATION_BUCKETS] + ["20+ candles"]
    summary_rows = []
    for label in bucket_labels:
        bucket_data = [r for r in collected_rows if r["bucket"] == label]
        total = len(bucket_data)
        if total == 0:
            summary_rows.append([label, 0, 0, 0, "", "", "", "", "", "", "", "", "", "", "", ""])
            continue
        successful = sum(1 for r in bucket_data if r["successful"])
        failed = total - successful
        success_rate = round(successful / total * 100, 2)
        avg_15 = round(sum(r["moves"][15] for r in bucket_data) / total, 3)
        avg_30 = round(sum(r["moves"][30] for r in bucket_data) / total, 3)
        avg_60 = round(sum(r["moves"][60] for r in bucket_data) / total, 3)
        avg_120 = round(sum(r["moves"][120] for r in bucket_data) / total, 3)
        avg_fav = round(sum(r["max_favorable_pct"] for r in bucket_data) / total, 3)
        avg_adv = round(sum(r["max_adverse_pct"] for r in bucket_data) / total, 3)
        target_hit_rates = {}
        for pct in TARGET_PCTS:
            hits = sum(1 for r in bucket_data if r["target_hits"][pct])
            target_hit_rates[pct] = round(hits / total * 100, 2)
        summary_rows.append([
            label, total, successful, failed, success_rate,
            avg_15, avg_30, avg_60, avg_120, avg_fav, avg_adv,
            target_hit_rates[0.5], target_hit_rates[1], target_hit_rates[2],
            target_hit_rates[3], target_hit_rates[5],
        ])
    return summary_rows


# ============================================
# ENTRY POINT
# ============================================
def run_backtest():
    print(f"\n{'=' * 60}")
    print(f"CONSOLIDATION BACKTEST STARTED: {datetime.now()}")
    print('=' * 60)
    print(f"DRY_RUN={DRY_RUN} | DAYS_OF_HISTORY={DAYS_OF_HISTORY} | MIN_SR_TOUCHES={MIN_SR_TOUCHES}")
    print("NOTE: 'consolidation >= 6 candles = genuine breakout' KOI hardcoded assumption "
          "nahi hai — sirf data collect + bucket-compare ho raha hai.\n")

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

    print(f"Backtesting {len(pairs)} pairs, {DAYS_OF_HISTORY} din ka data...\n")

    collected_rows = []
    for pair in pairs:
        try:
            backtest_pair(pair, collected_rows)
        except Exception as e:
            print(f"  {pair} pe unexpected error, skip: {e}")
        time.sleep(SLEEP_BETWEEN_PAIRS)

    print(f"\nTotal signals collected: {len(collected_rows)}")

    summary_rows = build_summary(collected_rows)
    write_summary(summary_rows)

    print("\n--- BUCKET-WISE SUMMARY ---")
    for row in summary_rows:
        label, total = row[0], row[1]
        if total == 0:
            print(f"  {label}: koi signal nahi mila")
            continue
        (_, _, successful, failed, success_rate, avg_15, avg_30, avg_60, avg_120, avg_fav, avg_adv,
         hit_05, hit_1, hit_2, hit_3, hit_5) = row
        print(
            f"  {label}: total={total} | success_rate={success_rate}% "
            f"(win={successful}, loss={failed}) | avg_15m={avg_15}% avg_30m={avg_30}% "
            f"avg_60m={avg_60}% avg_120m={avg_120}% | avg_max_fav={avg_fav}% avg_max_adv={avg_adv}%"
        )
        print(
            f"      target-hit-rate (within 120m): "
            f"0.5%={hit_05}% | 1%={hit_1}% | 2%={hit_2}% | 3%={hit_3}% | 5%={hit_5}%"
        )

    print(f"\nScan complete: {datetime.now()}")
    print("Raw signals -> 'Consolidation_Backtest' tab | Summary -> 'Consolidation_Backtest_Summary' tab")


if __name__ == "__main__":
    run_backtest()

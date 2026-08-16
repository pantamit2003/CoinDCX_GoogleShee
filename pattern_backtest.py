"""
pattern_backtest.py  (v1 — Breakout/Dominance/Confusion Pattern Data Collection)
=====================================================================
POORI TARAH ALAG, STANDALONE MODULE — backtest_tracker.py (breakout/
retest trading logic) ko BILKUL touch nahi karta, na uski koi file/
tab/logic modify karta hai. Yeh sirf ISI naye pattern ke liye data
collect karta hai, taaki baad mein alag se backtest ho sake.

PATTERN JO DETECT KARNA HAI:
    1. Breakout candle          (S/R level break — resistance/support)
    2. Dominance candle         (breakout ke turant baad, same direction)
    3. Confirmation/Dominance   (uske baad, phir se same-direction strong candle)
    4. Ek ya zyada CONFUSION candles (indecision phase)
    5. Confusion-zone High/Low todna (jab price finally range se nikle)
    6. Uske baad ka retest/reaction track karna

IMPORTANT:
    - Koi Telegram alert NAHI bhejta — sirf data collect karta hai.
    - Setup ko abhi "trading signal" NAHI maana jaata — sirf record hota hai.
    - Incomplete setups DISCARD nahi hote — unka current stage/state
      Google Sheet mein save rehta hai, agli run mein wahi se continue
      hota hai (jab tak naye candles available na ho jaayein).

GOOGLE SHEET TABS (dono naye, existing kisi tab ko touch nahi karte):
    - Pattern_Pending       — jo setups abhi track ho rahe hain (in-progress state, JSON blob mein)
    - Pattern_Backtest_Data — jo setups final ho chuke hain (complete ya invalid — dono record hote hain)

STATE REPRESENTATION:
    Pending state (candle-by-candle build hota hua pattern) ek JSON
    blob mein store hota hai (`State_JSON` column) — kyunki confusion
    candles ki count variable hai, fixed columns se yeh clean rakhna
    mushkil hota. Jab pattern "resolve" ho jaata hai (complete ya
    invalid), poori flat-row Pattern_Backtest_Data mein likhi jaati
    hai, doc mein maangi gayi saari columns ke saath.
"""

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from data.candles import get_candles
from candle_shape import classify_candle_shape
import config


RESOLUTION = "15"
RESOLUTION_MINUTES = 15
MATCH_TOLERANCE_MINUTES = 7

MAX_CONFUSION_CANDLES = 12       # confusion phase itni der tak hi wait karo (3 ghante)
OUTCOME_HORIZONS = [1, 3, 5]     # zone-break ke baad 1/3/5 candles tak outcome track karna
MAX_AGE_HOURS = 12               # itne ghante baad bhi setup resolve na ho, to jo bhi data hai
                                  # usi ke saath "STALE_INCOMPLETE" maar ke finalize kar do
                                  # (discard NAHI karte, jo bhi mila woh record hota hai)

RETEST_PROXIMITY_PCT = 0.5       # zone-break level ke retest ke liye tolerance

PENDING_WORKSHEET_NAME = "Pattern_Pending"
RESULTS_WORKSHEET_NAME = "Pattern_Backtest_Data"

PENDING_HEADER = ["Pair", "Breakout_Time_UTC", "Stage", "State_JSON"]

RESULTS_HEADER = [
    "Pair", "Timestamp_UTC", "Final_Status",
    # Breakout candle
    "Breakout_Direction", "Breakout_Level",
    "Breakout_Open", "Breakout_High", "Breakout_Low", "Breakout_Close",
    "Breakout_Body_Pct", "Breakout_Upper_Wick_Pct", "Breakout_Lower_Wick_Pct",
    "Breakout_Volume", "Breakout_RVOL_20",
    # Dominance candle (N+1)
    "Dominance_Open", "Dominance_High", "Dominance_Low", "Dominance_Close",
    "Dominance_Body_Pct", "Dominance_Upper_Wick_Pct", "Dominance_Lower_Wick_Pct",
    "Dominance_Direction",
    # Confirmation candle (N+2)
    "Confirmation_Open", "Confirmation_High", "Confirmation_Low", "Confirmation_Close",
    "Confirmation_Body_Pct", "Confirmation_Upper_Wick_Pct", "Confirmation_Lower_Wick_Pct",
    "Confirmation_Direction",
    # Confusion candles (variable count — JSON blob + summary)
    "Confusion_Count", "Confusion_Candles_JSON",
    "Confusion_Zone_High", "Confusion_Zone_Low",
    # Zone break
    "Zone_Break_Time_UTC", "Zone_Break_Direction", "Zone_Break_Close",
    # Retest / reaction (zone-break level ke against)
    "Retest_Detected", "Retest_Timestamp", "Retest_Price", "Retest_Depth_Pct",
    "Level_Held", "Level_Failed", "Post_Retest_Direction",
    # Outcome tracking (zone-break candle se aage)
    "Price_After_1", "Price_After_3", "Price_After_5",
    "PctChg_After_1", "PctChg_After_3", "PctChg_After_5",
    "MFE_Pct", "MAE_Pct",
]


# ============================================
# GOOGLE SHEETS CONNECTION
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
        _pending_ws.append_row(PENDING_HEADER, table_range="A1")
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
        _results_ws.append_row(RESULTS_HEADER, table_range="A1")
    return _results_ws


# ============================================
# HELPERS
# ============================================
def _to_utc_dt(value):
    dt = pd.to_datetime(value)
    if dt.tzinfo is None:
        dt = dt.tz_localize("UTC")
    return dt.to_pydatetime()


def _wick_pcts(open_price, high, low, close):
    """
    Upper/lower wick % nikalta hai (candle_shape.py isse return nahi karta,
    isliye yahan alag se, chhota, independent calculation — candle_shape.py
    ko modify nahi karna, jaisa instruction hai).
    """
    candle_range = high - low
    if candle_range == 0:
        return 0.0, 0.0
    upper_wick = high - max(open_price, close)
    lower_wick = min(open_price, close) - low
    return round((upper_wick / candle_range) * 100, 1), round((lower_wick / candle_range) * 100, 1)


def _candle_snapshot(row):
    """Ek candle row se poora OHLCV + derived metrics dict banata hai (JSON-safe)."""
    o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
    v = float(row["Volume"]) if "Volume" in row and row["Volume"] is not None else None
    upper_pct, lower_pct = _wick_pcts(o, h, l, c)
    shape_ctx = classify_candle_shape(o, h, l, c)
    return {
        "time": str(row["Time"]),
        "open": o, "high": h, "low": l, "close": c, "volume": v,
        "body_pct": shape_ctx["body_pct"],
        "upper_wick_pct": upper_pct,
        "lower_wick_pct": lower_pct,
        "direction": shape_ctx["direction"],
        "shape": shape_ctx["shape"],
    }


def _find_row_at(df, target_time, tolerance_minutes=MATCH_TOLERANCE_MINUTES):
    """target_time ke sabse paas wali candle dhoondta hai (tolerance ke andar)."""
    df_times = df["Time"]
    if df_times.dt.tz is None:
        df_times = df_times.dt.tz_localize("UTC")
    diffs = (df_times - target_time).abs()
    min_idx = diffs.idxmin()
    if diffs.loc[min_idx] <= timedelta(minutes=tolerance_minutes):
        return df.loc[min_idx]
    return None


def _find_next_row_after(df, after_time, tolerance_minutes=MATCH_TOLERANCE_MINUTES):
    """after_time se turant agli (chronologically next) candle dhoondta hai."""
    df_times = df["Time"]
    if df_times.dt.tz is None:
        df_times = df_times.dt.tz_localize("UTC")
    target = after_time + timedelta(minutes=RESOLUTION_MINUTES)
    diffs = (df_times - target).abs()
    candidates = df_times[df_times > after_time]
    if candidates.empty:
        return None
    # sabse paas wali candle jo after_time ke baad hai
    idx = (df_times[df_times > after_time] - target).abs().idxmin()
    if abs((df_times.loc[idx] - target)) <= timedelta(minutes=tolerance_minutes):
        return df.loc[idx]
    return None


# ============================================
# PUBLIC: add_pending_pattern — naya breakout track karna shuru karo
# ============================================
def add_pending_pattern(pair, direction, breakout_time, breakout_level,
                         breakout_open, breakout_high, breakout_low, breakout_close,
                         breakout_volume, rvol_20):
    """
    Naya breakout candle detect hone pe isse call karo (sirf
    BREAKOUT_ABOVE_RESISTANCE / BREAKDOWN_BELOW_SUPPORT ke liye —
    is pattern ka anchor point ek genuine level-break hai).

    direction: "UP" ya "DOWN"
    """
    ws = _get_pending_worksheet()

    # Simple cooldown — agar pair already kisi pattern ko track kar raha
    # hai, naya breakout skip karo (jaise backtest_tracker.py mein hai)
    existing = ws.get_all_records()
    for r in existing:
        if r.get("Pair") == pair and r.get("Stage") not in (None, "", "DONE"):
            return

    upper_pct, lower_pct = _wick_pcts(breakout_open, breakout_high, breakout_low, breakout_close)
    state = {
        "pair": pair,
        "direction": direction,
        "breakout_time": str(_to_utc_dt(breakout_time)),
        "breakout_level": float(breakout_level) if breakout_level not in (None, "") else None,
        "breakout": {
            "open": float(breakout_open), "high": float(breakout_high),
            "low": float(breakout_low), "close": float(breakout_close),
            "volume": float(breakout_volume) if breakout_volume is not None else None,
            "body_pct": classify_candle_shape(
                breakout_open, breakout_high, breakout_low, breakout_close
            )["body_pct"],
            "upper_wick_pct": upper_pct, "lower_wick_pct": lower_pct,
        },
        "rvol_20": round(float(rvol_20), 2),
        "dominance_candle": None,
        "confirmation_candle": None,
        "confusion_candles": [],
        "zone_high": None,
        "zone_low": None,
        "zone_break_time": None,
        "zone_break_direction": None,
        "zone_break_candle": None,
        "final_status": None,
    }

    ws.append_row([
        pair, state["breakout_time"], "AWAIT_DOMINANCE", json.dumps(state),
    ], table_range="A1")


# ============================================
# INTERNAL: pattern state machine ko ek candle-set ke against advance karna
# ============================================
def _advance_pattern(state, df, now_utc):
    """
    State ko jitna aage badha sakte hain (available candles ke hisaab
    se) badhata hai. Modify karta hai state dict in-place, aur return
    karta hai True agar pattern FINAL ho gaya (complete ya invalid),
    False agar abhi aur wait karna hai.
    """
    breakout_time = _to_utc_dt(state["breakout_time"])
    direction = state["direction"]   # "UP" / "DOWN"

    def is_same_direction_dominance(snap):
        return snap["shape"] == "DOMINANCE" and (
            (snap["direction"] == "GREEN" and direction == "UP")
            or (snap["direction"] == "RED" and direction == "DOWN")
        )

    stage = None  # local re-derive from state fields (JSON doesn't store "stage" string itself)

    # ---- STEP 1: Dominance candle (N+1) ----
    if state["dominance_candle"] is None:
        target = breakout_time + timedelta(minutes=RESOLUTION_MINUTES * 1)
        row = _find_row_at(df, target)
        if row is None:
            return False   # abhi candle aayi hi nahi
        snap = _candle_snapshot(row)
        if not is_same_direction_dominance(snap):
            state["final_status"] = "NO_PATTERN_MATCH_NO_DOMINANCE"
            return True
        state["dominance_candle"] = snap

    # ---- STEP 2: Confirmation/Dominance candle (N+2) ----
    if state["confirmation_candle"] is None:
        target = breakout_time + timedelta(minutes=RESOLUTION_MINUTES * 2)
        row = _find_row_at(df, target)
        if row is None:
            return False
        snap = _candle_snapshot(row)
        if not is_same_direction_dominance(snap):
            state["final_status"] = "NO_PATTERN_MATCH_NO_CONFIRMATION"
            return True
        state["confirmation_candle"] = snap

    # ---- STEP 3: Confusion candle(s) — ek ya zyada, phir zone-break candle ----
    if state["zone_break_candle"] is None:
        # last processed candle ka time pata karo (confirmation ya last confusion candle)
        if state["confusion_candles"]:
            last_time = _to_utc_dt(state["confusion_candles"][-1]["time"])
        else:
            last_time = breakout_time + timedelta(minutes=RESOLUTION_MINUTES * 2)

        while True:
            if len(state["confusion_candles"]) >= MAX_CONFUSION_CANDLES:
                state["final_status"] = "NO_PATTERN_MATCH_CONFUSION_TOO_LONG"
                return True

            row = _find_next_row_after(df, last_time)
            if row is None:
                return False   # agli candle abhi aayi nahi, wait karo

            snap = _candle_snapshot(row)

            if snap["shape"] == "CONFUSION":
                state["confusion_candles"].append(snap)
                state["zone_high"] = snap["high"] if state["zone_high"] is None else max(state["zone_high"], snap["high"])
                state["zone_low"] = snap["low"] if state["zone_low"] is None else min(state["zone_low"], snap["low"])
                last_time = _to_utc_dt(snap["time"])
                continue
            else:
                # yeh candle CONFUSION nahi hai — confusion phase khatam
                if not state["confusion_candles"]:
                    # ek bhi confusion candle nahi mili — pattern is specific
                    # structure ko match nahi karta (doc: "one or more" required)
                    state["final_status"] = "NO_PATTERN_MATCH_NO_CONFUSION"
                    return True
                state["zone_break_candle"] = snap
                # zone-break direction determine karo
                broke_up = snap["high"] > state["zone_high"]
                broke_down = snap["low"] < state["zone_low"]
                if broke_up and broke_down:
                    # dono taraf toda (wide candle) — jo original direction se match kare use priority do
                    state["zone_break_direction"] = "BREAK_UP" if direction == "UP" else "BREAK_DOWN"
                elif broke_up:
                    state["zone_break_direction"] = "BREAK_UP"
                elif broke_down:
                    state["zone_break_direction"] = "BREAK_DOWN"
                else:
                    # na upar toda na neeche — bas ek non-confusion candle jo
                    # zone ke andar hi rahi. Ise bhi zone mein extend kar do
                    # aur aage dekhna jaari rakho (rare edge case).
                    state["zone_high"] = max(state["zone_high"], snap["high"])
                    state["zone_low"] = min(state["zone_low"], snap["low"])
                    last_time = _to_utc_dt(snap["time"])
                    continue
                state["zone_break_time"] = snap["time"]
                break

    # ---- STEP 4: Outcome tracking (zone-break ke baad 1/3/5 candles + retest + MFE/MAE) ----
    zone_break_time = _to_utc_dt(state["zone_break_candle"]["time"])
    zone_break_close = state["zone_break_candle"]["close"]
    zbd = state["zone_break_direction"]   # BREAK_UP / BREAK_DOWN

    max_horizon = max(OUTCOME_HORIZONS)
    max_target = zone_break_time + timedelta(minutes=RESOLUTION_MINUTES * max_horizon)
    row_max = _find_row_at(df, max_target)
    if row_max is None:
        return False   # poora outcome-window abhi complete nahi hua

    # price after 1/3/5 candles
    prices_after = {}
    for h in OUTCOME_HORIZONS:
        target = zone_break_time + timedelta(minutes=RESOLUTION_MINUTES * h)
        row_h = _find_row_at(df, target)
        if row_h is None:
            return False
        prices_after[h] = float(row_h["Close"])

    # retest of zone-break level (jo boundary toda gaya tha, wahi level)
    retest_level = state["zone_high"] if zbd == "BREAK_UP" else state["zone_low"]
    df_times = df["Time"]
    if df_times.dt.tz is None:
        df_times = df_times.dt.tz_localize("UTC")
    window_df = df[(df_times > zone_break_time) & (df_times <= max_target)].copy()
    window_df["_t"] = df_times[(df_times > zone_break_time) & (df_times <= max_target)]

    proximity_frac = RETEST_PROXIMITY_PCT / 100.0
    retest_row = None
    for _, cand in window_df.sort_values("_t").iterrows():
        low_, high_ = float(cand["Low"]), float(cand["High"])
        if zbd == "BREAK_UP" and low_ <= retest_level * (1 + proximity_frac):
            retest_row = cand
            break
        if zbd == "BREAK_DOWN" and high_ >= retest_level * (1 - proximity_frac):
            retest_row = cand
            break

    retest_info = {
        "detected": "NO", "time": "", "price": "", "depth_pct": "",
        "level_held": "", "level_failed": "", "post_direction": "",
    }
    if retest_row is not None:
        retest_info["detected"] = "YES"
        retest_info["time"] = str(retest_row["_t"])
        if zbd == "BREAK_UP":
            extreme = float(retest_row["Low"])
            retest_info["depth_pct"] = round((retest_level - extreme) / retest_level * 100, 3)
        else:
            extreme = float(retest_row["High"])
            retest_info["depth_pct"] = round((extreme - retest_level) / retest_level * 100, 3)
        retest_info["price"] = extreme

        after_retest = window_df[window_df["_t"] > retest_row["_t"]].sort_values("_t")
        decisive_fail = False
        for _, cand in after_retest.iterrows():
            close_ = float(cand["Close"])
            if zbd == "BREAK_UP" and close_ < retest_level:
                decisive_fail = True
                break
            if zbd == "BREAK_DOWN" and close_ > retest_level:
                decisive_fail = True
                break
        retest_info["level_held"] = "NO" if decisive_fail else "YES"
        retest_info["level_failed"] = "YES" if decisive_fail else "NO"
        if not after_retest.empty:
            first_after = after_retest.iloc[0]
            retest_info["post_direction"] = "GREEN" if float(first_after["Close"]) >= float(first_after["Open"]) else "RED"

    # MFE / MAE — poore outcome-window (zone-break se max_horizon tak) ka
    def signed_pct(price):
        if zbd == "BREAK_UP":
            return (price - zone_break_close) / zone_break_close * 100
        return (zone_break_close - price) / zone_break_close * 100

    favorable_vals, adverse_vals = [], []
    for _, cand in window_df.iterrows():
        high_, low_ = float(cand["High"]), float(cand["Low"])
        if zbd == "BREAK_UP":
            favorable_vals.append(signed_pct(high_))
            adverse_vals.append(signed_pct(low_))
        else:
            favorable_vals.append(signed_pct(low_))
            adverse_vals.append(signed_pct(high_))

    mfe = round(max(favorable_vals), 3) if favorable_vals else 0.0
    mae = round(min(adverse_vals), 3) if adverse_vals else 0.0

    state["outcome"] = {
        "price_after_1": prices_after[1], "price_after_3": prices_after[3], "price_after_5": prices_after[5],
        "pct_after_1": round((prices_after[1] - zone_break_close) / zone_break_close * 100, 3),
        "pct_after_3": round((prices_after[3] - zone_break_close) / zone_break_close * 100, 3),
        "pct_after_5": round((prices_after[5] - zone_break_close) / zone_break_close * 100, 3),
        "mfe_pct": mfe, "mae_pct": mae,
        "retest": retest_info,
    }
    state["final_status"] = "PATTERN_COMPLETE"
    return True


def _state_to_result_row(pair, state):
    """Finalized state se Pattern_Backtest_Data ki flat row banata hai."""
    b = state["breakout"]
    d = state.get("dominance_candle") or {}
    c = state.get("confirmation_candle") or {}
    confusion = state.get("confusion_candles", [])
    zb = state.get("zone_break_candle") or {}
    outcome = state.get("outcome", {})
    retest = outcome.get("retest", {})

    return [
        pair,
        state["breakout_time"],
        state["final_status"],
        state["direction"],
        state["breakout_level"] if state["breakout_level"] is not None else "",
        b["open"], b["high"], b["low"], b["close"],
        b["body_pct"], b["upper_wick_pct"], b["lower_wick_pct"],
        b["volume"] if b["volume"] is not None else "",
        state["rvol_20"],
        d.get("open", ""), d.get("high", ""), d.get("low", ""), d.get("close", ""),
        d.get("body_pct", ""), d.get("upper_wick_pct", ""), d.get("lower_wick_pct", ""),
        d.get("direction", ""),
        c.get("open", ""), c.get("high", ""), c.get("low", ""), c.get("close", ""),
        c.get("body_pct", ""), c.get("upper_wick_pct", ""), c.get("lower_wick_pct", ""),
        c.get("direction", ""),
        len(confusion), json.dumps(confusion),
        state.get("zone_high", "") if state.get("zone_high") is not None else "",
        state.get("zone_low", "") if state.get("zone_low") is not None else "",
        state.get("zone_break_time", "") or "",
        state.get("zone_break_direction", "") or "",
        zb.get("close", ""),
        retest.get("detected", ""), retest.get("time", ""), retest.get("price", ""), retest.get("depth_pct", ""),
        retest.get("level_held", ""), retest.get("level_failed", ""), retest.get("post_direction", ""),
        outcome.get("price_after_1", ""), outcome.get("price_after_3", ""), outcome.get("price_after_5", ""),
        outcome.get("pct_after_1", ""), outcome.get("pct_after_3", ""), outcome.get("pct_after_5", ""),
        outcome.get("mfe_pct", ""), outcome.get("mae_pct", ""),
    ]


# ============================================
# PUBLIC: resolve_pending_patterns — har run ki shuruaat mein call karo
# ============================================
def resolve_pending_patterns(dry_run=False):
    pending_ws = _get_pending_worksheet()
    records = pending_ws.get_all_records()

    if not records:
        print("  [pattern_backtest] Koi pending pattern nahi hai.")
        return

    now_utc = datetime.now(timezone.utc)
    pairs_needed = {r["Pair"] for r in records}
    candle_cache = {}
    for pair in pairs_needed:
        try:
            candle_cache[pair] = get_candles(pair=pair, resolution=RESOLUTION, days=2)
        except Exception as e:
            print(f"  [pattern_backtest] {pair} candles fetch error: {e}")
            candle_cache[pair] = None

    still_pending_rows = []
    completed_count = 0
    invalid_count = 0
    stale_count = 0

    for r in records:
        pair = r["Pair"]
        try:
            state = json.loads(r["State_JSON"])
        except (json.JSONDecodeError, KeyError):
            print(f"  [pattern_backtest] {pair} — corrupt state, skip.")
            continue

        breakout_time = _to_utc_dt(state["breakout_time"])
        df = candle_cache.get(pair)

        finalized = False
        if df is not None and not df.empty:
            try:
                finalized = _advance_pattern(state, df, now_utc)
            except Exception as e:
                print(f"  [pattern_backtest] {pair} advance error: {e}")

        # Bahut purana ho gaya (data-gap/delist) — jo bhi mila usi ke saath finalize karo
        if not finalized and (now_utc - breakout_time) > timedelta(hours=MAX_AGE_HOURS):
            state["final_status"] = "STALE_INCOMPLETE"
            finalized = True
            stale_count += 1

        if finalized:
            if dry_run:
                print(f"  [pattern_backtest][DRY_RUN] FINAL: {pair} -> {state['final_status']}")
            else:
                try:
                    row = _state_to_result_row(pair, state)
                    _get_results_worksheet().append_row(row, table_range="A1")
                except Exception as e:
                    print(f"  [pattern_backtest] {pair} results write error: {e}")
                    still_pending_rows.append((pair, state))
                    continue
            if state["final_status"] == "PATTERN_COMPLETE":
                completed_count += 1
            elif state["final_status"] != "STALE_INCOMPLETE":
                invalid_count += 1
        else:
            still_pending_rows.append((pair, state))

    if not dry_run:
        try:
            clean_rows = [
                [pair, state["breakout_time"], _current_stage_label(state), json.dumps(state)]
                for pair, state in still_pending_rows
            ]
            pending_ws.clear()
            pending_ws.update([PENDING_HEADER] + clean_rows)
        except Exception as e:
            print(f"  [pattern_backtest] Pattern_Pending update error: {e}")
    else:
        print(f"  [pattern_backtest][DRY_RUN] Still pending: {len(still_pending_rows)}")

    print(f"  [pattern_backtest] Completed: {completed_count} | Invalid: {invalid_count} | "
          f"Stale: {stale_count} | Still pending: {len(still_pending_rows)}")


def _current_stage_label(state):
    """Sirf human-readable quick-glance label (Sheet mein Stage column ke liye)."""
    if state.get("final_status"):
        return "DONE"
    if state["dominance_candle"] is None:
        return "AWAIT_DOMINANCE"
    if state["confirmation_candle"] is None:
        return "AWAIT_CONFIRMATION"
    if state["zone_break_candle"] is None:
        return f"IN_CONFUSION ({len(state['confusion_candles'])} candles so far)"
    return "AWAIT_OUTCOME"

"""
sr_shape_outcome_tracker.py
=============================
v4 — RESEARCH-MODE REWRITE. Filters relaxed, direction-decision logic
CHANGED (no longer S/R-position-based, ab NEXT-CANDLE breakout se
confirm hoti hai). Purana v2/v3 wala "Support+CONFUSION=LONG" rule
HATA diya gaya hai.

v4.1 — STATE-FLOW FIX:
    NO_CONFIRMATION setups ab kabhi bhi Confusion_Pending mein reh
    NAHI sakte aur kabhi bhi future candles mein dobara confirmation
    ke liye "alive" nahi rehte. (unchanged from before)

v4.2 — FIRST-EVENT OUTCOME TRACKING (THIS REVISION):
    NAYA — resolve_pending() ab, existing per-horizon SL_Hit_*/
    Target_*_Hit booleans ke ALAWA, ek naya "first event" verdict
    bhi calculate karta hai: WIN_1R / LOSS_SL / TIMEOUT / AMBIGUOUS.
    Yeh chronological candle-walk se pehla event (1R target ya SL,
    jo bhi pehle aaye) detect karta hai. Same-candle mein dono touch
    hone par (documented, explicit user requirement) AMBIGUOUS
    maana jaata hai — is baar SL-first assume NAHI kiya jaata (yeh
    purane _analyze_trade_outcome() ke SL-first convention se
    JAANBUJHKAR alag hai — dono conventions ab sheet mein saath-saath
    maujood hain, alag columns mein).
    Naye columns (Confusion_Backtest_Data, end mein append):
        Outcome_1R, Outcome_Time, Target_1R_Price, R_Distance,
        Outcome_R_Multiple, Outcome_Status, MFE_120, MAE_120
    Existing columns (Price_After_*, PctChg_*, Max_Favorable_Move_Pct,
    Max_Adverse_Move_Pct, SL_Hit_*m, Target_*R_Hit) BILKUL UNCHANGED
    hain — dono naya aur purana system saath-saath chalte hain.
    Purani rows RECALCULATE nahi hoti (resolve_pending() sirf
    Confusion_Pending ki active rows process karta hai, already-
    written Confusion_Backtest_Data rows ko kabhi touch nahi karta).
    NAYA function generate_backtest_summary() bhi add hua — poori
    tarah READ-ONLY (Confusion_Backtest_Data se padhta hai), koi
    tracking-flow ko touch nahi karta. Overall summary + V1/V2/V3
    strategy-variant breakdown print karta hai.
    NAYA one-time helper _migrate_results_header() bhi add hua — sirf
    Row 1 (header) update karta hai existing sheet par, data rows
    touch nahi hote.

YEH FLOWCHART IMPLEMENT KARTA HAI (v4, unchanged):
    15-min candle
        |
    Kya price valid Support/Resistance ke paas hai? (touch_count >= MIN_SR_TOUCHES)
        |
       YES  (NO ho to us candle ko skip kar do)
        |
    Candle ka shape kya hai?
        |
    DOMINANCE / REJECTION / CONFUSION
        |
    SIRF CONFUSION wali candles aage jaati hain (DOMINANCE/REJECTION skip)
        |
    Kya is candle ka RVOL bhi threshold cross kar raha hai?
        (RVOL_20 >= RVOL_SHORT_THRESHOLD  YA  RVOL_96 >= RVOL_LONG_THRESHOLD)
        |
       NO -> skip (volume ka support nahi hai)
       YES -> CONFUSION candle ka HIGH/LOW record karo, "AWAITING
              CONFIRMATION" state mein daal do (abhi LONG/SHORT
              decide NAHI karna)
        |
    IMMEDIATELY NEXT (aur SIRF immediately next) 15-min candle kya karti hai?
        CONFUSION HIGH break        -> LONG  (Entry = Confusion High, SL = Confusion Low)
        CONFUSION LOW break         -> SHORT (Entry = Confusion Low,  SL = Confusion High)
        Neither break (ya dono break -> ambiguous) -> NO_CONFIRMATION
             -> setup CLOSED (record ho jaata hai, dobara alive nahi rehta)
        |
    Confirm hone ke baad (LONG/SHORT), confirmation-candle ke time se
    next 15/30/45/60/90/120 min mein kya hua?
    (price movement, SL hit/nahi, target R-multiples hit/nahi,
     max favorable/adverse move, AUR NAYA: first-event WIN_1R/LOSS_SL/
     TIMEOUT/AMBIGUOUS)
        |
    BACKTEST DATA (Google Sheet mein)

KYUN ALAG MODULE (unchanged):
    - backtest_tracker.py RVOL-spike TRIGGER se chalta hai, alag
      horizons/logic use karta hai.
    - Yeh module purana backtest_tracker.py / pattern_backtest.py /
      Trendline / main-Telegram-bot / Consolidation logic / candle-shape
      classification / existing S/R detection ko BILKUL touch nahi
      karta. Sirf apne teen Google Sheet tabs use karta hai.

SL / TARGET LOGIC (v4, unchanged):
    Break Direction = LONG  (immediately next candle CONFUSION HIGH todhi)
        Entry = Confusion candle HIGH  (jahan breakout hua)
        SL    = Confusion candle LOW
        Risk  = Entry - SL
        Target_nR = Entry + n * Risk
    Break Direction = SHORT (immediately next candle CONFUSION LOW todhi)
        Entry = Confusion candle LOW
        SL    = Confusion candle HIGH
        Risk  = SL - Entry
        Target_nR = Entry - n * Risk
    Outcome-horizons (15/30/45/60/90/120 min) ab CONFIRMATION candle
    (immediately next candle) ke time se count hote hain.

    SAME-CANDLE AMBIGUITY — DO ALAG CONVENTIONS (IMPORTANT):
    1. Existing _analyze_trade_outcome() (SL_Hit_*m / Target_*R_Hit
       columns ke liye): agar ek hi candle mein SL aur Target dono
       aa jaayein, CONSERVATIVELY SL-first maana jaata hai.
    2. NAYA _compute_first_event_outcome() (Outcome_1R column ke
       liye): agar ek hi candle mein dono aa jaayein, AMBIGUOUS
       maana jaata hai (explicit user requirement — koi SL-first
       assumption nahi).
    In dono ka result same-candle case mein ALAG ho sakta hai —
    yeh jaanbujhkar hai, dono columns alag purpose serve karte hain.
    Confirmation-candle khud agar CONFUSION HIGH aur LOW dono break
    kare, to us candle ko ambiguous maankar NO_CONFIRMATION record
    kiya jaata hai (yeh Stage-2 confirmation logic hai, first-event
    outcome logic se alag — waha bhi conservative convention hai).

VOLUME / RVOL (v4 — relaxed gate, unchanged formula):
    qualify (Stage 1) = (valid S/R ke paas, touches >= MIN_SR_TOUCHES)
                        AND (shape == CONFUSION)
                        AND (RVOL_20 >= RVOL_SHORT_THRESHOLD
                             OR RVOL_96 >= RVOL_LONG_THRESHOLD)
    Calculation logic bilkul intraday_spike_monitor.py ke
    get_intraday_rvol() jaisa hi hai — koi naya formula nahi.

KAISE HOOK KARNA HAI:
    import sr_shape_outcome_tracker as sr_shape_tracker
    ...
    sr_shape_tracker.resolve_confirmations(dry_run=DRY_RUN)  # scan shuru mein
    sr_shape_tracker.resolve_pending(dry_run=DRY_RUN)        # scan shuru mein
    ...
    sr_shape_tracker.process_candle(pair, df, dry_run=DRY_RUN)  # har pair ke liye

    ONE-TIME (naya sheet-header migrate karne ke liye, script se alag chala lo):
        python -c "import sr_shape_outcome_tracker as t; t._migrate_results_header()"

    Summary dekhne ke liye (kabhi bhi, standalone):
        python -c "import sr_shape_outcome_tracker as t; t.generate_backtest_summary()"
"""
from datetime import datetime, timedelta, timezone
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from data.candles import get_candles
from support_resistance import get_support_resistance, classify_price_position
from candle_shape import classify_candle_shape
from notifications.telegram_bot import send_confusion_telegram_message  # teesra, alag bot
import config

# ============================================
# CONFIG
# ============================================
RESOLUTION = "15"
RESOLUTION_MINUTES = 15

# Flowchart ke horizons: 15/30/45/60/90/120 min => candles 1,2,3,4,6,8
HORIZONS_MINUTES = [15, 30, 45, 60, 90, 120]
HORIZONS_CANDLES = [m // RESOLUTION_MINUTES for m in HORIZONS_MINUTES]  # [1,2,3,4,6,8]

SR_LOOKBACK = 50
SR_CLUSTER_TOLERANCE_PCT = 0.5
SR_PROXIMITY_PCT = 0.5

# NAYA v4: ab hard filter nahi, sirf minimum floor — 2,3,4,5,6+ sab
# record honge (SR_Touch_Count column se baad mein slice karna).
MIN_SR_TOUCHES = 2

# ---- RVOL (volume) config — intraday_spike_monitor.py jaisa hi formula ----
LOOKBACK_SHORT = 20     # RVOL_20 baseline (~5 ghante)
LOOKBACK_LONG = 96      # RVOL_96 baseline (~1 din)

# NAYA v4: relaxed research-phase gate (pehle 5.0 / 6.0 tha)
RVOL_SHORT_THRESHOLD = 2.0   # RVOL_20 is se upar -> volume-gate pass
RVOL_LONG_THRESHOLD = 2.0    # RVOL_96 is se upar -> volume-gate pass
# Gate pass hone ke liye dono mein se sirf EK condition true honi chahiye (OR).

MAX_AGE_HOURS = 6                 # itni der baad bhi resolve na ho paaye to discard
MATCH_TOLERANCE_MINUTES = 7       # future-candle match karte waqt tolerance (fallback path only)

# Risk-reward multiples jinke target-hit backtest mein check karne hain
TARGET_RR = [1, 1.5, 2, 3]

# ---- NAYA: first-event outcome ka observation window ----
# Existing 120-min horizon reuse kiya (max(HORIZONS_MINUTES)) — koi
# naya horizon nahi banaya, jaisa user ne bola tha.
FIRST_EVENT_HORIZON_MINUTES = max(HORIZONS_MINUTES)  # 120


def _rr_label(rr):
    """1 -> '1R', 1.5 -> '1.5R', 2 -> '2R' — column-naming ke liye."""
    return f"{rr:g}R"


# "Confusion" naming ke saath worksheet names
AWAITING_WORKSHEET_NAME = "Confusion_Awaiting_Confirmation"
PENDING_WORKSHEET_NAME = "Confusion_Pending"
RESULTS_WORKSHEET_NAME = "Confusion_Backtest_Data"

# price_position labels jo "Support" side ka concept represent karte hain
# vs "Resistance" side — ab sirf RECORDING/labeling ke liye, DIRECTION
# decide karne ke liye NAHI use hote (v4 change).
SUPPORT_POSITIONS = ("NEAR_SUPPORT", "BREAKDOWN_BELOW_SUPPORT")
RESISTANCE_POSITIONS = ("NEAR_RESISTANCE", "BREAKOUT_ABOVE_RESISTANCE")

AWAITING_HEADER = [
    "Pair", "Candle_Time_UTC", "Candle_Color", "Close", "RVOL_20", "RVOL_96",
    "Price_Position", "SR_Level_Price", "SR_Touch_Count",
    "Candle_Shape", "Shape_Strength", "Body_Pct", "Rejection_Side",
    "Candle_High", "Candle_Low",
    "Status",
]

PENDING_HEADER = [
    "Pair", "Candle_Time_UTC", "Candle_Color", "Close", "RVOL_20", "RVOL_96",
    "Price_Position", "SR_Level_Price", "SR_Touch_Count",
    "Candle_Shape", "Shape_Strength", "Body_Pct", "Rejection_Side",
    "Confusion_High", "Confusion_Low", "Confusion_Close",
    "Next_Candle_Time_UTC", "Next_Candle_High", "Next_Candle_Low",
    "Break_Direction", "Entry_Price", "Stop_Loss", "SL_Distance_Pct",
    "Status",
]

RESULTS_HEADER = (
    ["Pair", "Candle_Time_UTC", "Candle_Color", "Close", "RVOL_20", "RVOL_96",
     "Price_Position", "SR_Level_Price", "SR_Touch_Count",
     "Candle_Shape", "Shape_Strength", "Body_Pct", "Rejection_Side",
     "Confusion_High", "Confusion_Low", "Confusion_Close",
     "Next_Candle_Time_UTC", "Next_Candle_High", "Next_Candle_Low",
     "Break_Direction", "Entry_Price", "Stop_Loss", "SL_Distance_Pct"]
    + [f"Price_After_{m}min" for m in HORIZONS_MINUTES]
    + [f"PctChg_{m}min" for m in HORIZONS_MINUTES]
    + ["Max_Favorable_Move_Pct", "Max_Adverse_Move_Pct"]
    + [f"SL_Hit_{m}m" for m in HORIZONS_MINUTES]
    + [f"Target_{_rr_label(rr)}_Hit" for rr in TARGET_RR]
    # ---- NAYA (v4.2): first-event outcome tracking columns ----
    + ["Outcome_1R", "Outcome_Time", "Target_1R_Price", "R_Distance",
       "Outcome_R_Multiple", "Outcome_Status"]
    # ---- NAYA (v4.2): MFE/MAE over the 120-min window ----
    + ["MFE_120", "MAE_120"]
)

# ============================================
# GOOGLE SHEETS CONNECTION
# ============================================
_client = None
_awaiting_ws = None
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


def _get_awaiting_worksheet():
    global _awaiting_ws
    if _awaiting_ws is not None:
        return _awaiting_ws
    client = _connect()
    spreadsheet = client.open_by_key(config.SHEET_ID)
    try:
        _awaiting_ws = spreadsheet.worksheet(AWAITING_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        _awaiting_ws = spreadsheet.add_worksheet(
            title=AWAITING_WORKSHEET_NAME, rows=1000, cols=len(AWAITING_HEADER) + 2
        )
        _awaiting_ws.append_row(AWAITING_HEADER, table_range="A1")
    return _awaiting_ws


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
            title=PENDING_WORKSHEET_NAME, rows=1000, cols=len(PENDING_HEADER) + 2
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
# NAYA (v4.2): ONE-TIME HEADER MIGRATION HELPER
# ============================================
def _migrate_results_header(dry_run=False):
    """
    One-time helper — existing Confusion_Backtest_Data sheet ka
    header row (Row 1) ko current RESULTS_HEADER (jisme ab naye 8
    first-event/MFE-MAE columns hain) se match karta hai.

    DATA ROWS KO TOUCH NAHI KARTA — sirf Row 1 update hota hai.
    Purani rows ke naye columns automatically khali (blank) reh
    jaayenge, jo expected hai (unke liye first-event outcome kabhi
    calculate nahi hua tha, aur dobara calculate bhi nahi hoga —
    resolve_pending() sirf Confusion_Pending ki active rows process
    karta hai).

    Chalane ka tarika (ek baar, manually):
        python -c "import sr_shape_outcome_tracker as t; t._migrate_results_header()"
    """
    ws = _get_results_worksheet()
    current_header = ws.row_values(1)
    if current_header == RESULTS_HEADER:
        print("  [sr_shape_tracker] Header already up-to-date — kuch karne ki zaroorat nahi.")
        return
    if dry_run:
        print(f"  [sr_shape_tracker][DRY_RUN] Header ye update hota:\n{RESULTS_HEADER}")
        return
    ws.update("A1", [RESULTS_HEADER])
    print(f"  [sr_shape_tracker] Header migrated — ab {len(RESULTS_HEADER)} columns hain "
          f"(pehle {len(current_header)} the).")


# ============================================
# HELPERS
# ============================================
def _to_utc_dt(value):
    dt = pd.to_datetime(value)
    if dt.tzinfo is None:
        dt = dt.tz_localize("UTC")
    return dt.to_pydatetime()


def _to_ist_str(value):
    dt = pd.to_datetime(value)
    if dt.tzinfo is None:
        dt = dt.tz_localize("UTC")
    ist_dt = dt + pd.Timedelta(hours=5, minutes=30)
    return ist_dt.strftime("%Y-%m-%d %H:%M:%S") + " IST"


def _find_closest_candle(df, target_time, tolerance_minutes=MATCH_TOLERANCE_MINUTES):
    df_times = df["Time"]
    if df_times.dt.tz is None:
        df_times = df_times.dt.tz_localize("UTC")
    diffs = (df_times - target_time).abs()
    min_idx = diffs.idxmin()
    min_diff = diffs.loc[min_idx]
    if min_diff <= timedelta(minutes=tolerance_minutes):
        return True, float(df.loc[min_idx, "Close"])
    return False, None


def _find_next_candle(df, candle_time, resolution_minutes=RESOLUTION_MINUTES,
                       tolerance_minutes=MATCH_TOLERANCE_MINUTES):
    """
    STRICT "immediately next candle only" lookup (v4.1 fix).

    Primary path: find candle_time's row by EXACT match in df, then
    take the very next row by dataframe position (idx + 1). This is
    the true "immediately next candle" — it can never accidentally
    skip ahead by more than one candle, even if there are timestamp
    gaps or duplicate rows elsewhere in df.

    Fallback path: if candle_time itself isn't present in df (e.g. it
    scrolled out of the fetch window before we got to resolve it), we
    fall back to a tolerance-based time match — but still capped so it
    can only match a candle at approximately one resolution-step
    ahead, never further into the future.

    Return: (found: bool, row: pandas.Series or None)
    """
    df_times = df["Time"]
    if df_times.dt.tz is None:
        df_times = df_times.dt.tz_localize("UTC")

    exact_mask = df_times == candle_time
    if exact_mask.any():
        exact_idx = df.index[exact_mask][0]
        pos = df.index.get_loc(exact_idx)
        if pos + 1 < len(df):
            return True, df.iloc[pos + 1]
        # candle_time is the last candle we currently have — the
        # immediately-next candle hasn't printed yet, keep waiting.
        return False, None

    # Fallback: candle_time not found exactly in this df fetch.
    # Still only look for a candle at ~one resolution-step ahead.
    target_time = candle_time + timedelta(minutes=resolution_minutes)
    diffs = (df_times - target_time).abs()
    within_tolerance_mask = diffs <= timedelta(minutes=tolerance_minutes)
    after_signal_mask = (df_times > candle_time) & (df_times <= target_time + timedelta(minutes=tolerance_minutes))
    candidate_mask = within_tolerance_mask & after_signal_mask
    if not candidate_mask.any():
        return False, None
    candidate_diffs = diffs[candidate_mask]
    best_idx = candidate_diffs.idxmin()
    return True, df.loc[best_idx]


def _is_pair_already_active(records, pair, status_field="Status", active_values=("PENDING", "AWAITING_CONFIRMATION")):
    for r in records:
        if r.get("Pair") == pair and str(r.get(status_field, "")).upper() in active_values:
            return True
    return False


def _is_valid_pending_row(row):
    """
    v4.1 defensive gate: a row in Confusion_Pending is only eligible
    for outcome tracking if it is genuinely a CONFIRMED (LONG/SHORT)
    setup. This protects resolve_pending() against ever processing a
    NO_CONFIRMATION / awaiting / malformed row — even if one somehow
    ends up in this sheet (manual edit, partial write, future bug
    elsewhere). NO_CONFIRMATION setups must never be treated as
    "active pending."
    """
    status_ok = str(row.get("Status", "")).upper() == "PENDING"
    direction_ok = str(row.get("Break_Direction", "")).upper() in ("LONG", "SHORT")
    return status_ok and direction_ok


# ============================================
# RVOL CALCULATION (intraday_spike_monitor.py jaisa hi logic)
# ============================================
def _get_rvol_for_last_candle(df, lookback_periods=(LOOKBACK_SHORT, LOOKBACK_LONG)):
    """
    intraday_spike_monitor.py ke get_intraday_rvol() jaisa hi formula —
    (current volume) / (pichli N candles ka rolling average volume,
    shift(1) taaki current candle khud apne baseline mein shamil na ho).
    Sirf last (abhi-abhi close hui) candle ke liye value chahiye.
    Return: dict {period: rvol_value_or_None}
    """
    result = {}
    work_df = df.copy()
    for period in lookback_periods:
        avg_col = f"_avg_vol_{period}"
        work_df[avg_col] = work_df["Volume"].rolling(window=period).mean().shift(1)
        rvol_series = work_df["Volume"] / work_df[avg_col]
        last_val = rvol_series.iloc[-1]
        result[period] = None if pd.isna(last_val) else round(float(last_val), 2)
    return result


def _rvol_gate_passed(rvol_20, rvol_96):
    """
    Volume gate (v4, relaxed): RVOL_20 >= RVOL_SHORT_THRESHOLD
    YA RVOL_96 >= RVOL_LONG_THRESHOLD (OR condition, dono mandatory nahi).
    Agar dono None hain (insufficient history) to gate FAIL maana jayega.
    """
    cond_short = (rvol_20 is not None) and (rvol_20 >= RVOL_SHORT_THRESHOLD)
    cond_long = (rvol_96 is not None) and (rvol_96 >= RVOL_LONG_THRESHOLD)
    return cond_short or cond_long


# ============================================
# STAGE 1: CONFUSION candle detect karna + AWAITING_CONFIRMATION mein daalna
# (UNCHANGED)
# ============================================
def process_candle(pair, df, dry_run=False):
    """
    Flowchart Stage 1 implement karta hai ek pair ke liye:
        15-min candle -> valid S/R ke paas? -> shape classify ->
        SIRF CONFUSION -> RVOL gate pass? -> High/Low record karke
        AWAITING_CONFIRMATION mein daal do.

    DIRECTION YAHAN DECIDE NAHI HOTI (v4 change) — woh
    resolve_confirmations() IMMEDIATELY NEXT candle dekhkar karega,
    aur sirf usi ek candle ka wait hota hai (v4.1).

    df: candle dataframe (kam se kam SR_LOOKBACK+ candles chahiye,
        columns: Time/Open/High/Low/Close/Volume)

    Return: True agar candle qualify hoke awaiting-confirmation mein
            add hui, False agar skip hui.
    """
    if df is None or df.empty or len(df) < SR_LOOKBACK + 2:
        return False

    last_row = df.iloc[-1]
    prev_close = float(df.iloc[-2]["Close"])
    candle_open = float(last_row["Open"])
    candle_high = float(last_row["High"])
    candle_low = float(last_row["Low"])
    close = float(last_row["Close"])
    candle_time = last_row["Time"]
    candle_color = "GREEN" if close >= candle_open else "RED"

    # ---- Valid Support/Resistance ke paas hai? ----
    try:
        resistance_raw, support_raw = get_support_resistance(
            df, lookback=SR_LOOKBACK, cluster_tolerance_pct=SR_CLUSTER_TOLERANCE_PCT
        )
        resistance_levels = [r for r in resistance_raw if r[1] >= MIN_SR_TOUCHES]
        support_levels = [s for s in support_raw if s[1] >= MIN_SR_TOUCHES]
        sr_result = classify_price_position(
            close, prev_close, resistance_levels, support_levels,
            proximity_pct=SR_PROXIMITY_PCT,
        )
    except Exception as e:
        print(f"  [sr_shape_tracker] {pair} S/R error: {e}")
        return False

    price_position = sr_result["label"]
    sr_level_price = sr_result["level_price"]
    sr_touch_count = sr_result["touch_count"]

    if price_position == "MID_RANGE" or sr_level_price is None:
        return False

    # ---- Candle shape classify karo ----
    try:
        shape_ctx = classify_candle_shape(candle_open, candle_high, candle_low, close)
    except Exception as e:
        print(f"  [sr_shape_tracker] {pair} shape error: {e}")
        return False

    # ---- CONFUSION-ONLY FILTER ----
    if shape_ctx["shape"] != "CONFUSION":
        return False

    # ---- RVOL nikaalo ----
    rvol_vals = _get_rvol_for_last_candle(df, lookback_periods=(LOOKBACK_SHORT, LOOKBACK_LONG))
    rvol_20 = rvol_vals[LOOKBACK_SHORT]
    rvol_96 = rvol_vals[LOOKBACK_LONG]

    # ---- VOLUME GATE (relaxed v4) ----
    if not _rvol_gate_passed(rvol_20, rvol_96):
        print(f"  [sr_shape_tracker] {pair} S/R+CONFUSION mila lekin RVOL gate FAIL "
              f"(RVOL_20={rvol_20}, RVOL_96={rvol_96}, need >= {RVOL_SHORT_THRESHOLD} "
              f"ya >= {RVOL_LONG_THRESHOLD}) — skip.")
        return False

    # ---- COOLDOWN: same pair already awaiting-confirmation ya pending ho to skip ----
    awaiting_ws = _get_awaiting_worksheet()
    existing_awaiting = awaiting_ws.get_all_records()
    if _is_pair_already_active(existing_awaiting, pair, active_values=("AWAITING_CONFIRMATION",)):
        print(f"  [sr_shape_tracker] {pair} already awaiting confirmation — skip.")
        return False

    pending_ws = _get_pending_worksheet()
    existing_pending = pending_ws.get_all_records()
    if _is_pair_already_active(existing_pending, pair, active_values=("PENDING",)):
        print(f"  [sr_shape_tracker] {pair} already pending (outcome tracking) — skip.")
        return False

    # ---- AWAITING_CONFIRMATION mein add karo (direction abhi nahi) ----
    if dry_run:
        print(f"  [sr_shape_tracker][DRY_RUN] {pair} awaiting-confirmation add: "
              f"pos={price_position} shape={shape_ctx['shape']} "
              f"level={sr_level_price} touches={sr_touch_count} "
              f"RVOL_20={rvol_20} RVOL_96={rvol_96} "
              f"CONFUSION High={candle_high} Low={candle_low}")
    else:
        awaiting_ws.append_row([
            pair,
            str(_to_utc_dt(candle_time)),
            candle_color,
            close,
            rvol_20 if rvol_20 is not None else "",
            rvol_96 if rvol_96 is not None else "",
            price_position,
            sr_level_price,
            sr_touch_count,
            shape_ctx["shape"],
            shape_ctx["strength"],
            shape_ctx["body_pct"],
            shape_ctx.get("rejection_side") or "",
            candle_high,
            candle_low,
            "AWAITING_CONFIRMATION",
        ], table_range="A1")

    print(f"  [sr_shape_tracker] {pair} qualified for confirmation: {price_position} "
          f"(touched {sr_touch_count}x) | Shape=CONFUSION "
          f"({shape_ctx['strength']}, body={shape_ctx['body_pct']}%) | "
          f"RVOL_20={rvol_20} RVOL_96={rvol_96} (gate PASSED) | "
          f"High={candle_high} Low={candle_low} — waiting for immediately-next candle.")

    # ---- CONFUSION bot ko Telegram alert (awaiting confirmation) ----
    awaiting_msg = _build_awaiting_message(
        pair, candle_time, price_position, sr_level_price, sr_touch_count,
        shape_ctx, candle_high, candle_low, rvol_20=rvol_20, rvol_96=rvol_96,
    )
    if dry_run:
        print(f"  [sr_shape_tracker][DRY_RUN] Confusion Telegram (awaiting):\n{awaiting_msg}\n")
    else:
        try:
            send_confusion_telegram_message(awaiting_msg)
        except Exception as e:
            print(f"  [sr_shape_tracker] Confusion Telegram (awaiting) error: {e}")

    return True


# ============================================
# STAGE 2: IMMEDIATELY NEXT CANDLE se breakout direction CONFIRM karna
# (UNCHANGED)
# ============================================
def resolve_confirmations(dry_run=False):
    """
    Confusion_Awaiting_Confirmation sheet mein har AWAITING_CONFIRMATION
    row ke liye, us candle ke IMMEDIATELY BAAD wali (aur sirf usi) candle
    dhoondhta hai:
        Next candle High > Confusion High         -> LONG
        Next candle Low  < Confusion Low          -> SHORT
        Dono break (ambiguous) ya koi nahi break  -> NO_CONFIRMATION

    LONG/SHORT confirm hone par Confusion_Pending mein daal deta hai
    (outcome-tracking ke liye).

    NO_CONFIRMATION seedha Confusion_Backtest_Data mein blank-outcome
    row ke saath record ho jaata hai (taaki pata chale kitni baar
    confirmation nahi milta), aur us candle ko is function ke baad
    kabhi dobara process NAHI kiya jaata.
    """
    awaiting_ws = _get_awaiting_worksheet()
    records = awaiting_ws.get_all_records()
    if not records:
        print("  [sr_shape_tracker] Koi awaiting-confirmation candle nahi hai.")
        return

    now_utc = datetime.now(timezone.utc)
    pairs_needed = {r["Pair"] for r in records if str(r.get("Status", "")).upper() == "AWAITING_CONFIRMATION"}

    candle_cache = {}
    for pair in pairs_needed:
        try:
            candle_cache[pair] = get_candles(pair=pair, resolution=RESOLUTION, days=2)
        except Exception as e:
            print(f"  [sr_shape_tracker] {pair} candles fetch error: {e}")
            candle_cache[pair] = None

    still_awaiting = []
    confirmed_count = 0
    no_confirmation_count = 0
    discarded_count = 0

    for row in records:
        if str(row.get("Status", "")).upper() != "AWAITING_CONFIRMATION":
            continue

        pair = row["Pair"]
        candle_time = _to_utc_dt(row["Candle_Time_UTC"])
        confusion_high = float(row["Candle_High"])
        confusion_low = float(row["Candle_Low"])
        close = float(row["Close"])
        rvol_20_raw = row.get("RVOL_20", "")
        rvol_96_raw = row.get("RVOL_96", "")
        rvol_20 = float(rvol_20_raw) if rvol_20_raw not in ("", None) else None
        rvol_96 = float(rvol_96_raw) if rvol_96_raw not in ("", None) else None

        if now_utc - candle_time > timedelta(hours=MAX_AGE_HOURS):
            print(f"  [sr_shape_tracker] {pair} @ {row['Candle_Time_UTC']} stale (awaiting), discard.")
            discarded_count += 1
            continue

        df = candle_cache.get(pair)
        if df is None or df.empty:
            still_awaiting.append(row)
            continue

        found_next, next_row = _find_next_candle(df, candle_time)
        if not found_next:
            still_awaiting.append(row)
            continue

        next_candle_time = next_row["Time"]
        next_high = float(next_row["High"])
        next_low = float(next_row["Low"])

        broke_high = next_high > confusion_high
        broke_low = next_low < confusion_low

        if broke_high and broke_low:
            break_direction = "NO_CONFIRMATION"
        elif broke_high:
            break_direction = "LONG"
        elif broke_low:
            break_direction = "SHORT"
        else:
            break_direction = "NO_CONFIRMATION"

        common_fields = dict(
            pair=pair,
            candle_time_str=row["Candle_Time_UTC"],
            candle_color=row["Candle_Color"],
            close=close,
            rvol_20=rvol_20,
            rvol_96=rvol_96,
            price_position=row.get("Price_Position", "UNKNOWN"),
            sr_level_price=row.get("SR_Level_Price", ""),
            sr_touch_count=row.get("SR_Touch_Count", 0),
            candle_shape=row.get("Candle_Shape", "UNKNOWN"),
            shape_strength=row.get("Shape_Strength", ""),
            body_pct=row.get("Body_Pct", ""),
            rejection_side=row.get("Rejection_Side", ""),
            confusion_high=confusion_high,
            confusion_low=confusion_low,
            confusion_close=close,
            next_candle_time_str=str(_to_utc_dt(next_candle_time)),
            next_high=next_high,
            next_low=next_low,
        )

        if break_direction == "NO_CONFIRMATION":
            no_confirmation_count += 1
            result_row = (
                [common_fields["pair"], common_fields["candle_time_str"], common_fields["candle_color"],
                 common_fields["close"], common_fields["rvol_20"] if common_fields["rvol_20"] is not None else "",
                 common_fields["rvol_96"] if common_fields["rvol_96"] is not None else "",
                 common_fields["price_position"], common_fields["sr_level_price"], common_fields["sr_touch_count"],
                 common_fields["candle_shape"], common_fields["shape_strength"], common_fields["body_pct"],
                 common_fields["rejection_side"], common_fields["confusion_high"], common_fields["confusion_low"],
                 common_fields["confusion_close"], common_fields["next_candle_time_str"],
                 common_fields["next_high"], common_fields["next_low"],
                 "NO_CONFIRMATION", "", "", ""]
                + [""] * len(HORIZONS_MINUTES)   # Price_After_*
                + [""] * len(HORIZONS_MINUTES)   # PctChg_*
                + ["", ""]                        # Max favorable/adverse
                + [""] * len(HORIZONS_MINUTES)   # SL_Hit_*
                + [""] * len(TARGET_RR)          # Target_*_Hit
                # ---- NAYA: first-event + MFE/MAE columns bhi blank ----
                + ["", "", "", "", "", ""]        # Outcome_1R..Outcome_Status
                + ["", ""]                        # MFE_120, MAE_120
            )
            if dry_run:
                print(f"  [sr_shape_tracker][DRY_RUN] NO_CONFIRMATION (CLOSED): {pair} @ {row['Candle_Time_UTC']}")
            else:
                try:
                    _get_results_worksheet().append_row(result_row, table_range="A1")
                except Exception as e:
                    print(f"  [sr_shape_tracker] NO_CONFIRMATION result likhne mein error: {e}")
                    still_awaiting.append(row)
                    continue
                try:
                    no_conf_msg = _build_no_confirmation_message(
                        pair, candle_time, confusion_high, confusion_low,
                        next_candle_time, next_high, next_low,
                    )
                    send_confusion_telegram_message(no_conf_msg)
                except Exception as e:
                    print(f"  [sr_shape_tracker] Confusion Telegram (no-confirmation) error: {e}")
            continue

        # ---- LONG / SHORT confirmed -> Entry/SL nikaalo ----
        if break_direction == "LONG":
            entry_price = confusion_high
            stop_loss = confusion_low
            sl_distance_pct = round((entry_price - stop_loss) / entry_price * 100, 3)
        else:  # SHORT
            entry_price = confusion_low
            stop_loss = confusion_high
            sl_distance_pct = round((stop_loss - entry_price) / entry_price * 100, 3)

        if sl_distance_pct <= 0:
            discarded_count += 1
            continue

        confirmed_count += 1

        if dry_run:
            print(f"  [sr_shape_tracker][DRY_RUN] {pair} CONFIRMED {break_direction}: "
                  f"entry={entry_price} sl={stop_loss} ({sl_distance_pct}%) "
                  f"next_candle_time={next_candle_time}")
        else:
            pending_ws = _get_pending_worksheet()
            pending_ws.append_row([
                pair,
                row["Candle_Time_UTC"],
                row["Candle_Color"],
                close,
                rvol_20 if rvol_20 is not None else "",
                rvol_96 if rvol_96 is not None else "",
                row.get("Price_Position", "UNKNOWN"),
                row.get("SR_Level_Price", ""),
                row.get("SR_Touch_Count", 0),
                row.get("Candle_Shape", "UNKNOWN"),
                row.get("Shape_Strength", ""),
                row.get("Body_Pct", ""),
                row.get("Rejection_Side", ""),
                confusion_high,
                confusion_low,
                close,
                str(_to_utc_dt(next_candle_time)),
                next_high,
                next_low,
                break_direction,
                entry_price,
                stop_loss,
                sl_distance_pct,
                "PENDING",
            ], table_range="A1")
            try:
                confirm_msg = _build_confirmation_message(
                    pair, candle_time, break_direction, confusion_high, confusion_low,
                    next_candle_time, next_high, next_low, entry_price, stop_loss,
                    sl_distance_pct, rvol_20=rvol_20, rvol_96=rvol_96,
                )
                send_confusion_telegram_message(confirm_msg)
            except Exception as e:
                print(f"  [sr_shape_tracker] Confusion Telegram (confirmation) error: {e}")

        print(f"  [sr_shape_tracker] {pair} breakout CONFIRMED: {break_direction} "
              f"entry={entry_price} SL={stop_loss} ({sl_distance_pct}% away)")

    if not dry_run:
        try:
            clean_rows = [[
                r["Pair"], r["Candle_Time_UTC"], r["Candle_Color"], r["Close"],
                r.get("RVOL_20", ""),
                r.get("RVOL_96", ""),
                r.get("Price_Position", "UNKNOWN"),
                r.get("SR_Level_Price", ""),
                r.get("SR_Touch_Count", 0),
                r.get("Candle_Shape", "UNKNOWN"),
                r.get("Shape_Strength", ""),
                r.get("Body_Pct", ""),
                r.get("Rejection_Side", ""),
                r.get("Candle_High", ""),
                r.get("Candle_Low", ""),
                "AWAITING_CONFIRMATION",
            ] for r in still_awaiting]
            awaiting_ws.clear()
            awaiting_ws.update([AWAITING_HEADER] + clean_rows)
        except Exception as e:
            print(f"  [sr_shape_tracker] Awaiting sheet update error: {e}")
    else:
        print(f"  [sr_shape_tracker][DRY_RUN] Still awaiting: {len(still_awaiting)}")

    print(f"  [sr_shape_tracker] Confirmations: LONG/SHORT={confirmed_count} | "
          f"NO_CONFIRMATION(closed)={no_confirmation_count} | "
          f"Still awaiting (immediately-next candle not yet printed): {len(still_awaiting)} | "
          f"Discarded (stale/degenerate): {discarded_count}")


# ============================================
# TELEGRAM MESSAGE BUILDERS (Confusion bot) — UNCHANGED
# ============================================
def _build_awaiting_message(pair, candle_time, price_position, sr_level_price,
                             sr_touch_count, shape_ctx, candle_high, candle_low,
                             rvol_20=None, rvol_96=None):
    rvol_line = (
        f"<b>RVOL_20:</b> {rvol_20 if rvol_20 is not None else 'N/A'}x | "
        f"<b>RVOL_96:</b> {rvol_96 if rvol_96 is not None else 'N/A'}x\n"
    )
    return (
        f"🌀 <b>CONFUSION CANDLE — AWAITING CONFIRMATION</b>\n\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Candle Time (IST):</b> {_to_ist_str(candle_time)}\n"
        f"{rvol_line}"
        f"<b>Price Position:</b> {price_position} (Level: {sr_level_price}, tested {sr_touch_count}x pehle)\n"
        f"<b>Candle Shape:</b> CONFUSION ({shape_ctx['strength']}, body={shape_ctx['body_pct']}%)\n"
        f"<b>Confusion High:</b> {candle_high} | <b>Confusion Low:</b> {candle_low}\n\n"
        f"Immediately next candle CONFUSION HIGH todhe -> LONG watch | LOW todhe -> SHORT watch. "
        f"Confirm na ho to setup close ho jaata hai. Ye sirf data-collection ke liye hai, trade instruction nahi."
    )


def _build_confirmation_message(pair, candle_time, break_direction, confusion_high,
                                 confusion_low, next_candle_time, next_high, next_low,
                                 entry_price, stop_loss, sl_distance_pct,
                                 rvol_20=None, rvol_96=None):
    direction_emoji = "🟢" if break_direction == "LONG" else "🔴"
    rvol_line = (
        f"<b>RVOL_20:</b> {rvol_20 if rvol_20 is not None else 'N/A'}x | "
        f"<b>RVOL_96:</b> {rvol_96 if rvol_96 is not None else 'N/A'}x\n"
    )
    return (
        f"{direction_emoji} <b>BREAKOUT CONFIRMED — {break_direction}</b>\n\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Confusion Candle (IST):</b> {_to_ist_str(candle_time)}\n"
        f"<b>Confusion High/Low:</b> {confusion_high} / {confusion_low}\n"
        f"<b>Confirming Candle (IST):</b> {_to_ist_str(next_candle_time)}\n"
        f"<b>Confirming Candle High/Low:</b> {next_high} / {next_low}\n"
        f"{rvol_line}"
        f"<b>Entry:</b> {entry_price}\n"
        f"<b>Stop Loss:</b> {stop_loss} ({sl_distance_pct}% away)\n\n"
        f"Ye sirf data-collection/backtest ke liye hai, trade instruction nahi — "
        f"khud verify karke decide karo."
    )


def _build_no_confirmation_message(pair, candle_time, confusion_high, confusion_low,
                                    next_candle_time, next_high, next_low):
    return (
        f"⚪ <b>NO CONFIRMATION — SETUP CLOSED</b>\n\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Confusion Candle (IST):</b> {_to_ist_str(candle_time)}\n"
        f"<b>Confusion High/Low:</b> {confusion_high} / {confusion_low}\n"
        f"<b>Immediately Next Candle (IST):</b> {_to_ist_str(next_candle_time)}\n"
        f"<b>Next Candle High/Low:</b> {next_high} / {next_low}\n\n"
        f"Breakout confirm nahi hua — setup permanently close, record kiya gaya, "
        f"koi trade setup nahi, future candles mein dobara try nahi hoga."
    )


def _build_resolved_message(pair, candle_time, price_position, break_direction,
                             entry_price, stop_loss, outcome, pct_changes_by_horizon,
                             rvol_20=None, rvol_96=None):
    sl_final_hit = outcome["sl_hit_by"][max(HORIZONS_MINUTES)]
    result_emoji = "❌" if sl_final_hit else "✅"
    rvol_line = (
        f"<b>RVOL_20:</b> {rvol_20 if rvol_20 is not None else 'N/A'}x | "
        f"<b>RVOL_96:</b> {rvol_96 if rvol_96 is not None else 'N/A'}x\n"
    )
    targets_lines = "\n".join(
        f"  {_rr_label(rr)}: {'YES ✅' if outcome['target_hit'][rr] else 'NO'}"
        for rr in TARGET_RR
    )
    sl_lines = "\n".join(
        f"  {m}min: {'YES ❌' if outcome['sl_hit_by'][m] else 'NO'}"
        for m in HORIZONS_MINUTES
    )
    horizon_lines = "\n".join(
        f"  {m}min: {pct_changes_by_horizon.get(m, ''):+.2f}%" if pct_changes_by_horizon.get(m, "") != "" else f"  {m}min: N/A"
        for m in HORIZONS_MINUTES
    )
    return (
        f"{result_emoji} <b>CONFUSION OUTCOME RESOLVED</b>\n\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Candle Time (IST):</b> {_to_ist_str(candle_time)}\n"
        f"{rvol_line}"
        f"<b>Price Position:</b> {price_position}\n"
        f"<b>Break Direction:</b> {break_direction} | Entry: {entry_price} | SL: {stop_loss}\n\n"
        f"<b>Price % change by horizon:</b>\n{horizon_lines}\n\n"
        f"<b>SL Hit by horizon:</b>\n{sl_lines}\n\n"
        f"<b>Target Hit (R-multiples):</b>\n{targets_lines}\n\n"
        f"<b>Max Favorable:</b> {outcome['max_favorable_pct']}% | "
        f"<b>Max Adverse:</b> {outcome['max_adverse_pct']}%\n\n"
        f"(Sirf record/data — trade instruction nahi.)"
    )


# ============================================
# SL / TARGET / MAX-MOVE ANALYSIS (UNCHANGED — SL-first convention)
# ============================================
def _analyze_trade_outcome(df, anchor_time, break_direction, entry_price, stop_loss):
    """
    Confirmation-candle (anchor) ke baad, 120-min window ke andar
    (saari actual candles use karke) SL aur target (TARGET_RR
    multiples) HIGH/LOW se detect karta hai, plus max favorable/adverse
    move.

    Return: dict with keys "sl_hit_by", "target_hit",
            "max_favorable_pct", "max_adverse_pct".
    """
    max_horizon_min = max(HORIZONS_MINUTES)
    work_df = df.copy()
    time_col = work_df["Time"]
    if time_col.dt.tz is None:
        time_col = time_col.dt.tz_localize("UTC")
    work_df["_t"] = time_col

    window_start = anchor_time
    window_end = anchor_time + timedelta(minutes=max_horizon_min)
    window_df = work_df[(work_df["_t"] > window_start) & (work_df["_t"] <= window_end)]
    window_df = window_df.sort_values("_t")

    sl_hit_by = {m: False for m in HORIZONS_MINUTES}
    target_hit = {rr: False for rr in TARGET_RR}

    if window_df.empty:
        return {
            "sl_hit_by": sl_hit_by,
            "target_hit": target_hit,
            "max_favorable_pct": None,
            "max_adverse_pct": None,
        }

    is_long = (break_direction == "LONG")
    if is_long:
        risk = entry_price - stop_loss
    else:
        risk = stop_loss - entry_price

    targets = {rr: (entry_price + rr * risk) if is_long else (entry_price - rr * risk)
               for rr in TARGET_RR}

    sl_hit_flag = False
    sl_hit_time = None
    best_favorable_extreme = entry_price
    worst_adverse_extreme = entry_price

    for _, cand_row in window_df.iterrows():
        high = float(cand_row["High"])
        low = float(cand_row["Low"])
        cand_time = cand_row["_t"]

        if is_long:
            best_favorable_extreme = max(best_favorable_extreme, high)
            worst_adverse_extreme = min(worst_adverse_extreme, low)
        else:
            best_favorable_extreme = min(best_favorable_extreme, low)
            worst_adverse_extreme = max(worst_adverse_extreme, high)

        sl_breached_this_candle = (low <= stop_loss) if is_long else (high >= stop_loss)

        targets_breached_this_candle = set()
        for rr, target_price in targets.items():
            if target_hit[rr]:
                continue
            reached = (high >= target_price) if is_long else (low <= target_price)
            if reached:
                targets_breached_this_candle.add(rr)

        if not sl_hit_flag:
            if sl_breached_this_candle:
                sl_hit_flag = True
                sl_hit_time = cand_time
            elif targets_breached_this_candle:
                for rr in targets_breached_this_candle:
                    target_hit[rr] = True

        if sl_hit_flag and sl_hit_time is not None:
            for m in HORIZONS_MINUTES:
                if sl_hit_time <= anchor_time + timedelta(minutes=m):
                    sl_hit_by[m] = True

    if is_long:
        max_favorable_pct = round((best_favorable_extreme - entry_price) / entry_price * 100, 3)
        max_adverse_pct = round((entry_price - worst_adverse_extreme) / entry_price * 100, 3)
    else:
        max_favorable_pct = round((entry_price - best_favorable_extreme) / entry_price * 100, 3)
        max_adverse_pct = round((worst_adverse_extreme - entry_price) / entry_price * 100, 3)

    return {
        "sl_hit_by": sl_hit_by,
        "target_hit": target_hit,
        "max_favorable_pct": max_favorable_pct,
        "max_adverse_pct": max_adverse_pct,
    }


# ============================================
# NAYA (v4.2): FIRST-EVENT OUTCOME TRACKING
# ============================================
def _pct_moves(entry_price, best_favorable_extreme, worst_adverse_extreme, is_long):
    """MFE/MAE % helper — direction-aware (LONG: up=favorable, SHORT: down=favorable)."""
    if is_long:
        mfe = round((best_favorable_extreme - entry_price) / entry_price * 100, 3)
        mae = round((entry_price - worst_adverse_extreme) / entry_price * 100, 3)
    else:
        mfe = round((entry_price - best_favorable_extreme) / entry_price * 100, 3)
        mae = round((worst_adverse_extreme - entry_price) / entry_price * 100, 3)
    return mfe, mae


def _compute_first_event_outcome(df, anchor_time, break_direction, entry_price, stop_loss,
                                  horizon_minutes=FIRST_EVENT_HORIZON_MINUTES):
    """
    NAYA (v4.2) — FIRST-EVENT outcome tracking.

    Existing _analyze_trade_outcome() ke per-horizon SL_Hit_*/
    Target_*_Hit booleans se INDEPENDENT hai (har horizon ko alag-alag
    check karta hai). Yeh function candle-by-candle chronologically
    walk karta hai aur PEHLA event (1R Target ya SL) jo bhi pehle aaye,
    usko record karta hai — sirf ek single verdict:
    WIN_1R / LOSS_SL / TIMEOUT / AMBIGUOUS.

    NO LOOK-AHEAD: sirf anchor_time (confirmation-candle time) ke BAAD
    wali candles use hoti hain. Entry/SL/direction sab pehle se fixed
    hain (existing entry logic se) — yahan sirf outcome track ho raha
    hai, koi entry-time feature (RVOL/Price_Position/Touch/Body%/
    Candle_Color/Trend) yahan calculate/change nahi hoti.

    SAME-CANDLE RULE (explicit, jaanbujhkar existing
    _analyze_trade_outcome()'s SL-first convention se ALAG): agar ek
    hi future candle ke High/Low range mein Target aur SL dono aa
    jaayein, to intrabar order OHLC se pata nahi chal sakta — is case
    mein AMBIGUOUS record hota hai (koi SL-first assumption nahi).

    Return: dict with keys:
        outcome_1r, outcome_time, target_1r_price, r_distance,
        outcome_r_multiple, outcome_status, mfe_pct, mae_pct
    """
    work_df = df.copy()
    time_col = work_df["Time"]
    if time_col.dt.tz is None:
        time_col = time_col.dt.tz_localize("UTC")
    work_df["_t"] = time_col

    window_end = anchor_time + timedelta(minutes=horizon_minutes)
    window_df = work_df[(work_df["_t"] > anchor_time) & (work_df["_t"] <= window_end)]
    window_df = window_df.sort_values("_t")

    is_long = (break_direction == "LONG")
    r_distance = abs(entry_price - stop_loss)
    target_1r_price = entry_price + r_distance if is_long else entry_price - r_distance

    blank_result = {
        "outcome_1r": "TIMEOUT",
        "outcome_time": "",
        "target_1r_price": round(target_1r_price, 8) if r_distance > 0 else "",
        "r_distance": round(r_distance, 8) if r_distance > 0 else "",
        "outcome_r_multiple": "",
        "outcome_status": "TIMEOUT",
        "mfe_pct": "",
        "mae_pct": "",
    }

    if r_distance <= 0 or window_df.empty:
        return blank_result

    best_favorable_extreme = entry_price   # running MFE_120 tracker
    worst_adverse_extreme = entry_price    # running MAE_120 tracker

    for _, cand in window_df.iterrows():
        high = float(cand["High"])
        low = float(cand["Low"])
        cand_time = cand["_t"]

        # Running MFE/MAE — independent of first-event, tracked every candle in window.
        if is_long:
            best_favorable_extreme = max(best_favorable_extreme, high)
            worst_adverse_extreme = min(worst_adverse_extreme, low)
        else:
            best_favorable_extreme = min(best_favorable_extreme, low)
            worst_adverse_extreme = max(worst_adverse_extreme, high)

        target_reached = (high >= target_1r_price) if is_long else (low <= target_1r_price)
        sl_reached = (low <= stop_loss) if is_long else (high >= stop_loss)

        if target_reached and sl_reached:
            mfe_pct, mae_pct = _pct_moves(entry_price, best_favorable_extreme, worst_adverse_extreme, is_long)
            return {
                "outcome_1r": "AMBIGUOUS",
                "outcome_time": str(cand_time),
                "target_1r_price": round(target_1r_price, 8),
                "r_distance": round(r_distance, 8),
                "outcome_r_multiple": "",
                "outcome_status": "AMBIGUOUS",
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
            }
        if target_reached:
            mfe_pct, mae_pct = _pct_moves(entry_price, best_favorable_extreme, worst_adverse_extreme, is_long)
            return {
                "outcome_1r": "WIN_1R",
                "outcome_time": str(cand_time),
                "target_1r_price": round(target_1r_price, 8),
                "r_distance": round(r_distance, 8),
                "outcome_r_multiple": 1,
                "outcome_status": "FIRST_TARGET",
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
            }
        if sl_reached:
            mfe_pct, mae_pct = _pct_moves(entry_price, best_favorable_extreme, worst_adverse_extreme, is_long)
            return {
                "outcome_1r": "LOSS_SL",
                "outcome_time": str(cand_time),
                "target_1r_price": round(target_1r_price, 8),
                "r_distance": round(r_distance, 8),
                "outcome_r_multiple": 0,
                "outcome_status": "FIRST_SL",
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
            }
        # neither touched this candle -> continue walking window

    # Window khatam ho gayi, na target na SL hit hua -> TIMEOUT
    mfe_pct, mae_pct = _pct_moves(entry_price, best_favorable_extreme, worst_adverse_extreme, is_long)
    return {
        "outcome_1r": "TIMEOUT",
        "outcome_time": "",
        "target_1r_price": round(target_1r_price, 8),
        "r_distance": round(r_distance, 8),
        "outcome_r_multiple": "",
        "outcome_status": "TIMEOUT",
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
    }


# ============================================
# STAGE 3: PENDING (confirmed) setups resolve karna
#          (future 15/30/45/60/90/120 min outcome nikaalna, anchor =
#           confirmation/next-candle ka time)
#
# v4.2: ab existing per-horizon outcome ke saath-saath naya
# first-event outcome (WIN_1R/LOSS_SL/TIMEOUT/AMBIGUOUS) + MFE_120/
# MAE_120 bhi calculate + append hote hain. Existing logic ka koi
# hissa modify nahi hua — sirf result_row ke end mein naya data
# append hua hai.
# ============================================
def resolve_pending(dry_run=False):
    pending_ws = _get_pending_worksheet()
    records = pending_ws.get_all_records()
    if not records:
        print("  [sr_shape_tracker] Koi pending (confirmed) candle nahi hai.")
        return

    now_utc = datetime.now(timezone.utc)
    max_horizon_candles = max(HORIZONS_CANDLES)

    invalid_rows = [
        r for r in records
        if str(r.get("Status", "")).upper() == "PENDING" and not _is_valid_pending_row(r)
    ]
    for r in invalid_rows:
        print(f"  [sr_shape_tracker] WARNING: {r.get('Pair')} @ {r.get('Candle_Time_UTC')} "
              f"found in Confusion_Pending with invalid Break_Direction="
              f"{r.get('Break_Direction')!r} — dropping, NOT tracking outcome for it.")

    pairs_needed = {r["Pair"] for r in records if _is_valid_pending_row(r)}

    candle_cache = {}
    for pair in pairs_needed:
        try:
            candle_cache[pair] = get_candles(pair=pair, resolution=RESOLUTION, days=2)
        except Exception as e:
            print(f"  [sr_shape_tracker] {pair} candles fetch error: {e}")
            candle_cache[pair] = None

    still_pending = []
    resolved_count = 0
    discarded_count = 0

    for row in records:
        if not _is_valid_pending_row(row):
            continue

        pair = row["Pair"]
        anchor_time = _to_utc_dt(row["Next_Candle_Time_UTC"])
        close = float(row["Close"])
        break_direction = row.get("Break_Direction", "")
        entry_price = float(row.get("Entry_Price", close) or close)
        stop_loss_raw = row.get("Stop_Loss", "")
        stop_loss = float(stop_loss_raw) if stop_loss_raw not in ("", None) else None
        rvol_20_raw = row.get("RVOL_20", "")
        rvol_96_raw = row.get("RVOL_96", "")
        rvol_20 = float(rvol_20_raw) if rvol_20_raw not in ("", None) else None
        rvol_96 = float(rvol_96_raw) if rvol_96_raw not in ("", None) else None

        if now_utc - anchor_time > timedelta(hours=MAX_AGE_HOURS):
            print(f"  [sr_shape_tracker] {pair} @ {row['Next_Candle_Time_UTC']} stale (pending), discard.")
            discarded_count += 1
            continue

        df = candle_cache.get(pair)
        if df is None or df.empty:
            still_pending.append(row)
            continue

        max_target_time = anchor_time + timedelta(minutes=RESOLUTION_MINUTES * max_horizon_candles)
        found_max, _ = _find_closest_candle(df, max_target_time)
        if not found_max:
            still_pending.append(row)
            continue

        result_row = [
            pair,
            row["Candle_Time_UTC"],
            row["Candle_Color"],
            close,
            rvol_20 if rvol_20 is not None else "",
            rvol_96 if rvol_96 is not None else "",
            row.get("Price_Position", "UNKNOWN"),
            row.get("SR_Level_Price", ""),
            row.get("SR_Touch_Count", 0),
            row.get("Candle_Shape", "UNKNOWN"),
            row.get("Shape_Strength", ""),
            row.get("Body_Pct", ""),
            row.get("Rejection_Side", ""),
            row.get("Confusion_High", ""),
            row.get("Confusion_Low", ""),
            row.get("Confusion_Close", ""),
            row.get("Next_Candle_Time_UTC", ""),
            row.get("Next_Candle_High", ""),
            row.get("Next_Candle_Low", ""),
            break_direction,
            entry_price,
            stop_loss if stop_loss is not None else "",
            row.get("SL_Distance_Pct", ""),
        ]

        # ---- 15/30/45/60/90/120 min Price/PctChg tracking (anchor se) — UNCHANGED ----
        prices_after = []
        pct_changes = []
        all_found = True
        for n_candles in HORIZONS_CANDLES:
            target_time = anchor_time + timedelta(minutes=RESOLUTION_MINUTES * n_candles)
            found, price = _find_closest_candle(df, target_time)
            if not found:
                all_found = False
                break
            pct_change = round((price - entry_price) / entry_price * 100, 3)
            prices_after.append(price)
            pct_changes.append(pct_change)

        if not all_found:
            still_pending.append(row)
            continue

        result_row += prices_after + pct_changes

        # ---- SL / Target / Max-move analysis (existing, SL-first convention) — UNCHANGED ----
        outcome = _analyze_trade_outcome(df, anchor_time, break_direction, entry_price, stop_loss)
        result_row.append(
            outcome["max_favorable_pct"] if outcome["max_favorable_pct"] is not None else ""
        )
        result_row.append(
            outcome["max_adverse_pct"] if outcome["max_adverse_pct"] is not None else ""
        )
        for m in HORIZONS_MINUTES:
            result_row.append("YES" if outcome["sl_hit_by"][m] else "NO")
        for rr in TARGET_RR:
            result_row.append("YES" if outcome["target_hit"][rr] else "NO")

        # ---- NAYA (v4.2): FIRST-EVENT outcome (independent of per-horizon booleans above) ----
        first_event = _compute_first_event_outcome(
            df, anchor_time, break_direction, entry_price, stop_loss,
            horizon_minutes=FIRST_EVENT_HORIZON_MINUTES,
        )
        result_row.append(first_event["outcome_1r"])
        result_row.append(first_event["outcome_time"])
        result_row.append(first_event["target_1r_price"])
        result_row.append(first_event["r_distance"])
        result_row.append(first_event["outcome_r_multiple"])
        result_row.append(first_event["outcome_status"])
        result_row.append(first_event["mfe_pct"])
        result_row.append(first_event["mae_pct"])

        pct_by_horizon = dict(zip(HORIZONS_MINUTES, pct_changes))
        resolved_msg = _build_resolved_message(
            pair, anchor_time, row.get("Price_Position", "UNKNOWN"),
            break_direction, entry_price, stop_loss, outcome, pct_by_horizon,
            rvol_20=rvol_20, rvol_96=rvol_96,
        )
        if dry_run:
            print(f"  [sr_shape_tracker][DRY_RUN] Confusion Telegram (resolved):\n{resolved_msg}\n")
        else:
            try:
                send_confusion_telegram_message(resolved_msg)
            except Exception as e:
                print(f"  [sr_shape_tracker] Confusion Telegram (resolved) error: {e}")

        if dry_run:
            print(f"  [sr_shape_tracker][DRY_RUN] RESOLVED: {result_row}")
            print(f"  [sr_shape_tracker][DRY_RUN]   First-event: {first_event['outcome_1r']} "
                  f"@ {first_event['outcome_time']} | MFE_120={first_event['mfe_pct']}% "
                  f"MAE_120={first_event['mae_pct']}%")
        else:
            try:
                _get_results_worksheet().append_row(result_row, table_range="A1")
            except Exception as e:
                print(f"  [sr_shape_tracker] Results likhne mein error: {e}")
                still_pending.append(row)
                continue

        resolved_count += 1

    if not dry_run:
        try:
            clean_rows = [[
                r["Pair"], r["Candle_Time_UTC"], r["Candle_Color"], r["Close"],
                r.get("RVOL_20", ""),
                r.get("RVOL_96", ""),
                r.get("Price_Position", "UNKNOWN"),
                r.get("SR_Level_Price", ""),
                r.get("SR_Touch_Count", 0),
                r.get("Candle_Shape", "UNKNOWN"),
                r.get("Shape_Strength", ""),
                r.get("Body_Pct", ""),
                r.get("Rejection_Side", ""),
                r.get("Confusion_High", ""),
                r.get("Confusion_Low", ""),
                r.get("Confusion_Close", ""),
                r.get("Next_Candle_Time_UTC", ""),
                r.get("Next_Candle_High", ""),
                r.get("Next_Candle_Low", ""),
                r.get("Break_Direction", ""),
                r.get("Entry_Price", ""),
                r.get("Stop_Loss", ""),
                r.get("SL_Distance_Pct", ""),
                "PENDING",
            ] for r in still_pending]
            pending_ws.clear()
            pending_ws.update([PENDING_HEADER] + clean_rows)
        except Exception as e:
            print(f"  [sr_shape_tracker] Pending sheet update error: {e}")
    else:
        print(f"  [sr_shape_tracker][DRY_RUN] Still pending: {len(still_pending)}")

    print(f"  [sr_shape_tracker] Resolved: {resolved_count} | "
          f"Still pending: {len(still_pending)} | "
          f"Discarded (stale): {discarded_count} | "
          f"Invalid rows dropped (never tracked): {len(invalid_rows)}")


# ============================================
# NAYA (v4.2): BACKTEST SUMMARY (read-only, standalone)
# ============================================
def _avg_field(rows, field):
    vals = []
    for r in rows:
        v = r.get(field, "")
        if v in ("", None):
            continue
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    return round(sum(vals) / len(vals), 3) if vals else None


def _print_stats_block(rows, label):
    """
    Ek row-set (already Outcome_1R-resolved rows) ke liye stats print
    karta hai: Total, WIN_1R, LOSS_SL, Win Rate, TIMEOUT, AMBIGUOUS,
    Avg R, Avg MFE, Avg MAE.

    Win Rate = WIN_1R / (WIN_1R + LOSS_SL) — TIMEOUT/AMBIGUOUS is
    denominator se explicitly EXCLUDE hote hain, sirf apna % alag
    dikhaya jaata hai.
    """
    total = len(rows)
    wins = sum(1 for r in rows if r.get("Outcome_1R") == "WIN_1R")
    losses = sum(1 for r in rows if r.get("Outcome_1R") == "LOSS_SL")
    timeouts = sum(1 for r in rows if r.get("Outcome_1R") == "TIMEOUT")
    ambiguous = sum(1 for r in rows if r.get("Outcome_1R") == "AMBIGUOUS")
    resolved_n = wins + losses
    win_rate = round(wins / resolved_n * 100, 2) if resolved_n else None
    timeout_pct = round(timeouts / total * 100, 2) if total else None
    ambiguous_pct = round(ambiguous / total * 100, 2) if total else None

    avg_mfe = _avg_field(rows, "MFE_120")
    avg_mae = _avg_field(rows, "MAE_120")
    avg_r_resolved = _avg_field(rows, "Outcome_R_Multiple")

    print(f"\n--- {label} ---")
    print(f"Total setups: {total} | Resolved (WIN+LOSS): {resolved_n}")
    print(f"WIN_1R: {wins} | LOSS_SL: {losses} | Win Rate: {win_rate}%")
    print(f"TIMEOUT: {timeouts} ({timeout_pct}%) | AMBIGUOUS: {ambiguous} ({ambiguous_pct}%)")
    print(f"Avg MFE_120: {avg_mfe}% | Avg MAE_120: {avg_mae}%")
    print(f"Avg R (resolved trades only, WIN=1/LOSS=0): {avg_r_resolved}")

    return {
        "total": total, "resolved": resolved_n, "wins": wins, "losses": losses,
        "win_rate": win_rate, "timeouts": timeouts, "timeout_pct": timeout_pct,
        "ambiguous": ambiguous, "ambiguous_pct": ambiguous_pct,
        "avg_mfe": avg_mfe, "avg_mae": avg_mae, "avg_r": avg_r_resolved,
    }


def _in_rvol_2_3_band(row):
    """RVOL_20 in [2.0, 3.0) — boundary confirm karo agar tumhara matlab dono-inclusive hai."""
    rvol_20 = row.get("RVOL_20", "")
    try:
        return rvol_20 != "" and 2.0 <= float(rvol_20) < 3.0
    except (TypeError, ValueError):
        return False


def _touch_2_3(row):
    try:
        return 2 <= int(row.get("SR_Touch_Count", 0)) <= 3
    except (TypeError, ValueError):
        return False


def _body_lt_20(row):
    body = row.get("Body_Pct", "")
    try:
        return body != "" and float(body) < 20
    except (TypeError, ValueError):
        return False


def _near_resistance(row):
    return row.get("Price_Position") == "NEAR_RESISTANCE"


def _near_support(row):
    return row.get("Price_Position") == "NEAR_SUPPORT"


def generate_backtest_summary(v_filters=True):
    """
    NAYA (v4.2) — READ-ONLY summary function.

    Confusion_Backtest_Data se saari resolved rows padhta hai (kisi
    bhi tracking-flow/pending-state ko touch nahi karta) aur print
    karta hai:
        1. Overall summary (saare confirmed LONG/SHORT setups)
        2. V1/V2/V3 candidate-strategy breakdown (agar v_filters=True):
             V1: RVOL20 2-3 + Touch 2-3 + Body <20% + NEAR_RESISTANCE
             V2: RVOL20 2-3 + Body <20% + NEAR_RESISTANCE
             V3: RVOL20 2-3 + Touch 2-3 + NEAR_RESISTANCE

    Chalane ka tarika:
        python -c "import sr_shape_outcome_tracker as t; t.generate_backtest_summary()"
    """
    ws = _get_results_worksheet()
    records = ws.get_all_records()
    resolved = [r for r in records if r.get("Outcome_1R") in ("WIN_1R", "LOSS_SL", "TIMEOUT", "AMBIGUOUS")]

    print("=" * 60)
    print("OVERALL SUMMARY (all confirmed LONG/SHORT setups with first-event outcome)")
    print("=" * 60)
    _print_stats_block(resolved, "ALL SETUPS")

    if not v_filters:
        return

    v1_rows = [r for r in resolved if _in_rvol_2_3_band(r) and _touch_2_3(r) and _body_lt_20(r) and _near_resistance(r)]
    v2_rows = [r for r in resolved if _in_rvol_2_3_band(r) and _body_lt_20(r) and _near_resistance(r)]
    v3_rows = [r for r in resolved if _in_rvol_2_3_band(r) and _touch_2_3(r) and _near_resistance(r)]

    print("\n" + "=" * 60)
    print("STRATEGY VARIANTS (base: RVOL20 2-3 + NEAR_RESISTANCE)")
    print("=" * 60)
    _print_stats_block(v1_rows, "V1: + Touch 2-3 + Body <20%")
    _print_stats_block(v2_rows, "V2: + Body <20% (no touch filter)")
    _print_stats_block(v3_rows, "V3: + Touch 2-3 (no body filter)")

    # ---- NAYA: V4/V5/V6 — same conditions, NEAR_SUPPORT ke against ----
    v4_rows = [r for r in resolved if _in_rvol_2_3_band(r) and _touch_2_3(r) and _body_lt_20(r) and _near_support(r)]
    v5_rows = [r for r in resolved if _in_rvol_2_3_band(r) and _body_lt_20(r) and _near_support(r)]
    v6_rows = [r for r in resolved if _in_rvol_2_3_band(r) and _touch_2_3(r) and _near_support(r)]

    print("\n" + "=" * 60)
    print("STRATEGY VARIANTS (base: RVOL20 2-3 + NEAR_SUPPORT)")
    print("=" * 60)
    _print_stats_block(v4_rows, "V4: + Touch 2-3 + Body <20%")
    _print_stats_block(v5_rows, "V5: + Body <20% (no touch filter)")
    _print_stats_block(v6_rows, "V6: + Touch 2-3 (no body filter)")


# ============================================
# NAYA (v4.3): SEPARATE STRATEGY-REPORT SHEET (V1/V2/V3)
# ============================================
# Yeh EK ALAG worksheet hai — existing Awaiting/Pending/Backtest_Data
# tabs se bilkul independent. Isko likhne/update karne se woh purana
# system (jo already chal raha hai: process_candle, resolve_confirmations,
# resolve_pending) BILKUL touch nahi hota — yeh function sirf
# Confusion_Backtest_Data ko READ karta hai, aur apna result is naye
# tab mein WRITE karta hai. Purana system waisa hi chalta rehta hai.
STRATEGY_REPORT_WORKSHEET_NAME = "Confusion_Strategy_Report"

STRATEGY_REPORT_STATS_FIELDS = [
    "Strategy", "Total_Setups", "Resolved_Setups", "WIN_1R", "LOSS_SL",
    "Win_Rate_Pct", "TIMEOUT", "TIMEOUT_Pct", "AMBIGUOUS", "AMBIGUOUS_Pct",
    "Avg_R", "Avg_MFE_120", "Avg_MAE_120",
]

# Raw-row section header — same fields jo Confusion_Backtest_Data mein
# hain (koi naya field nahi banaya, sirf ek "Strategy" tag column extra
# hai taaki pata chale kaunsi row V1/V2/V3 mein se kis mein aayi).
STRATEGY_REPORT_ROW_HEADER = ["Strategy"] + RESULTS_HEADER


def _get_strategy_report_worksheet():
    global _client
    client = _connect()
    spreadsheet = client.open_by_key(config.SHEET_ID)
    try:
        ws = spreadsheet.worksheet(STRATEGY_REPORT_WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(
            title=STRATEGY_REPORT_WORKSHEET_NAME, rows=3000,
            cols=max(len(STRATEGY_REPORT_STATS_FIELDS), len(STRATEGY_REPORT_ROW_HEADER)) + 2,
        )
    return ws


def _stats_row_for_sheet(label, stats):
    return [
        label, stats["total"], stats["resolved"], stats["wins"], stats["losses"],
        stats["win_rate"] if stats["win_rate"] is not None else "",
        stats["timeouts"], stats["timeout_pct"] if stats["timeout_pct"] is not None else "",
        stats["ambiguous"], stats["ambiguous_pct"] if stats["ambiguous_pct"] is not None else "",
        stats["avg_r"] if stats["avg_r"] is not None else "",
        stats["avg_mfe"] if stats["avg_mfe"] is not None else "",
        stats["avg_mae"] if stats["avg_mae"] is not None else "",
    ]


def generate_strategy_report_sheet(include_raw_rows=True, dry_run=False):
    """
    NAYA (v4.3) — Alag, dedicated Google Sheet tab banata/update karta
    hai (Confusion_Strategy_Report) jisme sirf V1/V2/V3 candidate
    strategies (jo tumne diye the) ka summary + matching raw rows
    jaate hain.

    V1: RVOL20 2-3 + Touch 2-3 + Body <20% + NEAR_RESISTANCE
    V2: RVOL20 2-3 + Body <20% + NEAR_RESISTANCE
    V3: RVOL20 2-3 + Touch 2-3 + NEAR_RESISTANCE
    V4: RVOL20 2-3 + Touch 2-3 + Body <20% + NEAR_SUPPORT   (NAYA)
    V5: RVOL20 2-3 + Body <20% + NEAR_SUPPORT               (NAYA)
    V6: RVOL20 2-3 + Touch 2-3 + NEAR_SUPPORT               (NAYA)

    IMPORTANT: Yeh function Confusion_Backtest_Data se sirf PADHTA hai
    (read-only) — existing tracking flow (process_candle /
    resolve_confirmations / resolve_pending) ka koi hissa touch nahi
    hota, wo purane jaisa hi chalta rehta hai. Yeh ek total ALAG,
    optional, on-demand report hai.

    Sheet ka structure (top se neeche):
        --- SUMMARY ---
        (header row)
        V1 stats row
        V2 stats row
        V3 stats row
        (blank row)
        --- MATCHING SETUPS (RAW ROWS) --- [agar include_raw_rows=True]
        (header row)
        ... V1 ki saari matching rows (Strategy=V1 tag ke saath) ...
        ... V2 ki saari matching rows (Strategy=V2 tag ke saath) ...
        ... V3 ki saari matching rows (Strategy=V3 tag ke saath) ...

    Chalane ka tarika (jab bhi chaho, standalone — cron/scan se alag):
        python -c "import sr_shape_outcome_tracker as t; t.generate_strategy_report_sheet()"
    """
    results_ws = _get_results_worksheet()
    records = results_ws.get_all_records()
    resolved = [r for r in records if r.get("Outcome_1R") in ("WIN_1R", "LOSS_SL", "TIMEOUT", "AMBIGUOUS")]

    v1_rows = [r for r in resolved if _in_rvol_2_3_band(r) and _touch_2_3(r) and _body_lt_20(r) and _near_resistance(r)]
    v2_rows = [r for r in resolved if _in_rvol_2_3_band(r) and _body_lt_20(r) and _near_resistance(r)]
    v3_rows = [r for r in resolved if _in_rvol_2_3_band(r) and _touch_2_3(r) and _near_resistance(r)]
    # ---- NAYA: V4/V5/V6 — same conditions, NEAR_SUPPORT ke against ----
    v4_rows = [r for r in resolved if _in_rvol_2_3_band(r) and _touch_2_3(r) and _body_lt_20(r) and _near_support(r)]
    v5_rows = [r for r in resolved if _in_rvol_2_3_band(r) and _body_lt_20(r) and _near_support(r)]
    v6_rows = [r for r in resolved if _in_rvol_2_3_band(r) and _touch_2_3(r) and _near_support(r)]

    v1_stats = _print_stats_block(v1_rows, "V1: + Touch 2-3 + Body <20% (NEAR_RESISTANCE)")
    v2_stats = _print_stats_block(v2_rows, "V2: + Body <20% (NEAR_RESISTANCE)")
    v3_stats = _print_stats_block(v3_rows, "V3: + Touch 2-3 (NEAR_RESISTANCE)")
    v4_stats = _print_stats_block(v4_rows, "V4: + Touch 2-3 + Body <20% (NEAR_SUPPORT)")
    v5_stats = _print_stats_block(v5_rows, "V5: + Body <20% (NEAR_SUPPORT)")
    v6_stats = _print_stats_block(v6_rows, "V6: + Touch 2-3 (NEAR_SUPPORT)")

    sheet_rows = []
    sheet_rows.append(["--- SUMMARY ---"])
    sheet_rows.append(STRATEGY_REPORT_STATS_FIELDS)
    sheet_rows.append(_stats_row_for_sheet("V1", v1_stats))
    sheet_rows.append(_stats_row_for_sheet("V2", v2_stats))
    sheet_rows.append(_stats_row_for_sheet("V3", v3_stats))
    sheet_rows.append(_stats_row_for_sheet("V4", v4_stats))
    sheet_rows.append(_stats_row_for_sheet("V5", v5_stats))
    sheet_rows.append(_stats_row_for_sheet("V6", v6_stats))
    sheet_rows.append([])

    if include_raw_rows:
        sheet_rows.append(["--- MATCHING SETUPS (RAW ROWS) ---"])
        sheet_rows.append(STRATEGY_REPORT_ROW_HEADER)
        for label, rows in (
            ("V1", v1_rows), ("V2", v2_rows), ("V3", v3_rows),
            ("V4", v4_rows), ("V5", v5_rows), ("V6", v6_rows),
        ):
            for r in rows:
                row_values = ["" if r.get(col, "") is None else r.get(col, "") for col in RESULTS_HEADER]
                sheet_rows.append([label] + row_values)

    if dry_run:
        print(f"  [sr_shape_tracker][DRY_RUN] Strategy report sheet mein {len(sheet_rows)} rows likhta "
              f"(V1={len(v1_rows)}, V2={len(v2_rows)}, V3={len(v3_rows)}, "
              f"V4={len(v4_rows)}, V5={len(v5_rows)}, V6={len(v6_rows)} matching setups).")
        return

    ws = _get_strategy_report_worksheet()
    ws.clear()
    ws.update("A1", sheet_rows)
    print(f"  [sr_shape_tracker] Confusion_Strategy_Report sheet update ho gayi — "
          f"V1={len(v1_rows)}, V2={len(v2_rows)}, V3={len(v3_rows)}, "
          f"V4={len(v4_rows)}, V5={len(v5_rows)}, V6={len(v6_rows)} matching setups.")


# ============================================
# LOCAL TESTING
# ============================================
if __name__ == "__main__":
    print("sr_shape_outcome_tracker (v4.2) — standalone test run")
    print(f"Horizons: {HORIZONS_MINUTES} min => candles {HORIZONS_CANDLES}")
    print(f"MIN_SR_TOUCHES = {MIN_SR_TOUCHES} (soft floor, not a hard reliability filter)")
    print(f"RVOL gate: RVOL_20 >= {RVOL_SHORT_THRESHOLD} OR RVOL_96 >= {RVOL_LONG_THRESHOLD}")
    print(f"TARGET_RR = {TARGET_RR}")
    print(f"First-event horizon: {FIRST_EVENT_HORIZON_MINUTES} min")
    resolve_confirmations(dry_run=True)
    resolve_pending(dry_run=True)

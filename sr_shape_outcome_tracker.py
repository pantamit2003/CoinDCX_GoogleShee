"""
sr_shape_outcome_tracker.py
=============================
NAYA STANDALONE MODULE — RVOL/spike-trigger se bilkul INDEPENDENT tha,
ab is version (v3) mein RVOL bhi ek GATING condition ban gaya hai —
neeche "NAYA v3" comments mein detail hai.

YEH FLOWCHART IMPLEMENT KARTA HAI:

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
    NAYA v3: Kya is candle ka RVOL bhi threshold cross kar raha hai?
        (RVOL_20 >= RVOL_SHORT_THRESHOLD  YA  RVOL_96 >= RVOL_LONG_THRESHOLD)
        |
       NO -> skip (volume ka support nahi hai, setup weak maana jayega)
       YES -> aage badho
        |
    Support + CONFUSION -> LONG setup   |   Resistance + CONFUSION -> SHORT setup
        |
    Entry = confusion candle Close
    SL    = LONG: confusion candle LOW  |  SHORT: confusion candle HIGH
        |
    Uske baad next 15/30/45/60/90/120 min mein kya hua?
    (price movement, SL hit/nahi, target R-multiples hit/nahi,
     max favorable/adverse move)
        |
    BACKTEST DATA (Google Sheet mein)

MAIN OBJECTIVE (v3):
    "5+ touch Support/Resistance par, VOLUME SPIKE (RVOL) ke saath
    CONFUSION candle banne ke baad, confusion candle ke LOW/HIGH ko SL
    rakhkar trade karne par historically kya outcome aata hai?" — sirf
    DATA collect karta hai, koi profitability assume nahi karta.

KYUN ALAG MODULE:
    - backtest_tracker.py RVOL-spike TRIGGER se chalta hai aur horizons
      15/45/75 min (candles 1,3,5) track karta hai.
    - Yeh module kisi bhi 15-min candle ko process kar sakta hai —
      condition hai: price valid (MIN_SR_TOUCHES+) S/R level ke paas ho,
      candle shape CONFUSION ho, AUR (v3 se) RVOL bhi threshold cross
      kare. Horizons 15/30/45/60/90/120 min (candles 1,2,3,4,6,8).
    - Isliye purana backtest_tracker.py / pattern_backtest.py /
      Trendline / main-Telegram-bot / Consolidation logic / candle-shape
      classification / MIN_SR_TOUCHES / existing S/R detection ko
      BILKUL touch nahi kiya. Sirf naye, alag Google Sheet tabs use
      hote hain: Confusion_Pending aur Confusion_Backtest_Data.
    - RVOL calculation ka logic bhi wahi hai jo intraday_spike_monitor.py
      mein hai (rolling mean, shift(1), current volume / baseline) —
      koi naya formula nahi, sirf yahan bhi apply kiya gaya hai, aur
      ab yahan gating condition ke roop mein bhi use ho raha hai.

KAISE HOOK KARNA HAI (OPTIONAL):
    import sr_shape_outcome_tracker as sr_shape_tracker
    ...
    sr_shape_tracker.resolve_pending(dry_run=DRY_RUN)          # scan shuru mein ek baar
    ...
    sr_shape_tracker.process_candle(pair, df, dry_run=DRY_RUN)  # har pair ke liye
    Optional hai — is file ko import na karo to kuch bhi existing
    behaviour change nahi hota.

SL / TARGET LOGIC (v2):
    Support + CONFUSION => LONG
        Entry = Close
        SL    = confusion candle ka LOW
        Risk  = Entry - SL
        Target_nR = Entry + n * Risk
    Resistance + CONFUSION => SHORT
        Entry = Close
        SL    = confusion candle ka HIGH
        Risk  = SL - Entry
        Target_nR = Entry - n * Risk

    SL/Target detection candle ke HIGH/LOW se hota hai, sirf Close se
    nahi — future har candle (signal candle ke baad, 120-min window
    tak) ka High/Low check hota hai.

    SAME-CANDLE AMBIGUITY (documented assumption, koi profitability
    claim nahi): agar ek hi future candle ke andar SL aur Target dono
    price-range mein aa jaayein, to hum CONSERVATIVELY maan lete hain
    ki SL pehle hit hua (kyunki OHLC data se intrabar sequence pata
    nahi chalta). Yeh sirf ek measurement convention hai.

VOLUME / RVOL GATING (NAYA — v3):
    Pehle is module mein RVOL ka koi role nahi tha — sirf S/R + shape
    (CONFUSION) se hi setup qualify hota tha. Ab (v3) RVOL bhi ek
    zaroori condition ban gaya hai:

        qualify = (valid S/R ke paas) AND (shape == CONFUSION)
                  AND (RVOL_20 >= RVOL_SHORT_THRESHOLD
                       OR RVOL_96 >= RVOL_LONG_THRESHOLD)

    Matlab: sirf wahi CONFUSION candles aage jaayengi jinke peeche
    volume ka bhi support ho (chahe short-term RVOL_20 spike ho ya
    long-term RVOL_96 spike ho — dono mein se ek kaafi hai, "OR"
    condition, "AND" nahi — isse setups zyada strict-only-BOTH nahi
    ho jaate).

    RVOL_20 aur RVOL_96 dono values (chahe threshold cross karein ya
    na karein) ab Pending aur Results dono sheets mein record hoti
    hain, taaki baad mein backtest mein RVOL vs outcome ka relation
    bhi analyze kiya ja sake.

    Yeh calculation logic bilkul intraday_spike_monitor.py ke
    get_intraday_rvol() jaisa hi hai (rolling(window).mean().shift(1),
    phir current volume / baseline) — sirf yahan last candle ke liye
    nikala jaata hai.
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
MIN_SR_TOUCHES = 5   # backtest_tracker.py / intraday_spike_monitor.py jaisa hi standard

# ---- NAYA v3: RVOL (volume) config — intraday_spike_monitor.py jaisa hi ----
LOOKBACK_SHORT = 20     # RVOL_20 baseline (~5 ghante)
LOOKBACK_LONG = 96      # RVOL_96 baseline (~1 din)
RVOL_SHORT_THRESHOLD = 5.0   # RVOL_20 is se upar -> volume-gate pass
RVOL_LONG_THRESHOLD = 6.0    # RVOL_96 is se upar -> volume-gate pass
# Gate pass hone ke liye dono mein se sirf EK condition true honi chahiye (OR).

MAX_AGE_HOURS = 6                 # itni der baad bhi resolve na ho paaye to discard
MATCH_TOLERANCE_MINUTES = 7       # future-candle match karte waqt tolerance

# v2: Risk-reward multiples jinke target-hit backtest mein check karne hain
TARGET_RR = [1, 1.5, 2, 3]


def _rr_label(rr):
    """1 -> '1R', 1.5 -> '1.5R', 2 -> '2R' — column-naming ke liye."""
    return (f"{rr:g}R")


# "Confusion" naming ke saath worksheet names (sirf naam, baaki logic same)
PENDING_WORKSHEET_NAME = "Confusion_Pending"
RESULTS_WORKSHEET_NAME = "Confusion_Backtest_Data"

# price_position labels jo "Support" side ka concept represent karte hain
# (LONG setup) vs "Resistance" side (SHORT setup).
SUPPORT_POSITIONS = ("NEAR_SUPPORT", "BREAKDOWN_BELOW_SUPPORT")
RESISTANCE_POSITIONS = ("NEAR_RESISTANCE", "BREAKOUT_ABOVE_RESISTANCE")

PENDING_HEADER = [
    "Pair", "Candle_Time_UTC", "Candle_Color", "Close", "RVOL_20", "RVOL_96",
    "Price_Position", "SR_Level_Price", "SR_Touch_Count",
    "Candle_Shape", "Shape_Strength", "Body_Pct", "Rejection_Side",
    "Candle_High", "Candle_Low",
    "Setup_Direction", "Entry_Price", "Stop_Loss", "SL_Distance_Pct",
    "Status",
]

RESULTS_HEADER = (
    ["Pair", "Candle_Time_UTC", "Candle_Color", "Close", "RVOL_20", "RVOL_96",
     "Price_Position", "SR_Level_Price", "SR_Touch_Count",
     "Candle_Shape", "Shape_Strength", "Body_Pct", "Rejection_Side",
     "Setup_Direction", "Entry_Price", "Stop_Loss", "SL_Distance_Pct"]
    + [f"Price_After_{m}min" for m in HORIZONS_MINUTES]
    + [f"PctChg_{m}min" for m in HORIZONS_MINUTES]
    + ["Max_Favorable_Move_Pct", "Max_Adverse_Move_Pct"]
    + [f"SL_Hit_{m}m" for m in HORIZONS_MINUTES]
    + [f"Target_{_rr_label(rr)}_Hit" for rr in TARGET_RR]
)

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


def _is_pair_already_pending(records, pair):
    for r in records:
        if r.get("Pair") == pair and str(r.get("Status", "")).upper() == "PENDING":
            return True
    return False


# ============================================
# NAYA v3: RVOL CALCULATION (intraday_spike_monitor.py jaisa hi logic)
# ============================================
def _get_rvol_for_last_candle(df, lookback_periods=(LOOKBACK_SHORT, LOOKBACK_LONG)):
    """
    intraday_spike_monitor.py ke get_intraday_rvol() jaisa hi formula —
    (current volume) / (pichli N candles ka rolling average volume,
    shift(1) taaki current candle khud apne baseline mein shamil na ho).
    Sirf last (abhi-abhi close hui) candle ke liye value chahiye,
    isliye poore dataframe pe rolling apply karke sirf last row lete hain.

    Return: dict {period: rvol_value_or_None}
        None tab jab itni candles hi available na hon (rolling window
        ke liye insufficient history).
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
    NAYA v3 — volume gate: RVOL_20 >= RVOL_SHORT_THRESHOLD
    YA RVOL_96 >= RVOL_LONG_THRESHOLD (OR condition, dono mandatory nahi).
    Agar dono None hain (insufficient history) to gate FAIL maana jayega
    — bina volume-context ke setup qualify nahi karna chahiye.
    """
    cond_short = (rvol_20 is not None) and (rvol_20 >= RVOL_SHORT_THRESHOLD)
    cond_long = (rvol_96 is not None) and (rvol_96 >= RVOL_LONG_THRESHOLD)
    return cond_short or cond_long


# ============================================
# STEP 1-5: EK CANDLE KO PROCESS KARNA
# ============================================
def process_candle(pair, df, dry_run=False):
    """
    Flowchart ke Step 1-6 implement karta hai ek pair ke liye:
        15-min candle -> valid S/R ke paas? -> shape classify ->
        SIRF CONFUSION -> NAYA v3: RVOL gate pass? ->
        setup direction + entry/SL nikalo -> pending mein daal do
        (future outcome baad mein resolve() karega).

    df: candle dataframe (kam se kam SR_LOOKBACK+ candles chahiye,
        columns: Time/Open/High/Low/Close/Volume)

    Return: True agar candle qualify hoke pending mein add hui,
            False agar skip hui (valid S/R ke paas nahi thi, shape
            CONFUSION nahi thi, RVOL gate fail hua, direction determine
            nahi ho paayi, cooldown mein thi, ya data insufficient tha).
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

    # ---- STEP 2: Valid Support/Resistance ke paas hai? ----
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

    # "Valid S/R ke paas" = kisi bhi non-MID_RANGE label ke saath aana
    # (NEAR_RESISTANCE / NEAR_SUPPORT / BREAKOUT_ABOVE_RESISTANCE /
    # BREAKDOWN_BELOW_SUPPORT) — MID_RANGE ka matlab hai koi valid
    # (5+ touch) level nazdeek nahi hai.
    if price_position == "MID_RANGE" or sr_level_price is None:
        return False

    # ---- STEP 3: Candle shape classify karo ----
    try:
        shape_ctx = classify_candle_shape(candle_open, candle_high, candle_low, close)
    except Exception as e:
        print(f"  [sr_shape_tracker] {pair} shape error: {e}")
        return False

    # ---- CONFUSION-ONLY FILTER ----
    # Sirf CONFUSION shape wali candles hi track karni hain — DOMINANCE
    # aur REJECTION wali candles yahin skip ho jaati hain.
    if shape_ctx["shape"] != "CONFUSION":
        return False

    # ---- NAYA v3: RVOL nikaalo (S/R + CONFUSION dono confirm hone ke baad) ----
    rvol_vals = _get_rvol_for_last_candle(df, lookback_periods=(LOOKBACK_SHORT, LOOKBACK_LONG))
    rvol_20 = rvol_vals[LOOKBACK_SHORT]
    rvol_96 = rvol_vals[LOOKBACK_LONG]

    # ---- NAYA v3: VOLUME GATE — S/R + CONFUSION ke paas RVOL bhi confirm karo ----
    # Agar volume ka support nahi hai (na short-term na long-term RVOL
    # threshold cross hua), to is setup ko weak maankar skip karo.
    if not _rvol_gate_passed(rvol_20, rvol_96):
        print(f"  [sr_shape_tracker] {pair} S/R+CONFUSION mila lekin RVOL gate FAIL "
              f"(RVOL_20={rvol_20}, RVOL_96={rvol_96}, need >= {RVOL_SHORT_THRESHOLD} "
              f"ya >= {RVOL_LONG_THRESHOLD}) — skip.")
        return False

    # ---- SETUP DIRECTION (v2): Support+CONFUSION => LONG,
    #      Resistance+CONFUSION => SHORT ----
    if price_position in SUPPORT_POSITIONS:
        setup_direction = "LONG"
        entry_price = close
        stop_loss = candle_low
    elif price_position in RESISTANCE_POSITIONS:
        setup_direction = "SHORT"
        entry_price = close
        stop_loss = candle_high
    else:
        # Safety net — abhi tak sirf 4 non-MID_RANGE labels hi possible
        # hain, lekin agar future mein naya label add ho to yahan
        # direction determine nahi ho payegi, isliye skip.
        return False

    if setup_direction == "LONG":
        sl_distance_pct = round((entry_price - stop_loss) / entry_price * 100, 3)
    else:
        sl_distance_pct = round((stop_loss - entry_price) / entry_price * 100, 3)

    # SL distance 0 ya negative ho (flat/degenerate candle) to setup
    # invalid hai — risk calculate nahi ho sakta, isliye skip.
    if sl_distance_pct <= 0:
        return False

    # ---- COOLDOWN: same pair already pending ho to skip ----
    ws = _get_pending_worksheet()
    existing_records = ws.get_all_records()
    if _is_pair_already_pending(existing_records, pair):
        print(f"  [sr_shape_tracker] {pair} already pending — skip.")
        return False

    # ---- STEP 5: Pending mein add karo (future outcome baad mein) ----
    if dry_run:
        print(f"  [sr_shape_tracker][DRY_RUN] {pair} pending add: "
              f"pos={price_position} shape={shape_ctx['shape']} "
              f"level={sr_level_price} touches={sr_touch_count} "
              f"RVOL_20={rvol_20} RVOL_96={rvol_96} "
              f"direction={setup_direction} entry={entry_price} sl={stop_loss}")
    else:
        ws.append_row([
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
            setup_direction,
            entry_price,
            stop_loss,
            sl_distance_pct,
            "PENDING",
        ], table_range="A1")

    print(f"  [sr_shape_tracker] {pair} qualified: {price_position} "
          f"(touched {sr_touch_count}x) | Shape=CONFUSION "
          f"({shape_ctx['strength']}, body={shape_ctx['body_pct']}%) | "
          f"RVOL_20={rvol_20} RVOL_96={rvol_96} (gate PASSED) | "
          f"{setup_direction} entry={entry_price} SL={stop_loss} "
          f"({sl_distance_pct}% away)")

    # ---- CONFUSION bot ko Telegram alert (naya setup qualify hua) ----
    setup_msg = _build_setup_message(
        pair, candle_time, price_position, sr_level_price, sr_touch_count,
        shape_ctx, setup_direction, entry_price, stop_loss, sl_distance_pct,
        rvol_20=rvol_20, rvol_96=rvol_96,
    )
    if dry_run:
        print(f"  [sr_shape_tracker][DRY_RUN] Confusion Telegram (setup):\n{setup_msg}\n")
    else:
        try:
            send_confusion_telegram_message(setup_msg)
        except Exception as e:
            print(f"  [sr_shape_tracker] Confusion Telegram (setup) error: {e}")

    return True


# ============================================
# TELEGRAM MESSAGE BUILDERS (Confusion bot)
# ============================================
def _build_setup_message(pair, candle_time, price_position, sr_level_price,
                          sr_touch_count, shape_ctx, setup_direction,
                          entry_price, stop_loss, sl_distance_pct,
                          rvol_20=None, rvol_96=None):
    """Naya CONFUSION setup qualify hote hi (pending mein add hote hi) bhejne wala message."""
    direction_emoji = "🟢" if setup_direction == "LONG" else "🔴"
    rvol_line = (
        f"<b>RVOL_20:</b> {rvol_20 if rvol_20 is not None else 'N/A'}x | "
        f"<b>RVOL_96:</b> {rvol_96 if rvol_96 is not None else 'N/A'}x\n"
    )
    return (
        f"🌀 <b>CONFUSION SETUP DETECTED (volume-confirmed)</b>\n\n"
        f"<b>Pair:</b> {pair}\n"
        f"<b>Candle Time (IST):</b> {_to_ist_str(candle_time)}\n"
        f"{rvol_line}"
        f"<b>Price Position:</b> {price_position} (Level: {sr_level_price}, tested {sr_touch_count}x pehle)\n"
        f"<b>Candle Shape:</b> CONFUSION ({shape_ctx['strength']}, body={shape_ctx['body_pct']}%)\n\n"
        f"{direction_emoji} <b>Setup Direction:</b> {setup_direction}\n"
        f"<b>Entry:</b> {entry_price}\n"
        f"<b>Stop Loss:</b> {stop_loss} ({sl_distance_pct}% away)\n\n"
        f"Ye sirf data-collection/backtest ke liye hai, trade instruction nahi — "
        f"khud verify karke decide karo."
    )


def _build_resolved_message(pair, candle_time, price_position, setup_direction,
                             entry_price, stop_loss, outcome, pct_changes_by_horizon,
                             rvol_20=None, rvol_96=None):
    """120-min window fully resolve hone par (final outcome ke saath) bhejne wala message."""
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
        f"<b>Setup:</b> {setup_direction} | Entry: {entry_price} | SL: {stop_loss}\n\n"
        f"<b>Price % change by horizon:</b>\n{horizon_lines}\n\n"
        f"<b>SL Hit by horizon:</b>\n{sl_lines}\n\n"
        f"<b>Target Hit (R-multiples):</b>\n{targets_lines}\n\n"
        f"<b>Max Favorable:</b> {outcome['max_favorable_pct']}% | "
        f"<b>Max Adverse:</b> {outcome['max_adverse_pct']}%\n\n"
        f"(Sirf record/data — trade instruction nahi.)"
    )


# ============================================
# v2 — SL / TARGET / MAX-MOVE ANALYSIS
# ============================================
def _analyze_trade_outcome(df, candle_time, setup_direction, entry_price, stop_loss):
    """
    Signal-candle ke baad, 120-min window ke andar (saari actual
    candles use karke, sirf horizon-checkpoints nahi) SL aur target
    (TARGET_RR multiples) HIGH/LOW se detect karta hai, plus max
    favorable/adverse move.

    Return: dict with keys:
        "sl_hit_by": {15: bool, 30: bool, ...}   (cumulative — is
                     horizon TAK SL hit hua tha ya nahi)
        "target_hit": {rr: bool for rr in TARGET_RR}   (poori 120-min
                     window mein target hit hua, SL usse pehle hit
                     nahi hua)
        "max_favorable_pct": float ya None
        "max_adverse_pct": float ya None
    """
    max_horizon_min = max(HORIZONS_MINUTES)
    work_df = df.copy()
    time_col = work_df["Time"]
    if time_col.dt.tz is None:
        time_col = time_col.dt.tz_localize("UTC")
    work_df["_t"] = time_col

    window_start = candle_time
    window_end = candle_time + timedelta(minutes=max_horizon_min)
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

    is_long = (setup_direction == "LONG")
    if is_long:
        risk = entry_price - stop_loss
    else:
        risk = stop_loss - entry_price

    targets = {rr: (entry_price + rr * risk) if is_long else (entry_price - rr * risk)
               for rr in TARGET_RR}

    sl_hit_flag = False
    sl_hit_time = None
    best_favorable_extreme = entry_price   # LONG: highest High so far; SHORT: lowest Low so far
    worst_adverse_extreme = entry_price    # LONG: lowest Low so far; SHORT: highest High so far

    for _, cand_row in window_df.iterrows():
        high = float(cand_row["High"])
        low = float(cand_row["Low"])
        cand_time = cand_row["_t"]

        # ---- Max favorable / adverse tracking (SL hit hone ke baad bhi
        # continue karta hai — yeh "agar hold karte" wala theoretical
        # max move hai, trade management assumption nahi) ----
        if is_long:
            best_favorable_extreme = max(best_favorable_extreme, high)
            worst_adverse_extreme = min(worst_adverse_extreme, low)
        else:
            best_favorable_extreme = min(best_favorable_extreme, low)
            worst_adverse_extreme = max(worst_adverse_extreme, high)

        # ---- SL check (is candle mein SL breach hua?) ----
        sl_breached_this_candle = (low <= stop_loss) if is_long else (high >= stop_loss)

        # ---- Target checks (is candle mein kaunse targets breach hue?) ----
        targets_breached_this_candle = set()
        for rr, target_price in targets.items():
            if target_hit[rr]:
                continue
            reached = (high >= target_price) if is_long else (low <= target_price)
            if reached:
                targets_breached_this_candle.add(rr)

        if not sl_hit_flag:
            if sl_breached_this_candle and targets_breached_this_candle:
                # SAME-CANDLE AMBIGUITY: dono ek hi candle mein hue —
                # conservatively SL ko pehle maana jaata hai (documented
                # assumption upar module docstring mein, koi
                # profitability claim nahi).
                sl_hit_flag = True
                sl_hit_time = cand_time
            elif sl_breached_this_candle:
                sl_hit_flag = True
                sl_hit_time = cand_time
            elif targets_breached_this_candle:
                for rr in targets_breached_this_candle:
                    target_hit[rr] = True

        # agar sl_hit_flag pehle hi True ho chuka hai (pichli candle mein),
        # to is candle ke targets count nahi honge — trade already
        # stopped-out maana jaata hai.
        if sl_hit_flag and sl_hit_time is not None:
            for m in HORIZONS_MINUTES:
                if sl_hit_time <= candle_time + timedelta(minutes=m):
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
# STEP 6: PENDING CANDLES RESOLVE KARNA
#          (future 15/30/45/60/90/120 min outcome nikaalna)
# ============================================
def resolve_pending(dry_run=False):
    pending_ws = _get_pending_worksheet()
    records = pending_ws.get_all_records()
    if not records:
        print("  [sr_shape_tracker] Koi pending candle nahi hai.")
        return

    now_utc = datetime.now(timezone.utc)
    max_horizon_candles = max(HORIZONS_CANDLES)

    pairs_needed = {r["Pair"] for r in records if str(r.get("Status", "")).upper() == "PENDING"}
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
        if str(row.get("Status", "")).upper() != "PENDING":
            continue  # (is tab mein sirf PENDING rows honi chahiye, safety check)

        pair = row["Pair"]
        candle_time = _to_utc_dt(row["Candle_Time_UTC"])
        close = float(row["Close"])
        setup_direction = row.get("Setup_Direction", "")
        entry_price = float(row.get("Entry_Price", close) or close)
        stop_loss_raw = row.get("Stop_Loss", "")
        stop_loss = float(stop_loss_raw) if stop_loss_raw not in ("", None) else None

        # RVOL values pending row se hi carry-forward karo (candle time
        # pe jo the) — resolve() ke waqt firse recalculate nahi karte,
        # kyunki candle purani ho chuki hoti hai aur rolling baseline
        # thoda shift ho chuka hoga; jo signal-time pe record hua wahi
        # authoritative hai.
        rvol_20_raw = row.get("RVOL_20", "")
        rvol_96_raw = row.get("RVOL_96", "")
        rvol_20 = float(rvol_20_raw) if rvol_20_raw not in ("", None) else None
        rvol_96 = float(rvol_96_raw) if rvol_96_raw not in ("", None) else None

        if now_utc - candle_time > timedelta(hours=MAX_AGE_HOURS):
            print(f"  [sr_shape_tracker] {pair} @ {row['Candle_Time_UTC']} stale, discard.")
            discarded_count += 1
            continue

        df = candle_cache.get(pair)
        if df is None or df.empty:
            still_pending.append(row)
            continue

        # Sabse dooriwala horizon (120 min) available hai tabhi fully resolve karo
        max_target_time = candle_time + timedelta(minutes=RESOLUTION_MINUTES * max_horizon_candles)
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
            setup_direction,
            entry_price,
            stop_loss if stop_loss is not None else "",
            row.get("SL_Distance_Pct", ""),
        ]

        # ---- 15/30/45/60/90/120 min Price/PctChg tracking (unchanged) ----
        prices_after = []
        pct_changes = []
        all_found = True
        for n_candles in HORIZONS_CANDLES:
            target_time = candle_time + timedelta(minutes=RESOLUTION_MINUTES * n_candles)
            found, price = _find_closest_candle(df, target_time)
            if not found:
                all_found = False
                break
            pct_change = round((price - close) / close * 100, 3)
            prices_after.append(price)
            pct_changes.append(pct_change)

        if not all_found:
            still_pending.append(row)
            continue

        result_row += prices_after + pct_changes

        # ---- v2: SL / Target / Max-move analysis ----
        if setup_direction in ("LONG", "SHORT") and stop_loss is not None:
            outcome = _analyze_trade_outcome(df, candle_time, setup_direction, entry_price, stop_loss)
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

            # ---- CONFUSION bot ko Telegram alert (final outcome resolve hua) ----
            pct_by_horizon = dict(zip(HORIZONS_MINUTES, pct_changes))
            resolved_msg = _build_resolved_message(
                pair, candle_time, row.get("Price_Position", "UNKNOWN"),
                setup_direction, entry_price, stop_loss, outcome, pct_by_horizon,
                rvol_20=rvol_20, rvol_96=rvol_96,
            )
            if dry_run:
                print(f"  [sr_shape_tracker][DRY_RUN] Confusion Telegram (resolved):\n{resolved_msg}\n")
            else:
                try:
                    send_confusion_telegram_message(resolved_msg)
                except Exception as e:
                    print(f"  [sr_shape_tracker] Confusion Telegram (resolved) error: {e}")
        else:
            # Setup direction/SL missing (theoretically nahi hona chahiye,
            # safety fallback) — sab kuch blank/NO chhod do.
            result_row.append("")
            result_row.append("")
            for _ in HORIZONS_MINUTES:
                result_row.append("")
            for _ in TARGET_RR:
                result_row.append("")

        if dry_run:
            print(f"  [sr_shape_tracker][DRY_RUN] RESOLVED: {result_row}")
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
                r.get("Candle_High", ""),
                r.get("Candle_Low", ""),
                r.get("Setup_Direction", ""),
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
          f"Discarded (stale): {discarded_count}")


# ============================================
# LOCAL TESTING
# ============================================
if __name__ == "__main__":
    print("sr_shape_outcome_tracker — standalone test run")
    print(f"Horizons: {HORIZONS_MINUTES} min => candles {HORIZONS_CANDLES}")
    print(f"MIN_SR_TOUCHES = {MIN_SR_TOUCHES}")
    print(f"RVOL gate: RVOL_20 >= {RVOL_SHORT_THRESHOLD} OR RVOL_96 >= {RVOL_LONG_THRESHOLD}")
    print(f"TARGET_RR = {TARGET_RR}")
    resolve_pending(dry_run=True)

"""
backtest_tracker.py  (v6 — N+1/N+2 Shape + S/R Based Classification)
=====================================================================
CHANGES IN THIS VERSION (v6):

v6 CHANGE — RICHER CONFIRMATION CLASSIFICATION:
    Pehle (v5) confirmation sirf itna check karta tha:
        N+2 High > N+1 High  → CONFIRMED_CONTINUATION
        N+2 Low  < N+1 Low   → FAILED_BREAKOUT
    Yeh "DOMINANCE → REJECTION → OPPOSITE DOMINANCE" jaisa specific
    false-breakout pattern properly capture nahi karta tha — usse
    generic FAILED_BREAKOUT keh diya jaata tha.

    Ab N+1 aur N+2 ka poora context (shape, direction, rejection_side)
    use hota hai, 5 categories mein classify karne ke liye:
        REAL_BREAKOUT            — breakout confirm, N+2 strong same-direction
        WEAK_BREAKOUT             — breakout confirm hua, par conviction low
        FAILED_BREAKOUT           — generic adverse break (specific pattern nahi)
        CONFIRMED_FALSE_BREAKOUT  — DOMINANCE → REJECTION → OPPOSITE DOMINANCE
        STILL_UNDECIDED           — clear structure nahi bani

    Yeh sirf `_check_breakout_confirmation()` ke andar hua hai —
    baaki poora system (cooldown, V2 tabs, dual Telegram bot,
    15/45/75 min price tracking) v5 jaisa hi hai, touch nahi kiya.

v5 CHANGE 1 — COOLDOWN LOGIC (retained):
    Agar koi pair already "Pending_Spikes_V2" mein PENDING hai, to
    us pair ka naya signal automatically skip ho jayega.

v5 CHANGE 2 — FRESH V2 TABS (retained):
    - Pending_Spikes_V2
    - Spike_Backtest_Results_V2
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from data.candles import get_candles
from notifications.telegram_bot import send_telegram_message, send_strong_telegram_message
from candle_shape import classify_candle_shape
import config


HORIZONS = [1, 3, 5]           # candles baad check karna hai (15/45/75 min)
RESOLUTION = "15"
RESOLUTION_MINUTES = 15
MAX_AGE_HOURS = 6               # itne ghante baad bhi resolve na ho paaye toh discard
MATCH_TOLERANCE_MINUTES = 7     # candle-time match karte waqt itni tolerance rakho

# ---- V2 TABS — fresh clean data yahan jayega ----
PENDING_WORKSHEET_NAME = "Pending_Spikes_V2"
RESULTS_WORKSHEET_NAME = "Spike_Backtest_Results_V2"

# v6: N1_Direction, N2_Direction, N1_Rejection_Side, N2_Rejection_Side naye columns
PENDING_HEADER = [
    "Pair", "Trigger_Type", "Candle_Color", "Spike_Time_UTC", "Spike_Close",
    "Price_Position", "RVOL_20", "Confirmation_Status",
    "Trend_Type", "Trend_Detail", "Candle_Shape",
    "SR_Level_Price", "SR_Touch_Count",
    "N1_Candle_Shape", "N2_Candle_Shape",
    "N1_Direction", "N2_Direction", "N1_Rejection_Side", "N2_Rejection_Side",
]

RESULTS_HEADER = [
    "Pair", "Spike_Time_UTC", "Candle_Color", "Trigger_Type", "Spike_Close", "Price_Position",
    "Price_After_1", "PctChg_1", "Price_After_3", "PctChg_3",
    "Price_After_5", "PctChg_5", "Confirmation_Status",
    "Trend_Type", "Trend_Detail", "Candle_Shape",
    "SR_Level_Price", "SR_Touch_Count",
    "N1_Candle_Shape", "N2_Candle_Shape",
    "N1_Direction", "N2_Direction", "N1_Rejection_Side", "N2_Rejection_Side",
]

CONFIRMATION_ALERT_RVOL_THRESHOLD = 6.0

STRONG_BOT_ALLOWED_POSITIONS = (
    "BREAKOUT_ABOVE_RESISTANCE",
    "BREAKDOWN_BELOW_SUPPORT",
    "NEAR_RESISTANCE",
    "NEAR_SUPPORT",
)

# v6: naye 5-way classification labels — emoji aur note dono ke liye
STATUS_EMOJI = {
    "REAL_BREAKOUT": "✅",
    "WEAK_BREAKOUT": "🟡",
    "FAILED_BREAKOUT": "❌",
    "CONFIRMED_FALSE_BREAKOUT": "🔻",
    "STILL_UNDECIDED": "⚪",
}
STATUS_NOTE = {
    "REAL_BREAKOUT": "Breakout genuinely confirm hua — N+2 strong candle ne same direction mein structure break kiya. Conviction high hai.",
    "WEAK_BREAKOUT": "Breakout ki taraf move hua, par conviction low hai — N+2 weak/uncertain thi. Kam bharosemand continuation.",
    "FAILED_BREAKOUT": "Breakout expected direction mein sustain nahi hua — price ulti taraf structure tod gaya.",
    "CONFIRMED_FALSE_BREAKOUT": "Classic false-breakout pattern: spike ke baad rejection, phir opposite direction mein strong dominance — reversal confirm.",
    "STILL_UNDECIDED": "Abhi clear structure nahi bana — price kisi bhi taraf decisively nahi gaya. Aur wait karo.",
}


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


def _to_ist_str(value):
    dt = pd.to_datetime(value)
    if dt.tzinfo is None:
        dt = dt.tz_localize("UTC")
    ist_dt = dt + pd.Timedelta(hours=5, minutes=30)
    return ist_dt.strftime("%Y-%m-%d %H:%M:%S") + " IST"


def _find_closest_candle(df, target_time, tolerance_minutes=MATCH_TOLERANCE_MINUTES):
    found, row = _find_closest_row(df, target_time, tolerance_minutes)
    if found:
        return True, float(row["Close"])
    return False, None


def _find_closest_row(df, target_time, tolerance_minutes=MATCH_TOLERANCE_MINUTES):
    df_times = df["Time"]
    if df_times.dt.tz is None:
        df_times = df_times.dt.tz_localize("UTC")
    diffs = (df_times - target_time).abs()
    min_idx = diffs.idxmin()
    min_diff = diffs.loc[min_idx]
    if min_diff <= timedelta(minutes=tolerance_minutes):
        return True, df.loc[min_idx]
    return False, None


def _shape_ctx(open_price, high, low, close):
    """
    Candle ka OHLC leke poora classify_candle_shape() dict return karta
    hai (shape, direction, body_pct, strength, rejection_side) — v6 mein
    ab sirf readable label string nahi, raw fields bhi chahiye taaki
    classification logic unpe decision le sake.
    """
    try:
        return classify_candle_shape(float(open_price), float(high), float(low), float(close))
    except Exception:
        return {"shape": "UNKNOWN", "direction": "UNKNOWN", "body_pct": 0.0,
                "strength": "UNKNOWN", "rejection_side": None}


def _shape_label(ctx):
    """ctx dict se readable label string banata hai (Sheet/Telegram ke liye)."""
    if ctx["shape"] == "UNKNOWN":
        return "UNKNOWN"
    return f"{ctx['shape']} ({ctx['strength']}, body={ctx['body_pct']}%)"


# ============================================
# COOLDOWN CHECK
# ============================================
def _is_pair_already_pending(records, pair):
    """
    Check karta hai ki given pair already Pending_Spikes_V2 mein
    PENDING status ke saath hai ya nahi.
    """
    for r in records:
        if r.get("Pair") == pair and str(r.get("Confirmation_Status", "")).upper() == "PENDING":
            return True
    return False


# ============================================
# v6 — N+1/N+2 SHAPE + S/R BASED CLASSIFICATION
# ============================================
def _classify_confirmation(candle_color, n1_ctx, n2_ctx, broke_high, broke_low):
    """
    Doc ke Step 2 wala 5-way classification logic.

    candle_color: spike-candle ka GREEN/RED (expected direction)
    n1_ctx, n2_ctx: classify_candle_shape() ke poore dicts (N+1, N+2 candles)
    broke_high: N+2 High > N+1 High
    broke_low:  N+2 Low  < N+1 Low

    Return: classification string (REAL_BREAKOUT / WEAK_BREAKOUT /
            FAILED_BREAKOUT / CONFIRMED_FALSE_BREAKOUT / STILL_UNDECIDED)
    """
    expected_up = (candle_color == "GREEN")

    # "Favorable" = jis taraf spike ki original direction thi, us taraf
    # structure break hua. "Adverse" = ulti taraf break hua.
    structure_broken_favorable = (broke_high if expected_up else broke_low)
    structure_broken_adverse = (broke_low if expected_up else broke_high)

    n2_same_direction = (
        (n2_ctx["direction"] == "GREEN" and expected_up)
        or (n2_ctx["direction"] == "RED" and not expected_up)
    )

    # Spike ki original direction fail hone par jis side ki rejection
    # aani chahiye thi (GREEN spike fail => UPPER rejection; RED spike
    # fail => LOWER rejection)
    failure_rejection_side = "UPPER" if expected_up else "LOWER"

    # ---- 1. CONFIRMED_FALSE_BREAKOUT (sabse specific pattern, pehle check) ----
    # Pattern: N+1 REJECTION (galat side se) → N+2 OPPOSITE DOMINANCE → adverse break
    if (
        n1_ctx["shape"] == "REJECTION"
        and n1_ctx.get("rejection_side") == failure_rejection_side
        and n2_ctx["shape"] == "DOMINANCE"
        and not n2_same_direction
        and structure_broken_adverse
    ):
        return "CONFIRMED_FALSE_BREAKOUT"

    # ---- 2. REAL_BREAKOUT — favorable break + N+2 strong same-direction DOMINANCE ----
    if (
        structure_broken_favorable
        and n2_same_direction
        and n2_ctx["shape"] == "DOMINANCE"
        and n2_ctx.get("strength") == "STRONG"
    ):
        return "REAL_BREAKOUT"

    # ---- 3. WEAK_BREAKOUT — favorable break hua, par N+2 utni strong nahi ----
    if structure_broken_favorable and n2_same_direction:
        return "WEAK_BREAKOUT"

    # ---- 4. FAILED_BREAKOUT — generic adverse break (CONFIRMED_FALSE_BREAKOUT ka specific pattern match nahi hua) ----
    if structure_broken_adverse:
        return "FAILED_BREAKOUT"

    # ---- 5. STILL_UNDECIDED — koi clear structure nahi bana ----
    return "STILL_UNDECIDED"


def _check_breakout_confirmation(df, spike_time, candle_color):
    """
    N+1 (indecision) aur N+2 (confirmation) candles check karta hai,
    unka poora shape-context nikalta hai, aur _classify_confirmation()
    se 5-way status decide karwata hai.

    Return: dict ya None (agar abhi determine nahi ho sakta)
        {
            "status": ...,
            "n1_shape": label str, "n2_shape": label str,
            "n1_direction": GREEN/RED, "n2_direction": GREEN/RED,
            "n1_rejection_side": UPPER/LOWER/None,
            "n2_rejection_side": UPPER/LOWER/None,
        }
    """
    target_n1 = spike_time + timedelta(minutes=RESOLUTION_MINUTES * 1)
    target_n2 = spike_time + timedelta(minutes=RESOLUTION_MINUTES * 2)

    found_n1, row_n1 = _find_closest_row(df, target_n1)
    found_n2, row_n2 = _find_closest_row(df, target_n2)

    if not found_n1 or not found_n2:
        return None

    high_n1, low_n1 = float(row_n1["High"]), float(row_n1["Low"])
    high_n2, low_n2 = float(row_n2["High"]), float(row_n2["Low"])

    broke_high = high_n2 > high_n1
    broke_low = low_n2 < low_n1

    n1_ctx = _shape_ctx(row_n1["Open"], high_n1, low_n1, row_n1["Close"])
    n2_ctx = _shape_ctx(row_n2["Open"], high_n2, low_n2, row_n2["Close"])

    status = _classify_confirmation(candle_color, n1_ctx, n2_ctx, broke_high, broke_low)

    return {
        "status": status,
        "n1_shape": _shape_label(n1_ctx),
        "n2_shape": _shape_label(n2_ctx),
        "n1_direction": n1_ctx["direction"],
        "n2_direction": n2_ctx["direction"],
        "n1_rejection_side": n1_ctx.get("rejection_side"),
        "n2_rejection_side": n2_ctx.get("rejection_side"),
    }


# ============================================
# PUBLIC: add_pending — naya spike track karna shuru karo
# ============================================
def add_pending(pair, trigger_type, candle_color, spike_time, spike_close,
                 price_position="UNKNOWN", rvol_20=0.0,
                 trend_type="UNKNOWN", trend_detail="", candle_shape="UNKNOWN",
                 sr_level_price=None, sr_touch_count=0):
    """
    Naya spike pending mein add karta hai — pehle COOLDOWN check hota hai.
    """
    ws = _get_pending_worksheet()

    existing_records = ws.get_all_records()
    if _is_pair_already_pending(existing_records, pair):
        print(f"  [cooldown] {pair} already pending mein hai — naya signal skip kiya.")
        return

    ws.append_row([
        pair,
        trigger_type,
        candle_color,
        str(_to_utc_dt(spike_time)),
        float(spike_close),
        price_position,
        round(float(rvol_20), 2),
        "PENDING",
        trend_type,
        trend_detail,
        candle_shape,
        sr_level_price if sr_level_price is not None else "",
        sr_touch_count,
        "", "",   # N1_Candle_Shape, N2_Candle_Shape — abhi khaali, resolve pe bharega
        "", "", "", "",   # N1_Direction, N2_Direction, N1_Rejection_Side, N2_Rejection_Side
    ], table_range="A1")


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
        n1_shape = row.get("N1_Candle_Shape", "")
        n2_shape = row.get("N2_Candle_Shape", "")
        n1_direction = row.get("N1_Direction", "")
        n2_direction = row.get("N2_Direction", "")
        n1_rejection_side = row.get("N1_Rejection_Side", "")
        n2_rejection_side = row.get("N2_Rejection_Side", "")

        if now_utc - spike_time > timedelta(hours=MAX_AGE_HOURS):
            print(f"  [backtest_tracker] {pair} @ {row['Spike_Time_UTC']} stale, discard.")
            discarded_count += 1
            continue

        df = candle_cache.get(pair)
        if df is None or df.empty:
            still_pending.append(row)
            continue

        # ---- CONFIRMATION-CANDLE CHECK ----
        if confirmation_status == "PENDING":
            confirmation_result = _check_breakout_confirmation(df, spike_time, candle_color)
            if confirmation_result is not None:
                new_status = confirmation_result["status"]
                n1_shape = confirmation_result["n1_shape"]
                n2_shape = confirmation_result["n2_shape"]
                n1_direction = confirmation_result["n1_direction"]
                n2_direction = confirmation_result["n2_direction"]
                n1_rejection_side = confirmation_result["n1_rejection_side"] or ""
                n2_rejection_side = confirmation_result["n2_rejection_side"] or ""

                confirmation_status = new_status
                row["Confirmation_Status"] = new_status
                row["N1_Candle_Shape"] = n1_shape
                row["N2_Candle_Shape"] = n2_shape
                row["N1_Direction"] = n1_direction
                row["N2_Direction"] = n2_direction
                row["N1_Rejection_Side"] = n1_rejection_side
                row["N2_Rejection_Side"] = n2_rejection_side

                if rvol_20 >= CONFIRMATION_ALERT_RVOL_THRESHOLD:
                    confirmation_sent_count += 1
                    emoji = STATUS_EMOJI.get(new_status, "")
                    status_note = STATUS_NOTE.get(new_status, "")

                    target_n2 = spike_time + timedelta(minutes=RESOLUTION_MINUTES * 2)
                    found_n2, row_n2 = _find_closest_row(df, target_n2)
                    pct_move_so_far = ""
                    if found_n2:
                        current_price = float(row_n2["Close"])
                        pct = round((current_price - spike_close) / spike_close * 100, 2)
                        pct_move_so_far = f"{pct:+.2f}%"

                    direction_label = "UP (long)" if candle_color == "GREEN" else "DOWN (short)"

                    msg = (
                        f"{emoji} <b>CONFIRMATION UPDATE — {pair}</b>\n\n"
                        f"<b>Original Spike Info:</b>\n"
                        f"Spike Time (IST): {_to_ist_str(row['Spike_Time_UTC'])}\n"
                        f"Trigger Type: {row.get('Trigger_Type', 'N/A')}\n"
                        f"Spike Direction: {direction_label}\n"
                        f"Spike Close: {spike_close}\n"
                        f"RVOL_20 (at spike): {rvol_20:.2f}x\n"
                        f"Price Position (at spike): {row.get('Price_Position', 'N/A')}\n"
                        f"Trend (at spike): {row.get('Trend_Type', 'N/A')}\n"
                        f"Candle Shape (at spike): {row.get('Candle_Shape', 'N/A')}\n\n"
                        f"<b>Confirmation Result:</b>\n"
                        f"Status: <b>{new_status}</b>\n"
                        f"N+1 (Indecision) Shape: {n1_shape}"
                        + (f", rejection_side={n1_rejection_side}" if n1_rejection_side else "") + "\n"
                        f"N+2 (Confirmation) Shape: {n2_shape}"
                        + (f", rejection_side={n2_rejection_side}" if n2_rejection_side else "") + "\n"
                        f"Move so far (since spike): {pct_move_so_far}\n\n"
                        f"{status_note}"
                    )

                    if dry_run:
                        print(f"  [DRY_RUN] Confirmation Telegram: {msg}")
                    else:
                        try:
                            send_telegram_message(msg)
                        except Exception as e:
                            print(f"  [backtest_tracker] Confirmation Telegram error: {e}")

                        original_shape = str(row.get("Candle_Shape", ""))
                        original_position = row.get("Price_Position", "UNKNOWN")
                        if (original_shape.startswith("DOMINANCE")
                                and original_position in STRONG_BOT_ALLOWED_POSITIONS):
                            try:
                                send_strong_telegram_message(msg)
                            except Exception as e:
                                print(f"  [backtest_tracker] Strong-bot confirmation error: {e}")

        max_target_time = spike_time + timedelta(minutes=RESOLUTION_MINUTES * max_horizon)
        found_max, _ = _find_closest_candle(df, max_target_time)
        if not found_max:
            still_pending.append(row)
            continue

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
        result_row.append(row.get("Trend_Type", "UNKNOWN"))
        result_row.append(row.get("Trend_Detail", ""))
        result_row.append(row.get("Candle_Shape", "UNKNOWN"))
        result_row.append(row.get("SR_Level_Price", ""))
        result_row.append(row.get("SR_Touch_Count", 0))
        result_row.append(n1_shape)
        result_row.append(n2_shape)
        result_row.append(n1_direction)
        result_row.append(n2_direction)
        result_row.append(n1_rejection_side)
        result_row.append(n2_rejection_side)

        if dry_run:
            print(f"  [backtest_tracker][DRY_RUN] RESOLVED: {result_row}")
        else:
            try:
                _get_results_worksheet().append_row(result_row, table_range="A1")
            except Exception as e:
                print(f"  [backtest_tracker] Results likhne mein error: {e}")
                still_pending.append(row)
                continue

        resolved_count += 1

    if not dry_run:
        try:
            clean_rows = [[
                r["Pair"], r["Trigger_Type"], r["Candle_Color"],
                r["Spike_Time_UTC"], r["Spike_Close"],
                r.get("Price_Position", "UNKNOWN"),
                r.get("RVOL_20", 0),
                r.get("Confirmation_Status", "PENDING"),
                r.get("Trend_Type", "UNKNOWN"),
                r.get("Trend_Detail", ""),
                r.get("Candle_Shape", "UNKNOWN"),
                r.get("SR_Level_Price", ""),
                r.get("SR_Touch_Count", 0),
                r.get("N1_Candle_Shape", ""),
                r.get("N2_Candle_Shape", ""),
                r.get("N1_Direction", ""),
                r.get("N2_Direction", ""),
                r.get("N1_Rejection_Side", ""),
                r.get("N2_Rejection_Side", ""),
            ] for r in still_pending]
            pending_ws.clear()
            pending_ws.update([PENDING_HEADER] + clean_rows)
        except Exception as e:
            print(f"  [backtest_tracker] Pending_Spikes_V2 update error: {e}")
    else:
        print(f"  [backtest_tracker][DRY_RUN] Still pending: {len(still_pending)}")

    print(f"  [backtest_tracker] Resolved: {resolved_count} | "
          f"Still pending: {len(still_pending)} | "
          f"Discarded (stale): {discarded_count} | "
          f"Confirmation alerts sent: {confirmation_sent_count}")

"""
live_v1_v6_scanner.py

FAST LIVE V1-V6 TELEGRAM SCANNER

Source of truth:
    sr_shape_outcome_tracker.py

IMPORTANT:
    Existing V1-V6 strategy logic is NOT changed.

Routing:
    V1 / V2 / V3 -> Resistance Telegram Bot
    V4 / V5 / V6 -> Support Telegram Bot

Detection:
    Existing process_candle() performs:
        15m closed candle
        -> S/R
        -> CONFUSION
        -> RVOL gate
        -> AWAITING_CONFIRMATION

    After that, this scanner applies the EXISTING V1-V6 report
    classification helpers to the same setup.

Telegram:
    Alert is sent immediately after classification.
    No next-candle confirmation wait.

Order flow:
    Optional informational snapshot only.
    It is NEVER used for V1-V6 detection.
"""

import html
import json
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from data.candles import get_candles
from data.order_flow import get_order_flow_snapshot
from exchange.coindcx import get_active_pairs
import sr_shape_outcome_tracker as sr_shape_tracker
import config


# ============================================================
# CONFIG
# ============================================================

DRY_RUN = (
    os.environ.get("DRY_RUN", "True")
    .strip()
    .lower()
    in ("1", "true", "yes")
)

RESOLUTION = "15"

MAX_PAIRS_LIVE = 300

# Moderate concurrency.
# Increase only if CoinDCX API remains stable.
CANDLE_WORKERS = 10

# Safety-net duplicate state.
STATE_FILE = os.environ.get(
    "LIVE_V1_V6_STATE_FILE",
    "live_v1_v6_alert_state.json"
)

MAX_STATE_KEYS = 500


# ============================================================
# TELEGRAM CREDENTIALS
# ============================================================

def _get_resistance_credentials():
    token = (
        os.environ.get("V1_V3_BOT_TOKEN")
        or getattr(config, "V1_V3_BOT_TOKEN", None)
    )

    chat_id = (
        os.environ.get("V1_V3_CHAT_ID")
        or getattr(config, "V1_V3_CHAT_ID", None)
    )

    return token, chat_id


def _get_support_credentials():
    token = (
        os.environ.get("V4_V6_BOT_TOKEN")
        or getattr(config, "V4_V6_BOT_TOKEN", None)
    )

    chat_id = (
        os.environ.get("V4_V6_CHAT_ID")
        or getattr(config, "V4_V6_CHAT_ID", None)
    )

    return token, chat_id


def _send_telegram(token, chat_id, text):
    if not token or not chat_id:
        print("  [live_v1_v6] Telegram credentials missing.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=10,
        )

        if response.status_code != 200:
            print(
                f"  [live_v1_v6] Telegram failed: "
                f"{response.status_code} {response.text}"
            )
            return False

        return True

    except Exception as exc:
        print(
            f"  [live_v1_v6] Telegram exception: {exc}"
        )
        return False


# ============================================================
# STATE
# ============================================================

def _load_state():
    if not os.path.exists(STATE_FILE):
        return {"alerted_keys": []}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        state.setdefault("alerted_keys", [])
        return state

    except Exception:
        return {"alerted_keys": []}


def _save_state(state):
    try:
        state["alerted_keys"] = state["alerted_keys"][
            -MAX_STATE_KEYS:
        ]

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(state, f)

    except Exception as exc:
        print(
            f"  [live_v1_v6] State save error: {exc}"
        )


# ============================================================
# EXISTING V1-V6 LOGIC
# ============================================================

def _setup_to_report_row(setup):
    """
    Converts the setup_out dictionary produced by the existing
    process_candle() into the same field names used by the
    existing V1-V6 report helpers.

    NO strategy logic is recreated here.
    """

    return {
        "Price_Position": setup.get("price_position"),
        "RVOL_20": (
            setup.get("rvol_20")
            if setup.get("rvol_20") is not None
            else ""
        ),
        "SR_Touch_Count": (
            setup.get("sr_touch_count")
            if setup.get("sr_touch_count") is not None
            else ""
        ),
        "Body_Pct": (
            setup.get("body_pct")
            if setup.get("body_pct") is not None
            else ""
        ),
    }


def _get_matching_strategies(setup):
    """
    Uses the EXACT existing report helper functions.

    No new thresholds.
    No new S/R rules.
    No new body rule.
    No new volume rule.
    """

    row = _setup_to_report_row(setup)

    matched = []

    # ---------------- RESISTANCE ----------------

    if (
        sr_shape_tracker._in_rvol_2_3_band(row)
        and sr_shape_tracker._touch_2_3(row)
        and sr_shape_tracker._body_lt_20(row)
        and sr_shape_tracker._near_resistance(row)
    ):
        matched.append("V1")

    if (
        sr_shape_tracker._in_rvol_2_3_band(row)
        and sr_shape_tracker._body_lt_20(row)
        and sr_shape_tracker._near_resistance(row)
    ):
        matched.append("V2")

    if (
        sr_shape_tracker._in_rvol_2_3_band(row)
        and sr_shape_tracker._touch_2_3(row)
        and sr_shape_tracker._near_resistance(row)
    ):
        matched.append("V3")

    # ---------------- SUPPORT ----------------

    if (
        sr_shape_tracker._in_rvol_2_3_band(row)
        and sr_shape_tracker._touch_2_3(row)
        and sr_shape_tracker._body_lt_20(row)
        and sr_shape_tracker._near_support(row)
    ):
        matched.append("V4")

    if (
        sr_shape_tracker._in_rvol_2_3_band(row)
        and sr_shape_tracker._body_lt_20(row)
        and sr_shape_tracker._near_support(row)
    ):
        matched.append("V5")

    if (
        sr_shape_tracker._in_rvol_2_3_band(row)
        and sr_shape_tracker._touch_2_3(row)
        and sr_shape_tracker._near_support(row)
    ):
        matched.append("V6")

    return matched


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def _fmt(value, suffix=""):
    if value is None or value == "":
        return "N/A"

    return html.escape(
        f"{value}{suffix}"
    )


def _build_setup_message(setup, strategies):
    resistance = any(
        s in ("V1", "V2", "V3")
        for s in strategies
    )

    side = (
        "RESISTANCE"
        if resistance
        else "SUPPORT"
    )

    strategy_text = " + ".join(strategies)

    return (
        f"🔔 <b>LIVE V1-V6 SETUP — {side}</b>\n"
        f"<b>Matched:</b> {strategy_text}\n\n"

        f"<b>Pair:</b> {_fmt(setup.get('pair'))}\n"
        f"<b>Candle Time (IST):</b> "
        f"{_fmt(setup.get('candle_time_ist'))}\n\n"

        f"<b>Confusion High / Low:</b> "
        f"{_fmt(setup.get('confusion_high'))} / "
        f"{_fmt(setup.get('confusion_low'))}\n"

        f"<b>Conditional:</b> "
        f"High break = LONG "
        f"(SL {_fmt(setup.get('confusion_low'))}) | "
        f"Low break = SHORT "
        f"(SL {_fmt(setup.get('confusion_high'))})\n\n"

        f"<b>RVOL_20:</b> "
        f"{_fmt(setup.get('rvol_20'), 'x')} | "
        f"<b>RVOL_96:</b> "
        f"{_fmt(setup.get('rvol_96'), 'x')}\n"

        f"<b>Price Position:</b> "
        f"{_fmt(setup.get('price_position'))}\n"

        f"<b>S/R Level:</b> "
        f"{_fmt(setup.get('sr_level_price'))}\n"

        f"<b>Touch Count:</b> "
        f"{_fmt(setup.get('sr_touch_count'))}\n"

        f"<b>Candle Shape:</b> "
        f"{_fmt(setup.get('candle_shape'))} "
        f"({_fmt(setup.get('shape_strength'))}, "
        f"body={_fmt(setup.get('body_pct'), '%')})\n"

        f"<b>Regime (Recent):</b> "
        f"{_fmt(setup.get('market_regime'))} "
        f"(score={_fmt(setup.get('regime_score'))})\n"

        f"<b>Regime (Background):</b> "
        f"{_fmt(setup.get('background_regime'))} "
        f"(score={_fmt(setup.get('background_regime_score'))})\n\n"

        f"⚠️ <i>Direction pending. "
        f"Next-candle confirmation is NOT required for this alert. "
        f"This is a setup alert, not a trade instruction.</i>"
    )


# ============================================================
# ORDER FLOW — INFORMATION ONLY
# ============================================================

def _build_order_flow_message(setup, order_flow):
    """
    IMPORTANT:
    This is NOT used for V1-V6 qualification.
    """

    if not order_flow:
        return None

    buy = order_flow.get("Aggressive_Buy_Volume")
    sell = order_flow.get("Aggressive_Sell_Volume")
    delta = order_flow.get("Delta")
    delta_pct = order_flow.get("Delta_Pct")
    sample = order_flow.get("Order_Flow_Sample_Size")
    span = order_flow.get("Order_Flow_Span_Seconds")

    if all(
        value is None
        for value in (buy, sell, delta, delta_pct)
    ):
        return None

    return (
        f"📊 <b>LIVE TRADE SNAPSHOT — INFO ONLY</b>\n"
        f"<b>Pair:</b> {_fmt(setup.get('pair'))}\n\n"

        f"<i>CoinDCX ke recent trades ka snapshot hai. "
        f"Yeh 15-minute candle Delta nahi hai aur "
        f"V1-V6 detection/selection mein use nahi hua.</i>\n\n"

        f"<b>Buy Volume:</b> {_fmt(buy)}\n"
        f"<b>Sell Volume:</b> {_fmt(sell)}\n"
        f"<b>Snapshot Delta:</b> {_fmt(delta)} "
        f"({_fmt(delta_pct, '%')})\n"
        f"<b>Sample:</b> {_fmt(sample)} trades\n"
        f"<b>Span:</b> {_fmt(span)}s"
    )


def _send_order_flow_after_alert(setup, token, chat_id):
    """
    Fetch snapshot AFTER the setup alert.

    This keeps first Telegram alert fast.
    """

    try:
        order_flow = (
            get_order_flow_snapshot(
                setup["pair"]
            )
            or {}
        )

        message = _build_order_flow_message(
            setup,
            order_flow,
        )

        if message:
            _send_telegram(
                token,
                chat_id,
                message,
            )

    except Exception as exc:
        print(
            f"  [live_v1_v6] "
            f"Order-flow snapshot failed for "
            f"{setup.get('pair')}: {exc}"
        )


# ============================================================
# CANDLE FETCH
# ============================================================

def _fetch_pair(pair):
    try:
        df = get_candles(
            pair=pair,
            resolution=RESOLUTION,
            days=2,
        )

        if df is None or df.empty:
            return pair, None, "No candle data"

        return pair, df, None

    except Exception as exc:
        return pair, None, str(exc)


# ============================================================
# PROCESS ONE PAIR
# ============================================================

def _process_pair(pair, dry_run=False):
    try:
        df = get_candles(
            pair=pair,
            resolution=RESOLUTION,
            days=2,
        )

        if df is None or df.empty:
            return []

        setup_out = []

        # EXISTING DETECTION — DO NOT CHANGE.
        qualified = sr_shape_tracker.process_candle(
            pair,
            df,
            dry_run=dry_run,
            setup_out=setup_out,
        )

        # IMPORTANT:
        # process_candle() may return False because the original
        # tracker is waiting for the next candle confirmation.
        # For this LIVE V1-V6 scanner, setup_out itself is enough.
        if not setup_out:
            return []

        setup = setup_out[0]

        strategies = _get_matching_strategies(
            setup
        )

        if not strategies:
            return []

        setup["matched_strategies"] = strategies

        return [setup]

    except Exception as exc:
        print(
            f"  [live_v1_v6] "
            f"{pair} error: {exc}"
        )
        return []


# ============================================================
# MAIN SCAN
# ============================================================

def run_one_cycle():
    print("\n" + "=" * 70)
    print(
        "LIVE V1-V6 CYCLE: "
        f"{datetime.now(timezone.utc).isoformat()} UTC | "
        f"DRY_RUN={DRY_RUN}"
    )
    print("=" * 70)

    try:
        pairs = get_active_pairs()

    except Exception as exc:
        print(
            f"[live_v1_v6] "
            f"get_active_pairs() failed: {exc}"
        )
        return

    total_pairs = len(pairs)

    if MAX_PAIRS_LIVE:
        pairs = pairs[:MAX_PAIRS_LIVE]

    print(
        f"Scanning {len(pairs)} / "
        f"{total_pairs} active pairs..."
    )

    detected = []

    # --------------------------------------------------------
    # Candle fetching in parallel for speed.
    # Detection itself remains existing tracker logic.
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=CANDLE_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                _fetch_pair,
                pair
            ): pair
            for pair in pairs
        }

        fetched = []

        for future in as_completed(futures):
            pair = futures[future]

            try:
                result_pair, df, error = (
                    future.result()
                )

                if error:
                    print(
                        f"  [live_v1_v6] "
                        f"{result_pair}: {error}"
                    )
                    continue

                fetched.append(
                    (result_pair, df)
                )

            except Exception as exc:
                print(
                    f"  [live_v1_v6] "
                    f"{pair} fetch error: {exc}"
                )

    print(
        f"Fetched {len(fetched)} pair(s). "
        f"Running existing V5/V6 tracker logic..."
    )

    # --------------------------------------------------------
    # Existing tracker processing.
    #
    # Sequential intentionally because process_candle()
    # performs Google Sheet operations and existing Telegram
    # operations. We don't parallelize those writes.
    # --------------------------------------------------------

    for pair, df in fetched:

        try:
            setup_out = []

            qualified = (
                sr_shape_tracker.process_candle(
                    pair,
                    df,
                    dry_run=DRY_RUN,
                    setup_out=setup_out,
                )
            )

            # IMPORTANT:
            # Do NOT wait for process_candle() to return True.
            # The original tracker can return False while waiting
            # for the next candle, even though setup_out already
            # contains the current qualifying setup.
            if not setup_out:
                continue

            setup = setup_out[0]

            matched = _get_matching_strategies(
                setup
            )

            if not matched:
                continue

            setup["matched_strategies"] = matched

            detected.append(setup)

        except Exception as exc:
            print(
                f"  [live_v1_v6] "
                f"{pair} processing error: {exc}"
            )

    if not detected:
        print(
            "No V1-V6 setup detected this cycle."
        )
        return

    print(
        f"Detected {len(detected)} "
        f"V1-V6 setup(s): "
        f"{[(x['pair'], x['matched_strategies']) for x in detected]}"
    )

    state = _load_state()

    for setup in detected:

        pair = setup["pair"]
        candle_time = setup["candle_time_ist"]

        key = (
            f"{pair}|"
            f"{candle_time}|"
            f"{','.join(setup['matched_strategies'])}"
        )

        if key in state["alerted_keys"]:
            print(
                f"  Already alerted: {key}"
            )
            continue

        strategies = setup[
            "matched_strategies"
        ]

        resistance = any(
            s in ("V1", "V2", "V3")
            for s in strategies
        )

        if resistance:
            token, chat_id = (
                _get_resistance_credentials()
            )
        else:
            token, chat_id = (
                _get_support_credentials()
            )

        message = _build_setup_message(
            setup,
            strategies,
        )

        if DRY_RUN:
            print(
                "\n[DRY_RUN] Telegram message:\n"
                f"{message}\n"
            )
            continue

        # ----------------------------------------------------
        # FIRST: send setup alert immediately.
        # ----------------------------------------------------

        sent = _send_telegram(
            token,
            chat_id,
            message,
        )

        if not sent:
            print(
                f"  ❌ Alert failed: {pair}"
            )
            continue

        state["alerted_keys"].append(key)
        _save_state(state)

        print(
            f"  ✅ V1-V6 alert sent: "
            f"{pair} -> {strategies}"
        )

        # ----------------------------------------------------
        # SECOND: optional informational snapshot.
        # This does NOT delay the setup alert.
        # ----------------------------------------------------

        _send_order_flow_after_alert(
            setup,
            token,
            chat_id,
        )


def main():
    print(
        "live_v1_v6_scanner.py | "
        f"DRY_RUN={DRY_RUN}"
    )

    try:
        run_one_cycle()

    except Exception as exc:
        print(
            f"[live_v1_v6] "
            f"UNEXPECTED ERROR: {exc}"
        )
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()

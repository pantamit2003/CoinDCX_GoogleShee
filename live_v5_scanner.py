"""
live_v5_scanner.py — single-run live V5 alert (cron-job.org / GitHub Actions).

FLOW (har run mein):
  Stage 2/3 (sheet tracking, Telegram se independent)
  -> Stage 1: process_candle (V5 detection, unchanged)
  -> detect hote hi us pair ka Order Flow snapshot, SAME RUN mein
  -> deterministic selection (sirf 1 setup)
  -> LIVE_V5 bot par turant alert.

Alert next-candle confirmation ka wait NAHI karta (direction pending hoti hai).
Order Flow = get_order_flow_snapshot(pair) ka last ~30 trades ka chhota snapshot
(kuch seconds), 15-min candle ka Delta NAHI — alert mein yahi likha jaata hai.
"""
import html
import json
import os
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests

from data.candles import get_candles
from data.order_flow import get_order_flow_snapshot
from exchange.coindcx import get_active_pairs
import sr_shape_outcome_tracker as sr_shape_tracker
import config

DRY_RUN = os.environ.get("DRY_RUN", "True").strip().lower() in ("1", "true", "yes")

RESOLUTION = "15"
RESOLUTION_MINUTES = 15
MAX_PAIRS_LIVE = 300
SLEEP_BETWEEN_PAIRS = 0.3

STATE_FILE = os.environ.get("LIVE_V5_STATE_FILE", "live_v5_alert_state.json")
MAX_STATE_KEYS = 200


def _get_telegram_credentials():
    token = os.environ.get("LIVE_V5_BOT_TOKEN") or getattr(config, "LIVE_V5_BOT_TOKEN", None)
    chat_id = os.environ.get("LIVE_V5_CHAT_ID") or getattr(config, "LIVE_V5_CHAT_ID", None)
    if token and chat_id:
        return token, chat_id
    return None, None


# ---------------- state (extra safety-net; asli dedupe sheet cooldown se hota hai) ----------------
def _load_state():
    if not os.path.exists(STATE_FILE):
        return {"alerted_keys": []}
    try:
        with open(STATE_FILE, "r") as f:
            st = json.load(f)
        st.setdefault("alerted_keys", [])
        return st
    except Exception:
        return {"alerted_keys": []}


def _save_state(state):
    try:
        state["alerted_keys"] = state["alerted_keys"][-MAX_STATE_KEYS:]
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"  [live_v5_scanner] State save error (non-fatal): {e}")


# ---------------- telegram ----------------
def _send_v5_telegram(text):
    token, chat_id = _get_telegram_credentials()
    if not token or not chat_id:
        print("  [live_v5_scanner] ERROR: LIVE_V5_BOT_TOKEN/LIVE_V5_CHAT_ID nahi mile — alert SKIP.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"  [live_v5_scanner] Telegram failed: {resp.status_code} {resp.text}")
            return False
        return True
    except Exception as e:
        print(f"  [live_v5_scanner] Telegram exception (non-fatal): {e}")
        return False


def _build_v5_message(s):
    def fmt(v, suffix=""):
        if v is None or v == "":
            return "N/A"
        return f"{v}{suffix}"

    lag = s.get("secs_after_candle_close")
    lag_txt = f"~{lag:.0f}s" if lag is not None else "N/A"
    err = s.get("order_flow_error")
    err_line = f"\n  ⚠️ Order Flow Error: {html.escape(str(err))}" if err else ""

    return (
        f"🔔 <b>V5 SETUP DETECTED (Confusion Candle)</b> — DIRECTION PENDING\n"
        f"<i>Yeh alert next-candle confirmation ka wait nahi karta. LONG/SHORT abhi "
        f"confirm NAHI hai. Trade instruction nahi — khud verify karo.</i>\n\n"
        f"<b>Pair:</b> {s['pair']}\n"
        f"<b>Candle Time (IST):</b> {s['candle_time_ist']}\n"
        f"<b>Confusion High / Low:</b> {fmt(s['confusion_high'])} / {fmt(s['confusion_low'])}\n"
        f"<b>Conditional:</b> High ke upar break = LONG (SL {fmt(s['confusion_low'])}) | "
        f"Low ke neeche break = SHORT (SL {fmt(s['confusion_high'])})\n\n"
        f"<b>RVOL_20:</b> {fmt(s['rvol_20'], 'x')} | <b>RVOL_96:</b> {fmt(s['rvol_96'], 'x')}\n"
        f"<b>Price Position:</b> {fmt(s['price_position'])} "
        f"(Level: {fmt(s['sr_level_price'])}, touched {fmt(s['sr_touch_count'])}x)\n"
        f"<b>Candle Shape:</b> {fmt(s['candle_shape'])} "
        f"({fmt(s['shape_strength'])}, body={fmt(s['body_pct'], '%')})\n"
        f"<b>Regime (Recent):</b> {fmt(s['market_regime'])} (score={fmt(s['regime_score'])})\n"
        f"<b>Regime (Background):</b> {fmt(s['background_regime'])} "
        f"(score={fmt(s['background_regime_score'])})\n\n"
        f"<b>Order Flow — ⚠️ SHORT SNAPSHOT, 15-min candle ka Delta NAHI</b>\n"
        f"<i>CoinDCX endpoint sirf last ~30 trades deta hai (kuch seconds). "
        f"Tick-rule se inferred, exchange-provided nahi. Isse candle ke "
        f"buying/selling pressure ka proof mat maano.</i>\n"
        f"  Fetched ≈ {lag_txt} after candle close (candle time = open time maana gaya)\n"
        f"  Aggressive Buy Vol: {fmt(s.get('aggressive_buy_volume'))}\n"
        f"  Aggressive Sell Vol: {fmt(s.get('aggressive_sell_volume'))}\n"
        f"  Snapshot Delta: {fmt(s.get('delta'))} | Snapshot Delta %: {fmt(s.get('delta_pct'), '%')}\n"
        f"  Sample: {fmt(s.get('order_flow_sample_size'))} trades | "
        f"Span: {fmt(s.get('order_flow_span_seconds'))}s"
        f"{err_line}"
    )


# ---------------- selection (existing deterministic logic) ----------------
def _score_setup(s):
    def safe_float(v):
        try:
            return float(v) if v is not None and v != "" else 0.0
        except (TypeError, ValueError):
            return 0.0
    return (abs(safe_float(s.get("delta_pct"))),
            safe_float(s.get("rvol_20")),
            safe_float(s.get("sr_touch_count")))


def select_one_setup(setups):
    if not setups:
        return None
    best = max(_score_setup(s) for s in setups)
    tied = sorted((s for s in setups if _score_setup(s) == best), key=lambda s: s["pair"])
    return tied[0]


# ---------------- order flow attach ----------------
def _attach_order_flow(setup):
    try:
        of = get_order_flow_snapshot(setup["pair"]) or {}
    except Exception as e:
        of = {"Order_Flow_Error": f"unexpected: {e}"}

    fetched_at = datetime.now(timezone.utc)
    candle_close = setup["candle_time_utc"] + timedelta(minutes=RESOLUTION_MINUTES)

    setup["aggressive_buy_volume"] = of.get("Aggressive_Buy_Volume")
    setup["aggressive_sell_volume"] = of.get("Aggressive_Sell_Volume")
    setup["delta"] = of.get("Delta")
    setup["delta_pct"] = of.get("Delta_Pct")
    setup["order_flow_sample_size"] = of.get("Order_Flow_Sample_Size")
    setup["order_flow_span_seconds"] = of.get("Order_Flow_Span_Seconds")
    setup["order_flow_error"] = of.get("Order_Flow_Error")
    setup["secs_after_candle_close"] = (fetched_at - candle_close).total_seconds()
    return setup


# ---------------- one cycle ----------------
def run_one_live_cycle():
    print(f"\n{'=' * 60}")
    print(f"LIVE V5 CYCLE: {datetime.now(timezone.utc).isoformat()} UTC | DRY_RUN={DRY_RUN}")
    print('=' * 60)

    # Stage 2 + 3: sirf sheet tracking. Return value ignore — alert inpe dependent nahi.
    # Stage 1 se PEHLE taaki purane AWAITING rows ka cooldown naye setup ko block na kare.
    print("  Stage 2: resolve_confirmations (tracking only, no alert)...")
    try:
        sr_shape_tracker.resolve_confirmations(dry_run=DRY_RUN, send_individual_telegram=False)
    except Exception as e:
        print(f"  [live_v5_scanner] resolve_confirmations error: {e}")
        traceback.print_exc()

    print("  Stage 3: resolve_pending (outcome tracking)...")
    try:
        sr_shape_tracker.resolve_pending(dry_run=DRY_RUN)
    except Exception as e:
        print(f"  [live_v5_scanner] resolve_pending error: {e}")

    try:
        pairs = get_active_pairs()
    except Exception as e:
        print(f"  [live_v5_scanner] get_active_pairs() error, cycle SKIP: {e}")
        return
    if MAX_PAIRS_LIVE:
        pairs = pairs[:MAX_PAIRS_LIVE]

    print(f"  Stage 1: scanning {len(pairs)} pairs...")
    detected = []
    for pair in pairs:
        try:
            df = get_candles(pair=pair, resolution=RESOLUTION, days=2)
            if df is None or df.empty:
                continue
            out = []
            qualified = sr_shape_tracker.process_candle(pair, df, dry_run=DRY_RUN, setup_out=out)
            if qualified and out:
                # Order Flow detection ke turant baad (scan ke end mein nahi) — lag kam
                detected.append(_attach_order_flow(out[0]))
        except Exception as e:
            print(f"  [live_v5_scanner] {pair} error (skip, continue): {e}")
        time.sleep(SLEEP_BETWEEN_PAIRS)

    if not detected:
        print("  No V5 setup detected this cycle — no alert.")
        return

    print(f"  {len(detected)} setup(s) detected: {[s['pair'] for s in detected]}")
    selected = select_one_setup(detected)

    state = _load_state()
    key = f"{selected['pair']}|{selected['candle_time_ist']}"
    if key in state["alerted_keys"]:
        print(f"  Already alerted for {key} — skip duplicate.")
        return

    message = _build_v5_message(selected)
    if DRY_RUN:
        print(f"  [DRY_RUN] LIVE_V5 message (NOT sent):\n{message}\n")
        return

    if _send_v5_telegram(message):
        state["alerted_keys"].append(key)
        _save_state(state)
        print(f"  ✅ LIVE_V5 alert sent for {selected['pair']}.")
    else:
        print(f"  ❌ LIVE_V5 alert FAILED for {selected['pair']}.")


def main():
    print(f"live_v5_scanner.py | DRY_RUN={DRY_RUN} | state={STATE_FILE}")
    try:
        run_one_live_cycle()
    except Exception as e:
        print(f"[live_v5_scanner] UNEXPECTED error: {e}")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()

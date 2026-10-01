"""
live_v5_scanner.py
====================
NAYA, STANDALONE, SINGLE-RUN SCRIPT — koi existing file ka replacement
nahi hai. intraday_spike_monitor.py, GitHub Actions workflow
(intraday_scan.yml), ya telegram_bot.py mein koi change nahi kiya gaya.

ARCHITECTURE (cron-job.org se, bilkul intraday_spike_monitor.py jaisa):
    Yeh script EK BAAR chalta hai, apna kaam karta hai, exit ho jaata
    hai. Persistent loop/sleep NAHI hai — timing cron-job.org (jo har
    15 min GitHub Actions workflow_dispatch ko externally trigger
    karta hai) handle karta hai. VPS ki zaroorat nahi, jab tak current
    cron-based setup hi use ho raha hai (VPS-conversion instructions
    neeche diye hain agar kabhi zaroorat pade).

!!! VERIFIED TIMING BEHAVIOR (existing code inspect karke confirm kiya,
    assumption se NAHI) !!!
    V5 ka "confirmed" LONG/SHORT setup (jisme direction, entry, SL,
    Order Flow hota hai) CONFUSION candle close hone ke TURANT BAAD
    nahi milta. sr_shape_outcome_tracker.resolve_confirmations() ko
    direction decide karne ke liye CONFUSION candle ke IMMEDIATELY
    NEXT candle ka High/Low chahiye hota hai (_find_next_candle()) —
    jab tak woh agli candle khud close nahi ho jaati, setup
    "AWAITING_CONFIRMATION" hi rehta hai.
    Isliye yeh alert "EARLY" nahi kehlaya ja sakta agar iska matlab
    "confusion-candle-close ke turant baad" ho. Sahi framing: yeh
    alert CONFIRMING candle (immediately-next candle) close hone ke
    turant baad aata hai — jo V5 methodology ki apni definition ke
    hisaab se sabse jaldi possible moment hai jab direction pata chal
    sakta hai. Dono candle-times (Confusion + Confirming) alert mein
    clearly alag dikhaye jaate hain.

KYA KARTA HAI (ek run mein):
    1. Saare active pairs (MAX_PAIRS_LIVE tak — niche "UNVERIFIED"
       comment dekho) ka latest 15-min candle fetch karta hai
    2. Har pair pe EXISTING sr_shape_tracker.process_candle() chalata
       hai (V5 Stage 1 — bilkul unchanged, CONFUSION candle detect
       karta hai, Awaiting sheet mein daalta hai, direction abhi tak
       pata nahi)
    3. EXISTING sr_shape_tracker.resolve_confirmations() chalata hai
       (V5 Stage 2 — bilkul unchanged logic: immediately-next candle
       dhoondhta hai, LONG/SHORT confirm karta hai agar woh candle
       mil jaaye, Order Flow EXISTING call se hi fetch karta hai).
       send_individual_telegram=False pass karte hain (naya, optional
       parameter — default True, purana behavior unaffected) taaki
       is call se Confusion-bot ka per-pair "confirmation" alert na
       jaaye, aur return value (confirmed setups) use karte hain.
    4. Agar is run mein 1+ setup confirm hua, DETERMINISTIC SCORING se
       SIRF EK choose karta hai (Delta_Pct -> RVOL_20 -> SR_Touch_Count
       -> pair-name, is priority order mein — poori detail
       _score_setup() ke docstring mein)
    5. SIRF EK Telegram alert bhejta hai — "V5 BREAKOUT CONFIRMED"
       label ke saath, Order Flow/Delta included, SIRF LIVE_V5 bot pe
       (koi fallback nahi)
    6. EXISTING sr_shape_tracker.resolve_pending() bhi chalata hai
       (Stage 3 — outcome tracking jaari rehta hai, unchanged)

!!! DEPLOYMENT WARNING — DUPLICATE-TRIGGER RISK (verified) !!!
    intraday_spike_monitor.py ka EXISTING run_one_scan() already
    khud sr_shape_tracker.process_candle() / resolve_confirmations()
    / resolve_pending() ko call karta hai (v4.6 integration, jo humne
    uss file mein dekha tha). Agar intraday_spike_monitor.py ka apna
    cron-job.org trigger ACTIVE rehta hai SAATH-SAATH is naye
    live_v5_scanner.py ke, to DONO processes har 15 min mein ek hi
    Google Sheet rows ko parallel/sequentially process karne ki
    koshish karenge — duplicate Confusion-bot alerts, possible race
    conditions, aur duplicate Order Flow API calls ho sakte hain.
    ZAROORI: deployment se pehle decide karo — ya to
    intraday_spike_monitor.py ka cron-job.org trigger band karo, ya
    uske run_one_scan() se teeno sr_shape_tracker calls hata do (yeh
    ek chhota, zaroori — "unnecessary" nahi — change hoga us file
    mein; poori detail final chat response mein, point D).

DUPLICATE-ALERT SAFETY:
    Asli dedupe Google Sheet ke level pe hai — resolve_confirmations()
    har AWAITING_CONFIRMATION row ko EK HI baar process karke
    Confusion_Pending mein move kar deta hai (ya NO_CONFIRMATION mein
    close kar deta hai), existing code mein already aisa hai, maine
    kuch change nahi kiya — isliye wahi candle dobara "confirm" hi
    nahi ho sakti kisi future run mein. Local state-file (neeche)
    sirf ek extra safety-net hai, primary mechanism nahi.

DEPENDENCY: koi NAYI pip package nahi chahiye — existing requirements.txt
(requests, pandas, gspread, google-auth, coindcx) hi kaafi hai.

CHALANE KA TARIKA (local test):
    DRY_RUN=True python live_v5_scanner.py

CHALANE KA TARIKA (GitHub Actions se, production):
    DRY_RUN=False python live_v5_scanner.py

VPS/PERSISTENT-PROCESS CONVERSION (agar future mein zaroorat pade):
    Is file ka run_one_live_cycle() function already self-contained
    hai. VPS pe persistent chalane ke liye bas __main__ block ko
    neeche jaisa wrap kar do (koi aur change nahi chahiye):

        import time
        while True:
            now = datetime.now(timezone.utc).timestamp()
            wait = (900 - (now % 900)) + 10   # agle 15-min candle-close + 10s buffer
            time.sleep(wait)
            try:
                run_one_live_cycle()
            except Exception as e:
                print(f"cycle error, continuing: {e}")
"""

import json
import os
import time
import traceback
from datetime import datetime, timezone

import requests

from data.candles import get_candles
from exchange.coindcx import get_active_pairs
import sr_shape_outcome_tracker as sr_shape_tracker
import config

# ============================================
# CONFIG
# ============================================
# Environment variable se control hota hai — isliye code change kiye
# bina hi test <-> production switch ho sakta hai.
DRY_RUN = os.environ.get("DRY_RUN", "True").strip().lower() in ("1", "true", "yes")

RESOLUTION = "15"
RESOLUTION_MINUTES = 15

# !!! UNVERIFIED — get_active_pairs() ka source code is conversation
# mein kabhi nahi mila, isliye "top 100 by liquidity" CONFIRM nahi kiya
# ja saka. Yeh sirf "list ke pehle 100 pairs, jis bhi order mein
# get_active_pairs() unhe return karta hai" — agar woh order liquidity-
# based nahi hai, to yeh "random/arbitrary 100" ban jaata hai, "top 100"
# nahi. exchange/coindcx.py ka get_active_pairs() dikhao, confirm hote
# hi is comment ko hata denge / sorting add kar denge agar zaroorat ho.
MAX_PAIRS_LIVE = 100
SLEEP_BETWEEN_PAIRS = 0.3

# Dedicated "V5 CONFIRMED" bot ke credentials — env var ya
# telegram_config.py mein:
#   LIVE_V5_BOT_TOKEN = "..."
#   LIVE_V5_CHAT_ID = "..."
# NO FALLBACK (jaanbujhkar) — agar yeh set nahi hain, alert bhejne ki
# koshish hi nahi hogi, koi doosra bot silently reuse nahi hoga. Purana
# Confusion-bot fallback hata diya gaya hai, taaki alert-types kabhi
# accidentally ek hi chat mein mix na hon.
def _get_telegram_credentials():
    token = os.environ.get("LIVE_V5_BOT_TOKEN") or getattr(config, "LIVE_V5_BOT_TOKEN", None)
    chat_id = os.environ.get("LIVE_V5_CHAT_ID") or getattr(config, "LIVE_V5_CHAT_ID", None)
    if token and chat_id:
        return token, chat_id
    return None, None


# Dedupe state file — agar process crash/restart ho, isse pata chalta
# hai ki is candle_time ke liye already alert ja chuka hai ya nahi.
STATE_FILE = os.environ.get("LIVE_V5_STATE_FILE", "live_v5_alert_state.json")


def _load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_alerted_candle_time": None, "last_alerted_pair": None}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"last_alerted_candle_time": None, "last_alerted_pair": None}


def _save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"  [live_v5_scanner] State file save error (non-fatal): {e}")


# ============================================
# TELEGRAM — naya, self-contained (telegram_bot.py touch nahi kiya)
# ============================================
def _send_v5_confirmed_telegram(text):
    token, chat_id = _get_telegram_credentials()
    if not token or not chat_id:
        print("  [live_v5_scanner] ERROR: LIVE_V5_BOT_TOKEN/LIVE_V5_CHAT_ID nahi mile "
              "(no fallback by design) — alert SKIP.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"  [live_v5_scanner] Telegram send failed: {resp.status_code} {resp.text}")
            return False
        return True
    except Exception as e:
        print(f"  [live_v5_scanner] Telegram send exception (non-fatal): {e}")
        return False


def _build_v5_confirmed_message(setup):
    direction = setup.get("break_direction", "")
    emoji = "🟢" if direction == "LONG" else "🔴" if direction == "SHORT" else "⚪"

    def fmt(v, suffix=""):
        if v is None or v == "":
            return "N/A"
        return f"{v}{suffix}"

    return (
        f"{emoji} <b>V5 BREAKOUT CONFIRMED</b> — NOT AUTOMATIC TRADE EXECUTION\n"
        f"<i>(Khud verify karke decide karo. Yeh alert Confirming Candle ke close "
        f"hone ke turant baad aaya hai — Confusion Candle ke turant baad NAHI, "
        f"kyunki V5 ko direction confirm karne ke liye agli candle ka wait karna "
        f"padta hai. Dono times neeche alag dikhaye gaye hain.)</i>\n\n"
        f"<b>Pair:</b> {setup['pair']}\n"
        f"<b>Confusion Candle Time (IST):</b> {setup['candle_time_ist']}\n"
        f"<b>Confirming Candle Time (IST):</b> {setup['next_candle_time_ist']}\n"
        f"<b>Break Direction:</b> {direction}\n\n"
        f"<b>Entry:</b> {fmt(setup['entry_price'])}\n"
        f"<b>Stop Loss:</b> {fmt(setup['stop_loss'])} ({fmt(setup['sl_distance_pct'], '%')} away)\n"
        f"<b>Confusion High/Low:</b> {fmt(setup['confusion_high'])} / {fmt(setup['confusion_low'])}\n\n"
        f"<b>RVOL_20:</b> {fmt(setup['rvol_20'], 'x')} | <b>RVOL_96:</b> {fmt(setup['rvol_96'], 'x')}\n"
        f"<b>Price Position:</b> {fmt(setup['price_position'])} "
        f"(Level: {fmt(setup['sr_level_price'])}, touched {fmt(setup['sr_touch_count'])}x)\n"
        f"<b>Candle Shape:</b> {fmt(setup['candle_shape'])} "
        f"({fmt(setup['shape_strength'])}, body={fmt(setup['body_pct'], '%')})\n\n"
        f"<b>Market Regime (Recent):</b> {fmt(setup['market_regime'])} (score={fmt(setup['regime_score'])})\n"
        f"<b>Market Regime (Background):</b> {fmt(setup['background_regime'])} "
        f"(score={fmt(setup['background_regime_score'])})\n\n"
        f"<b>Order Flow (tick-rule inferred, NOT exchange-provided):</b>\n"
        f"  Aggressive Buy Volume: {fmt(setup['aggressive_buy_volume'])}\n"
        f"  Aggressive Sell Volume: {fmt(setup['aggressive_sell_volume'])}\n"
        f"  Delta: {fmt(setup['delta'])} | Delta %: {fmt(setup['delta_pct'], '%')}\n"
        f"  Sample Size: {fmt(setup['order_flow_sample_size'])} trades | "
        f"Span: {fmt(setup['order_flow_span_seconds'])}s\n"
        f"{'  ⚠️ Order Flow Error: ' + str(setup['order_flow_error']) if setup.get('order_flow_error') else ''}"
    )


# ============================================
# SELECTION — deterministic SCORING (Delta/RVOL/SR-touches se)
# ============================================
def _score_setup(s):
    """
    Priority order (sabse important pehle), sirf EXISTING V5 fields
    reuse karke — koi naya V5 concept invent nahi kiya:

    1. |Delta_Pct| — Order Flow conviction (explicitly required, V5
       research ka core part). Normalized (%) hai isliye pairs ke
       beech fairly comparable hai, raw Delta (size-dependent) nahi.
    2. RVOL_20 — pehla tie-breaker. Already ek EXISTING V5 volume-gate
       hai; jitna zyada utna "loud" breakout candle.
    3. SR_Touch_Count — doosra tie-breaker. Already ek EXISTING V5
       concept — zyada touches = zyada validated level.
    4. Pair name (alphabetically) — final guaranteed-deterministic
       tie-breaker agar upar ke teeno exactly barabar hon.

    Missing Delta_Pct (Order_Flow_Error wale case) ko 0 (neutral)
    treat karte hain — exclude nahi karte, bas priority mein neeche
    chala jaata hai apne aap.
    """
    def safe_float(v):
        try:
            return float(v) if v is not None and v != "" else 0.0
        except (TypeError, ValueError):
            return 0.0

    delta_pct = abs(safe_float(s.get("delta_pct")))
    rvol_20 = safe_float(s.get("rvol_20"))
    sr_touch_count = safe_float(s.get("sr_touch_count"))
    return (delta_pct, rvol_20, sr_touch_count)


def select_one_setup(confirmed_setups):
    """
    confirmed_setups mein se EXACTLY EK setup chunta hai using
    _score_setup() ki priority order. Tie hone par (sab scores
    exactly barabar) pair-name alphabetically sabse pehla choose
    hota hai — isliye selection hamesha 100% reproducible hai, kabhi
    random nahi.
    """
    if not confirmed_setups:
        return None

    best_score = max(_score_setup(s) for s in confirmed_setups)
    tied = [s for s in confirmed_setups if _score_setup(s) == best_score]
    tied.sort(key=lambda s: s["pair"])
    return tied[0]


# ============================================
# EK LIVE CYCLE
# ============================================
def run_one_live_cycle():
    print(f"\n{'=' * 60}")
    print(f"LIVE V5 CYCLE: {datetime.now(timezone.utc).isoformat()} UTC | DRY_RUN={DRY_RUN}")
    print('=' * 60)

    try:
        pairs = get_active_pairs()
    except Exception as e:
        print(f"  [live_v5_scanner] get_active_pairs() error, is cycle SKIP: {e}")
        return
    if MAX_PAIRS_LIVE:
        pairs = pairs[:MAX_PAIRS_LIVE]

    print(f"  Scanning {len(pairs)} pairs (Stage 1: process_candle)...")
    for pair in pairs:
        try:
            df = get_candles(pair=pair, resolution=RESOLUTION, days=2)
            if df is None or df.empty:
                continue
            sr_shape_tracker.process_candle(pair, df, dry_run=DRY_RUN)
        except Exception as e:
            print(f"  [live_v5_scanner] {pair} process_candle error (skip, continue): {e}")
        time.sleep(SLEEP_BETWEEN_PAIRS)

    print("  Stage 2: resolve_confirmations (individual Telegram suppressed)...")
    confirmed_setups = []
    try:
        confirmed_setups = sr_shape_tracker.resolve_confirmations(
            dry_run=DRY_RUN, send_individual_telegram=False
        ) or []
    except Exception as e:
        print(f"  [live_v5_scanner] resolve_confirmations error: {e}")
        traceback.print_exc()

    print("  Stage 3: resolve_pending (outcome tracking, unchanged)...")
    try:
        sr_shape_tracker.resolve_pending(dry_run=DRY_RUN)
    except Exception as e:
        print(f"  [live_v5_scanner] resolve_pending error: {e}")

    if not confirmed_setups:
        print("  No valid V5 setup this cycle — no alert sent.")
        return

    print(f"  {len(confirmed_setups)} setup(s) confirmed this cycle: "
          f"{[s['pair'] for s in confirmed_setups]}")

    selected = select_one_setup(confirmed_setups)
    if selected is None:
        print("  Selection returned None — no alert sent.")
        return

    # ---- Dedupe guard (crash/restart safety) ----
    state = _load_state()
    dedupe_key = f"{selected['pair']}|{selected['candle_time_ist']}"
    if state.get("last_alerted_candle_time") == dedupe_key:
        print(f"  Already alerted for {dedupe_key} (state file) — skip duplicate.")
        return

    message = _build_v5_confirmed_message(selected)

    if DRY_RUN:
        print(f"  [DRY_RUN] V5 CONFIRMED Telegram message (NOT sent):\n{message}\n")
    else:
        sent = _send_v5_confirmed_telegram(message)
        if sent:
            state["last_alerted_candle_time"] = dedupe_key
            state["last_alerted_pair"] = selected["pair"]
            _save_state(state)
            print(f"  ✅ V5 CONFIRMED alert sent for {selected['pair']}.")
        else:
            print(f"  ❌ V5 CONFIRMED alert FAILED to send for {selected['pair']} — will not retry this cycle.")


# ============================================
# ENTRY POINT — single run, cron-job.org/GitHub Actions trigger karta hai
# ============================================
def main():
    print("live_v5_scanner.py — single-run live V5 alert cycle")
    print(f"DRY_RUN = {DRY_RUN}")
    print(f"Resolution = {RESOLUTION_MINUTES}min")
    print(f"State file = {STATE_FILE}\n")

    try:
        run_one_live_cycle()
    except Exception as e:
        # Top-level safety net — is run ka crash agle cron-triggered
        # run ko affect nahi karega, aur partial Sheet writes already
        # unchanged V5 logic ke andar apne try/except se protected hain.
        print(f"[live_v5_scanner] UNEXPECTED error, is run FAILED (agla cron run normally chalega): {e}")
        traceback.print_exc()
        raise  # GitHub Actions ko red/failed dikhana zaroori hai, taaki pata chale


if __name__ == "__main__":
    main()

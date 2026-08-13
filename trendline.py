"""
trendline.py
============
NAYA MODULE — support_resistance.py ko touch nahi karta, alag concept hai.

FARAK support_resistance.py SE:
    S/R = HORIZONTAL lines (fixed price level)
    Trendline = DIAGONAL line (time ke saath price badalta hai)

KYA KARTA HAI:
    1. Recent swing highs/lows dhoondta hai (local pivots)
    2. Unmein se best-fit diagonal line nikalta hai (uptrend ya downtrend)
    3. "Touch points" count karta hai — line ko kitni baar price ne
       chua hai bina todhe (jitna zyada, utni strong/valid line)
    4. Current price ka trendline se distance/position batata hai
    5. BREAK detect karta hai (RETEST abhi implement nahi hua — future
       upgrade ke roop mein add ho sakta hai)
"""

import numpy as np
import pandas as pd


# ============================================
# CONFIG
# ============================================
SWING_LOOKBACK = 3          # kitne candles left-right dekh ke pivot confirm karna hai
TRENDLINE_LOOKBACK = 60     # kitni candles peeche jaake swing points dhoondhne hain (~15 ghante)
MIN_TOUCH_POINTS = 2        # kam se kam itne touches chahiye taaki trendline "valid" mani jaye
TOUCH_TOLERANCE_PCT = 0.3   # itne % ke andar ho to "touch" mana jaayega (exact match zaroori nahi)
RETEST_TOLERANCE_PCT = 0.5  # break ke baad price wapas itne % ke andar aaye to "retest" mana jaayega
                              # (abhi use nahi ho raha, future retest-logic ke liye reserved)


# ============================================
# STEP 1: Swing points dhoondo (pivot highs/lows)
# ============================================
def _find_swing_points(df, lookback=SWING_LOOKBACK):
    highs = df["High"].values
    lows = df["Low"].values
    n = len(df)
    swing_highs = []
    swing_lows = []
    for i in range(lookback, n - lookback):
        window_high = highs[i - lookback: i + lookback + 1]
        if highs[i] == window_high.max():
            swing_highs.append((i, highs[i]))
        window_low = lows[i - lookback: i + lookback + 1]
        if lows[i] == window_low.min():
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


# ============================================
# STEP 2: Best-fit trendline nikalo (uptrend ya downtrend)
# ============================================
def _fit_line_through_points(points):
    if len(points) < 2:
        return None
    xs = np.array([p[0] for p in points])
    ys = np.array([p[1] for p in points])
    slope, intercept = np.polyfit(xs, ys, 1)
    return slope, intercept


def _count_touch_points(df, slope, intercept, is_support, start_idx, end_idx,
                         tolerance_pct=TOUCH_TOLERANCE_PCT):
    touches = 0
    col = "Low" if is_support else "High"
    for i in range(start_idx, end_idx + 1):
        line_price = slope * i + intercept
        actual_price = df[col].iloc[i]
        pct_diff = abs(actual_price - line_price) / line_price * 100
        if pct_diff <= tolerance_pct:
            touches += 1
    return touches


# ============================================
# MAIN FUNCTION — ye intraday_spike_monitor.py se call hoga
# ============================================
def get_trendline_context(df, lookback=TRENDLINE_LOOKBACK):
    recent = df.tail(lookback).reset_index(drop=True)
    if len(recent) < lookback // 2:
        return _empty_context()

    swing_highs, swing_lows = _find_swing_points(recent)

    up_fit = _fit_line_through_points(swing_lows[-4:]) if len(swing_lows) >= 2 else None
    down_fit = _fit_line_through_points(swing_highs[-4:]) if len(swing_highs) >= 2 else None

    last_idx = len(recent) - 1
    current_price = recent["Close"].iloc[-1]
    prev_price = recent["Close"].iloc[-2] if len(recent) > 1 else current_price

    best_context = _empty_context()

    if up_fit and up_fit[0] > 0:
        slope, intercept = up_fit
        touches = _count_touch_points(recent, slope, intercept, is_support=True,
                                        start_idx=max(0, last_idx - lookback), end_idx=last_idx - 1)
        if touches >= MIN_TOUCH_POINTS:
            line_price_now = slope * last_idx + intercept
            line_price_prev = slope * (last_idx - 1) + intercept
            deviation_pct = (current_price - line_price_now) / line_price_now * 100
            just_broke = prev_price >= line_price_prev and current_price < line_price_now
            best_context = {
                "trend": "UPTREND",
                "trendline_price": round(float(line_price_now), 8),
                "touch_points": touches,
                "position": _classify_position(deviation_pct),
                "deviation_pct": round(deviation_pct, 2),
                "just_broke": bool(just_broke),
                "break_direction": "DOWN" if just_broke else None,
            }

    if down_fit and down_fit[0] < 0:
        slope, intercept = down_fit
        touches = _count_touch_points(recent, slope, intercept, is_support=False,
                                        start_idx=max(0, last_idx - lookback), end_idx=last_idx - 1)
        if touches >= MIN_TOUCH_POINTS:
            line_price_now = slope * last_idx + intercept
            line_price_prev = slope * (last_idx - 1) + intercept
            deviation_pct = (current_price - line_price_now) / line_price_now * 100
            just_broke = prev_price <= line_price_prev and current_price > line_price_now
            down_context = {
                "trend": "DOWNTREND",
                "trendline_price": round(float(line_price_now), 8),
                "touch_points": touches,
                "position": _classify_position(deviation_pct),
                "deviation_pct": round(deviation_pct, 2),
                "just_broke": bool(just_broke),
                "break_direction": "UP" if just_broke else None,
            }
            if down_context["touch_points"] >= best_context["touch_points"]:
                best_context = down_context

    return best_context


def _classify_position(deviation_pct, near_threshold=0.5):
    if abs(deviation_pct) <= near_threshold:
        return "AT_TRENDLINE"
    elif deviation_pct > 0:
        return "ABOVE_TRENDLINE"
    else:
        return "BELOW_TRENDLINE"


def _empty_context():
    return {
        "trend": "NO_CLEAR_TREND",
        "trendline_price": None,
        "touch_points": 0,
        "position": "UNKNOWN",
        "deviation_pct": 0.0,
        "just_broke": False,
        "break_direction": None,
    }

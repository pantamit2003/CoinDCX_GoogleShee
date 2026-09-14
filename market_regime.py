"""
market_regime.py
==================
NAYA STANDALONE MODULE — sirf market regime classify karta hai
(UP_TREND / DOWN_TREND / CHOPPY). Koi API call nahi karta, koi
Google Sheet nahi chhuta, koi V5 logic touch nahi karta.

PURPOSE:
V5 (Confusion-candle) setup ke time market "trending" tha ya
"choppy" tha — yeh label karna hai, taaki baad mein V5 WIN vs LOSS
ko regime ke against compare kar sakein. ABHI YEH FILTER NAHI HAI —
sirf analysis data hai. V5 eligibility is module se kabhi decide
nahi hoti.

NO LOOKAHEAD (critical):
Is module ko jo bhi df diya jaaye, use assume karta hai ki caller ne
PEHLE HI future/confirmation/confusion candle EXCLUDE kar di hai.
Yeh module khud kuch bhi "future" nahi dekhta — sirf jo df milta hai
uski TAIL (sabse recent, sabse aakhri rows) use karta hai.

APPROACH (simple, interpretable — koi RSI/ADX/heavy indicators nahi):
Teen chhote, samajhne-laayak components combine kiye hain:
  1. Efficiency Ratio (ER) — net displacement / sum of absolute
     candle-to-candle moves. 1 ke paas = seedha ek direction mein
     chala (trending), 0 ke paas = bahut zigzag (choppy).
  2. Swing structure — recent swing highs aur swing lows badh rahe
     hain (HH+HL = uptrend structure) ya ghat rahe hain (LH+LL =
     downtrend structure) ya mixed (no clear structure).
  3. Close-to-close consistency — kitne candles same direction mein
     closed hue (net upar band vs neeche band).
Teeno ko weighted-average karke ek continuous Regime_Score (-1 to +1)
milta hai. Sign = direction, magnitude = trend-strength/choppiness.
Threshold cross karne par UP_TREND/DOWN_TREND, warna CHOPPY.

Thresholds abhi conservative defaults hain — V5 historical data pe
tune karne ke liye module-level constants rakhe hain, taaki baad mein
sirf yahan ek number badalna ho, poora system dobara likhna na pade.
"""
from support_resistance import find_swing_points

# ============================================
# CONFIG (tuning ke liye — V5 historical data pe test karke adjust karo)
# ============================================
DEFAULT_LOOKBACK = 10         # ~10 ghante ka 15m data (32-48 range ke beech)
MIN_LOOKBACK = 10              # isse kam candles ho to reliable score nahi ban sakta

TREND_SCORE_THRESHOLD = 0.30   # |Regime_Score| >= isse -> TREND, warna CHOPPY

# Component weights (sum = 1.0) — start simple, baad mein tune karna
WEIGHT_EFFICIENCY = 0.5
WEIGHT_STRUCTURE = 0.3
WEIGHT_CONSISTENCY = 0.2


def _trend_of_sequence(seq):
    """
    Ek sequence (jaise swing highs ki list, chronological order mein)
    overall badh rahi hai ya ghat rahi hai, wo detect karta hai —
    consecutive-diff ka majority-sign lekar (single noisy jump se
    galat na ho jaaye, isliye majority-vote).
    Return: 1 (rising), -1 (falling), 0 (mixed/not-enough-points)
    """
    if len(seq) < 2:
        return 0
    diffs = [seq[i] - seq[i - 1] for i in range(1, len(seq))]
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return 0


def _clip(value, lo=-1.0, hi=1.0):
    return max(lo, min(hi, value))


def _insufficient_result(actual_lookback):
    return {
        "Market_Regime": "INSUFFICIENT_DATA",
        "Regime_Score": None,
        "Regime_Lookback": actual_lookback,
        "Trend_Direction": None,
        "Efficiency_Ratio": None,
        "Structure_Score": None,
        "Consistency_Score": None,
    }


def classify_market_regime(df, lookback=DEFAULT_LOOKBACK, min_lookback=MIN_LOOKBACK):
    """
    df: candle dataframe (Open/High/Low/Close columns) — CALLER KI
        ZIMMEDARI hai ki isme confusion/confirmation/future candle na
        ho. Yeh function sirf df ki last `lookback` rows use karta hai.
    lookback: kitni pichli candles use karni hain (default 40, 32-48
        range ke beech).
    min_lookback: isse kam data mile to reliable regime nahi bana
        sakte — INSUFFICIENT_DATA return hota hai (bahut naya pair ya
        data-gap ka rare edge case).

    Return: dict:
        {
            "Market_Regime": "UP_TREND" / "DOWN_TREND" / "CHOPPY" / "INSUFFICIENT_DATA",
            "Regime_Score": float (-1 to +1) or None,
            "Regime_Lookback": int (actually kitni candles use hui),
            "Trend_Direction": "UP" / "DOWN" / "NEUTRAL" or None,
            "Efficiency_Ratio": float or None,
            "Structure_Score": int or None,
            "Consistency_Score": float or None,
        }
    """
    if df is None or df.empty:
        return _insufficient_result(0)

    window = df.tail(lookback).reset_index(drop=True)
    actual_lookback = len(window)

    if actual_lookback < min_lookback:
        return _insufficient_result(actual_lookback)

    closes = window["Close"].astype(float).values

    # ---- 1. Efficiency Ratio (signed by net direction) ----
    net_move = closes[-1] - closes[0]
    sum_abs_moves = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    efficiency_ratio = abs(net_move) / sum_abs_moves if sum_abs_moves > 0 else 0.0
    direction_sign = 1 if net_move > 0 else (-1 if net_move < 0 else 0)
    er_signed = efficiency_ratio * direction_sign

    # ---- 2. Swing structure (HH/HL vs LH/LL) ----
    swing_highs, swing_lows = find_swing_points(window, window=2)
    highs_trend = _trend_of_sequence(swing_highs)
    lows_trend = _trend_of_sequence(swing_lows)
    if highs_trend == 1 and lows_trend == 1:
        structure_signed = 1      # Higher Highs + Higher Lows -> uptrend structure
    elif highs_trend == -1 and lows_trend == -1:
        structure_signed = -1     # Lower Highs + Lower Lows -> downtrend structure
    else:
        structure_signed = 0      # mixed / not-enough-swings -> no clear structure

    # ---- 3. Close-to-close consistency ----
    diffs = closes[1:] - closes[:-1]
    up_count = int((diffs > 0).sum())
    down_count = int((diffs < 0).sum())
    total_moves = up_count + down_count
    consistency_signed = (up_count - down_count) / total_moves if total_moves > 0 else 0.0

    # ---- Combine (weighted) ----
    regime_score = (
        WEIGHT_EFFICIENCY * er_signed
        + WEIGHT_STRUCTURE * structure_signed
        + WEIGHT_CONSISTENCY * consistency_signed
    )
    regime_score = round(_clip(regime_score), 4)

    if regime_score >= TREND_SCORE_THRESHOLD:
        market_regime = "UP_TREND"
        trend_direction = "UP"
    elif regime_score <= -TREND_SCORE_THRESHOLD:
        market_regime = "DOWN_TREND"
        trend_direction = "DOWN"
    else:
        market_regime = "CHOPPY"
        trend_direction = "NEUTRAL"

    return {
        "Market_Regime": market_regime,
        "Regime_Score": regime_score,
        "Regime_Lookback": actual_lookback,
        "Trend_Direction": trend_direction,
        "Efficiency_Ratio": round(float(efficiency_ratio), 4),
        "Structure_Score": structure_signed,
        "Consistency_Score": round(float(consistency_signed), 4),
    }


# ============================================
# LOCAL TESTING
# ============================================
if __name__ == "__main__":
    import pandas as pd
    import numpy as np

    print("market_regime.py — standalone smoke test")

    # Synthetic uptrend
    n = 50
    up_closes = np.linspace(100, 120, n) + np.random.normal(0, 0.3, n)
    up_df = pd.DataFrame({
        "Open": up_closes, "High": up_closes + 0.5,
        "Low": up_closes - 0.5, "Close": up_closes,
    })
    print("Synthetic UP series:", classify_market_regime(up_df))

    # Synthetic choppy/range
    choppy_closes = 100 + np.sin(np.linspace(0, 15, n)) * 2 + np.random.normal(0, 0.3, n)
    choppy_df = pd.DataFrame({
        "Open": choppy_closes, "High": choppy_closes + 0.5,
        "Low": choppy_closes - 0.5, "Close": choppy_closes,
    })
    print("Synthetic CHOPPY series:", classify_market_regime(choppy_df))

    # Insufficient data
    print("Insufficient data:", classify_market_regime(up_df.head(5)))

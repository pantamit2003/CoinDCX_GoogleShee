"""
market_regime.py
==================
STANDALONE MODULE — sirf market regime classify karta hai
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
PEHLE HI confusion candle aur future/confirmation candle EXCLUDE kar
di hai. Yeh module khud kuch bhi "future" nahi dekhta — sirf jo df
milta hai uski TAIL (sabse recent, sabse aakhri rows) use karta hai.

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

TWO-LAYER REGIME (additive — koi existing formula change nahi hua):
  1. RECENT REGIME (existing, unchanged formula/weights/thresholds)
     — last DEFAULT_LOOKBACK (5) candles, confusion candle se PEHLE
     ki sabse recent candles. "Abhi immediately market kis condition
     mein hai."
  2. BACKGROUND REGIME (NAYA) — usi existing classify_market_regime()
     formula ko REUSE karke, ek ALAG window par: recent 5 candles se
     PEHLE ki 15 candles (recent candles background window mein
     INCLUDE nahi hoti, koi overlap nahi). "Is recent movement se
     pehle broader short-term trend kya tha."
     Koi naya scoring formula, koi naya threshold introduce nahi
     hua — sirf input-window alag hai.

Thresholds/weights abhi conservative defaults hain — V5 historical
data pe tune karne ke liye module-level constants rakhe hain, taaki
baad mein sirf yahan number badalna ho, poora system dobara likhna
na pade.
"""
from support_resistance import find_swing_points

# ============================================
# CONFIG (tuning ke liye — V5 historical data pe test karke adjust karo)
# ============================================
# ---- RECENT regime (existing, unchanged) ----
DEFAULT_LOOKBACK = 5
MIN_LOOKBACK = 5

TREND_SCORE_THRESHOLD = 0.30   # |Regime_Score| >= isse -> TREND, warna CHOPPY

# Component weights (sum = 1.0) — existing, unchanged
WEIGHT_EFFICIENCY = 0.5
WEIGHT_STRUCTURE = 0.3
WEIGHT_CONSISTENCY = 0.2

# ---- NAYA: BACKGROUND regime (additive, same formula, alag window) ----
BACKGROUND_LOOKBACK = 15       # ~3h45m on 15m timeframe
BACKGROUND_MIN_LOOKBACK = 15


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
    EXISTING FUNCTION — UNCHANGED FORMULA/WEIGHTS/THRESHOLDS.

    df: candle dataframe (Open/High/Low/Close columns) — CALLER KI
        ZIMMEDARI hai ki isme confusion/confirmation/future candle na
        ho. Yeh function sirf df ki last `lookback` rows use karta hai.
    lookback: kitni pichli candles use karni hain.
    min_lookback: isse kam candles ho to reliable score nahi ban sakta
        — INSUFFICIENT_DATA return hota hai (rare edge case: bahut
        naya pair ya data-gap).

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
# NAYA (additive): RECENT + BACKGROUND REGIME (dono ek call mein)
# ============================================
def classify_recent_and_background_regime(
    df,
    recent_lookback=DEFAULT_LOOKBACK,
    background_lookback=BACKGROUND_LOOKBACK,
):
    """
    NAYA — additive helper. Existing classify_market_regime() ko HI
    reuse karta hai, do baar, do ALAG (NON-OVERLAPPING) windows par:

      1. RECENT     = df ki last `recent_lookback` (5) candles.
      2. BACKGROUND = un recent candles se PEHLE ki `background_lookback`
                      (15) candles — recent window ke saath koi overlap
                      nahi.

    Example (confusion candle @ 18:00, 15-min candles):
        Background: 13:15 -> 16:45  (15 candles)
        Recent:     17:00 -> 17:45  (5 candles)
        Current:    18:00 confusion candle (na Recent na Background mein)

    df: caller (process_candle) se already confusion-candle-EXCLUDED
        df milna chahiye (jaisa classify_market_regime() mein hota
        hai) — yahan bhi wahi guarantee zaroori hai: is function ko
        jo df diya jaaye usme confusion/confirmation/future candle
        NA ho.

    Return: (recent_result: dict, background_result: dict)
        dono dicts classify_market_regime() jaisi hi shape ke hain.
    """
    recent_result = classify_market_regime(
        df, lookback=recent_lookback, min_lookback=recent_lookback
    )

    # Background window = recent window ko chhod kar, usse PEHLE ki
    # candles. Recent candles background mein kabhi include nahi
    # hoti — isliye df ke end se `recent_lookback` candles pehle hi
    # hata di jaati hain, phir usme se background_lookback li jaati hai.
    if df is None or df.empty:
        background_df = df
    else:
        cutoff = len(df) - recent_lookback
        background_df = df.iloc[:max(cutoff, 0)]

    background_result = classify_market_regime(
        background_df, lookback=background_lookback, min_lookback=background_lookback
    )

    return recent_result, background_result


# ============================================
# LOCAL TESTING
# ============================================
if __name__ == "__main__":
    import pandas as pd
    import numpy as np

    print("market_regime.py — standalone smoke test")

    n = 50
    up_closes = np.linspace(100, 120, n) + np.random.normal(0, 0.3, n)
    up_df = pd.DataFrame({
        "Open": up_closes, "High": up_closes + 0.5,
        "Low": up_closes - 0.5, "Close": up_closes,
    })
    print("Synthetic UP series (recent only):", classify_market_regime(up_df))

    choppy_closes = 100 + np.sin(np.linspace(0, 15, n)) * 2 + np.random.normal(0, 0.3, n)
    choppy_df = pd.DataFrame({
        "Open": choppy_closes, "High": choppy_closes + 0.5,
        "Low": choppy_closes - 0.5, "Close": choppy_closes,
    })
    print("Synthetic CHOPPY series (recent only):", classify_market_regime(choppy_df))

    print("Insufficient data (recent):", classify_market_regime(up_df.head(3)))

    recent, background = classify_recent_and_background_regime(up_df)
    print("Recent regime (UP series):", recent)
    print("Background regime (UP series):", background)

    recent_c, background_c = classify_recent_and_background_regime(choppy_df)
    print("Recent regime (CHOPPY series):", recent_c)
    print("Background regime (CHOPPY series):", background_c)

    # Insufficient background (df bahut chhota hai, 15 candles background ke liye kaafi nahi)
    small_df = up_df.head(10)
    r_small, b_small = classify_recent_and_background_regime(small_df)
    print("Small df -> Recent:", r_small, "| Background:", b_small)

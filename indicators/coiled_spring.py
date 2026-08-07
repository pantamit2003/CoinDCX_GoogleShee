"""
indicators/coiled_spring.py

Detects coins that have been sitting in a tight, low-volatility BASING
range for an extended period — a "coiled spring" setup where a breakout
(in EITHER direction) becomes more statistically likely, even though
this signal alone does NOT tell you which direction the breakout will go.

WHY THIS EXISTS:
Some of the biggest % moves (e.g. a coin going 4-5x in a single day)
happen AFTER weeks/months of boring, dead sideways price action with
shrinking volume and shrinking candle ranges. This module flags those
"coiled" coins so you can put them on a watchlist BEFORE the move,
instead of only reacting after the move has already happened.

HOW TO USE THIS (IMPORTANT):
This is a WATCHLIST signal, not an entry signal.
    - Coiled_Spring = True  -> "keep an eye on this coin, energy is building"
    - Breakout_Score (from strategy/scoring_engine.py, already in your
      pipeline) is what tells you WHEN to actually enter, once price
      finally breaks out of the tight range with volume support.

Requires these columns already on df BEFORE calling this function:
    - 'high', 'low' (raw OHLC columns from your candle data)
    - 'Trend_Stage' (from strategy/trend_lifecycle.py -> classify_trend_lifecycle)
    - 'ATR' (from indicators/atr.py -> add_atr)  [optional but recommended]

If your ATR column has a different name (e.g. 'atr_14' instead of 'ATR'),
change the ATR_COLUMN_NAME constant below to match.
"""

import pandas as pd

# If your atr.py names the column something other than "ATR", change this:
ATR_COLUMN_NAME = "ATR"


def detect_coiled_spring(
    df: pd.DataFrame,
    basing_lookback: int = 30,          # how many recent candles to check for BASING
    min_basing_count: int = 20,         # how many of those candles must be BASING
    range_lookback_short: int = 10,     # "recent" range window
    range_lookback_long: int = 60,      # "older/baseline" range window
    range_contraction_threshold: float = 0.5,   # recent range must be < 50% of older range
    atr_lookback: int = 90,             # baseline window for ATR average
    atr_contraction_threshold: float = 0.6,     # current ATR must be < 60% of its 90-period average
) -> pd.DataFrame:
    """
    Adds these columns to df:
        Basing_Duration       - how many of the last `basing_lookback` candles were BASING
        Range_Contraction     - recent range / older range (lower = tighter squeeze)
        ATR_Contraction_Ratio - current ATR / long-term average ATR (lower = quieter)
        Coiled_Spring         - final True/False flag combining all three conditions
    """
    df = df.copy()

    if "Trend_Stage" not in df.columns:
        raise ValueError(
            "detect_coiled_spring requires 'Trend_Stage' column. "
            "Run classify_trend_lifecycle(df) before calling this function."
        )

    # 1. How many of the last N candles were BASING (extended sideways = more "coiled")
    is_basing = (df["Trend_Stage"] == "BASING").astype(int)
    df["Basing_Duration"] = is_basing.rolling(basing_lookback).sum()

    # 2. Range contraction: is the recent trading range much smaller than the
    #    older/bigger range? (squeeze getting tighter)
    recent_range = (
            df["High"].rolling(range_lookback_short).max()
            - df["Low"].rolling(range_lookback_short).min()
    )
    older_range = (
            df["High"].rolling(range_lookback_long).max()
            - df["Low"].rolling(range_lookback_long).min()
    )
    df["Range_Contraction"] = recent_range / older_range.replace(0, pd.NA)

    # 3. ATR contraction: is current volatility much lower than its own average?
    if ATR_COLUMN_NAME in df.columns:
        atr_avg = df[ATR_COLUMN_NAME].rolling(atr_lookback).mean()
        df["ATR_Contraction_Ratio"] = df[ATR_COLUMN_NAME] / atr_avg.replace(0, pd.NA)
    else:
        # ATR not available - don't hard fail, just skip this condition
        df["ATR_Contraction_Ratio"] = pd.NA

    # 4. Combine into final flag.
    #    If ATR wasn't available, only require the other two conditions.
    basing_ok = df["Basing_Duration"] >= min_basing_count
    range_ok = df["Range_Contraction"] < range_contraction_threshold

    if df["ATR_Contraction_Ratio"].notna().any():
        atr_ok = df["ATR_Contraction_Ratio"] < atr_contraction_threshold
        df["Coiled_Spring"] = (basing_ok & range_ok & atr_ok).fillna(False)
    else:
        df["Coiled_Spring"] = (basing_ok & range_ok).fillna(False)

    return df


# ---------- Helper Function (matches your existing project convention) ----------
def add_coiled_spring(df, **kwargs):
    return detect_coiled_spring(df, **kwargs)

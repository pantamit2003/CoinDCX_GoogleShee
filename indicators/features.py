"""
indicators/features.py

Layer 1 — Feature Engineering (pure computation, no decisions).

New institutional-grade metrics for the momentum scanner redesign.
Each function takes a DataFrame with at least:
    Time, Open, High, Low, Close, Volume
and returns the same DataFrame with new columns added.

These are designed to sit ALONGSIDE your existing EMA/MACD/RSI/ATR/Volume
modules, not replace them. Column naming follows your existing convention
(TitleCase, short names) so they slot into main.py / scanner.py cleanly.
"""

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 1. Relative Volume (RVOL)
# ---------------------------------------------------------------------------
def add_rvol(df: pd.DataFrame, windows=(20, 50)) -> pd.DataFrame:
    """
    RVOL = current volume / rolling average volume.
    Adds RVOL_20, RVOL_50 (or whatever windows you pass).
    A value of 3.0 means current bar's volume is 3x the rolling average.
    """
    for w in windows:
        avg_col = f"AvgVolume_{w}"
        rvol_col = f"RVOL_{w}"
        df[avg_col] = df["Volume"].rolling(window=w, min_periods=1).mean()
        df[rvol_col] = df["Volume"] / df[avg_col].replace(0, np.nan)
    return df


# ---------------------------------------------------------------------------
# 2. VWAP + deviation
# ---------------------------------------------------------------------------
def add_vwap(df: pd.DataFrame, reset_daily: bool = True) -> pd.DataFrame:
    """
    Session VWAP = cumulative(Typical Price * Volume) / cumulative(Volume).
    If reset_daily=True, VWAP resets every calendar day (requires 'Time'
    to be a proper datetime column — convert upstream if it's a string/ms).

    Adds: VWAP, VWAP_Deviation (% distance of Close from VWAP)
    """
    df = df.copy()
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    tp_vol = typical_price * df["Volume"]

    if reset_daily and np.issubdtype(df["Time"].dtype, np.datetime64):
        day = df["Time"].dt.date
        cum_tp_vol = tp_vol.groupby(day).cumsum()
        cum_vol = df["Volume"].groupby(day).cumsum()
    else:
        cum_tp_vol = tp_vol.cumsum()
        cum_vol = df["Volume"].cumsum()

    df["VWAP"] = cum_tp_vol / cum_vol.replace(0, np.nan)
    df["VWAP_Deviation"] = ((df["Close"] - df["VWAP"]) / df["VWAP"]) * 100
    return df


# ---------------------------------------------------------------------------
# 3. EMA Slope (rate of change of an EMA, not just its level)
# ---------------------------------------------------------------------------
def add_ema_slope(df: pd.DataFrame, ema_col: str, lookback: int = 5) -> pd.DataFrame:
    """
    Slope = % change of the EMA over `lookback` bars.
    Requires the EMA column (e.g. 'EMA9') to already exist — run your
    existing EMA module first.

    Adds: {ema_col}_Slope
    """
    slope_col = f"{ema_col}_Slope"
    df[slope_col] = df[ema_col].pct_change(periods=lookback) * 100
    return df


# ---------------------------------------------------------------------------
# 4. ATR Expansion Ratio
# ---------------------------------------------------------------------------
def add_atr_expansion(df: pd.DataFrame, baseline_window: int = 50) -> pd.DataFrame:
    """
    Requires 'ATR' column to already exist (from your existing ATR module).
    ATR_Expansion = current ATR / rolling average ATR.
    > 1 = volatility expanding, < 1 = contracting (coiling).

    Adds: ATR_Baseline, ATR_Expansion
    """
    df["ATR_Baseline"] = df["ATR"].rolling(window=baseline_window, min_periods=1).mean()
    df["ATR_Expansion"] = df["ATR"] / df["ATR_Baseline"].replace(0, np.nan)
    return df


# ---------------------------------------------------------------------------
# 5. Velocity & Acceleration (ATR-normalized, comparable across coins)
# ---------------------------------------------------------------------------
def add_velocity(df: pd.DataFrame, lookback: int = 3) -> pd.DataFrame:
    """
    Requires 'ATR' column.
    Velocity = price change over `lookback` bars, normalized by ATR.
    This makes velocity comparable across a 2%-ATR coin and a 6%-ATR coin.
    Acceleration = change in velocity itself (is momentum speeding up or slowing).

    Adds: Velocity, Acceleration
    """
    price_change = df["Close"].diff(periods=lookback)
    df["Velocity"] = price_change / df["ATR"].replace(0, np.nan)
    df["Acceleration"] = df["Velocity"].diff(periods=lookback)
    return df


# ---------------------------------------------------------------------------
# 6. Candle Body Quality (body-to-range ratio, wick rejection)
# ---------------------------------------------------------------------------
def add_candle_quality(df: pd.DataFrame) -> pd.DataFrame:
    """
    Body_Ratio: how much of the candle's range is 'real' body vs wick.
      Close to 1 = strong directional conviction candle.
      Close to 0 = indecision / rejection candle.
    Upper_Wick_Ratio / Lower_Wick_Ratio: rejection strength at top/bottom.

    Adds: Body_Ratio, Upper_Wick_Ratio, Lower_Wick_Ratio
    """
    candle_range = (df["High"] - df["Low"]).replace(0, np.nan)
    body = (df["Close"] - df["Open"]).abs()

    df["Body_Ratio"] = body / candle_range

    upper_wick = df["High"] - df[["Open", "Close"]].max(axis=1)
    lower_wick = df[["Open", "Close"]].min(axis=1) - df["Low"]

    df["Upper_Wick_Ratio"] = upper_wick / candle_range
    df["Lower_Wick_Ratio"] = lower_wick / candle_range
    return df


# ---------------------------------------------------------------------------
# 7. Consecutive Candle Streak
# ---------------------------------------------------------------------------
def add_candle_streak(df: pd.DataFrame) -> pd.DataFrame:
    """
    Counts consecutive bullish or bearish candles ending at each bar.
    Positive number = N consecutive bullish candles.
    Negative number = N consecutive bearish candles.

    Adds: Candle_Streak
    """
    is_bullish = df["Close"] > df["Open"]
    direction = np.where(is_bullish, 1, -1)

    streak = np.zeros(len(df), dtype=int)
    for i in range(len(df)):
        if i == 0:
            streak[i] = direction[i]
        elif direction[i] == direction[i - 1]:
            streak[i] = streak[i - 1] + direction[i]
        else:
            streak[i] = direction[i]

    df["Candle_Streak"] = streak
    return df


# ---------------------------------------------------------------------------
# 8. Pullback Quality (how orderly is a pullback within an established trend)
# ---------------------------------------------------------------------------
def add_pullback_quality(df: pd.DataFrame, trend_ema_col: str = "EMA20", lookback: int = 5) -> pd.DataFrame:
    """
    Requires an EMA column representing the trend (default EMA20).
    Flags bars where price has pulled back toward the trend EMA after an
    advance, and scores the pullback's orderliness:
      - Shallow pullback (small % retrace, low ATR expansion) = healthy
      - Violent pullback (large range, high volume) = disorderly / warning

    Adds: Pullback_Depth (% distance of Close from recent high, only when
          in a pullback state), Pullback_Orderly (bool)
    """
    rolling_high = df["Close"].rolling(window=lookback, min_periods=1).max()
    df["Pullback_Depth"] = ((rolling_high - df["Close"]) / rolling_high) * 100

    # Orderly = pullback depth is shallow (<2%) and candle bodies are small
    # relative to range (indecision, not panic selling)
    if "Body_Ratio" not in df.columns:
        df = add_candle_quality(df)

    df["Pullback_Orderly"] = (df["Pullback_Depth"] < 2.0) & (df["Body_Ratio"] < 0.5)
    return df


# ---------------------------------------------------------------------------
# 9. Recent New Low check (filters out "still bleeding" coins like RIVER —
#    a coin that JUST made a fresh low is not flattening out, it's still
#    actively falling, even if EMAs look tangled/BASING right now)
# ---------------------------------------------------------------------------
def add_recent_low_flag(df: pd.DataFrame, lookback: int = 90, recent_window: int = 10) -> pd.DataFrame:
    """
    Made_New_Low_Recently = True if the lowest Low of the last `lookback`
    candles occurred within the last `recent_window` candles.
    True = coin is still actively making fresh lows (falling knife).
    False = coin's low is further back in the past = genuinely flattening.

    Adds: Bars_Since_90d_Low, Made_New_Low_Recently
    """
    bars_since = []
    lows = df["Low"].values
    for i in range(len(df)):
        start = max(0, i - lookback + 1)
        window = lows[start:i + 1]
        min_pos_in_window = len(window) - 1 - window[::-1].argmin()
        bars_since.append(i - (start + min_pos_in_window))

    df["Bars_Since_90d_Low"] = bars_since
    df["Made_New_Low_Recently"] = df["Bars_Since_90d_Low"] <= recent_window
    return df


# ---------------------------------------------------------------------------
# Convenience: run all Layer 1 features in one call
# ---------------------------------------------------------------------------
def add_all_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Runs the full Layer 1 feature set. Assumes EMA9/EMA20/EMA50/ATR already
    exist on df (i.e. run your existing indicators.ema / indicators.atr
    modules first).
    """
    df = add_rvol(df, windows=(20, 40, 60, 80))
    df = add_vwap(df)

    for ema_col in ["EMA9", "EMA20", "EMA50"]:
        if ema_col in df.columns:
            df = add_ema_slope(df, ema_col)

    if "ATR" in df.columns:
        df = add_atr_expansion(df)
        df = add_velocity(df)

    df = add_candle_quality(df)
    df = add_candle_streak(df)
    df = add_pullback_quality(df)
    df = add_recent_low_flag(df)

    return df

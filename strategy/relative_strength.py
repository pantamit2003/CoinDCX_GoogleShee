"""
strategy/relative_strength.py

Layer 3 — Context: Relative Strength vs BTC.

WHY THIS MODULE EXISTS:
Most altcoins move with BTC, not independently. A coin's own EMA/RSI/volume
can look "strong" purely because BTC is pumping and dragging it along —
that kind of move dies the moment BTC pauses. This module measures how much
of a coin's move is genuinely ITS OWN, above and beyond what BTC did in the
same window. Only moves with real excess strength vs BTC tend to sustain
long enough to be tradable.

Requires: coin_df and btc_df, both already having a 'Time' and 'Close'
column, fetched on the SAME resolution/timeframe.

Adds these columns to the coin_df:
    BTC_Close         -> BTC's close, aligned to coin's timestamps
    Coin_Return        -> coin's % return over `lookback` bars
    BTC_Return          -> BTC's % return over `lookback` bars (same window)
    RS_Excess           -> Coin_Return - BTC_Return (the key number)
    RS_Ratio             -> Close_coin / Close_btc (normalized ratio line)
    RS_Ratio_Slope       -> is the ratio line itself trending up or down
    RS_Label             -> Excellent / Good / Neutral / Weak / Underperforming
"""

import numpy as np
import pandas as pd


class RelativeStrength:

    def __init__(
        self,
        lookback=20,
        ratio_slope_lookback=10,
        excellent_threshold=1.5,   # coin outperforming BTC by >=1.5% over lookback
        good_threshold=0.5,
        weak_threshold=-0.5,
        underperform_threshold=-1.5,
    ):
        self.lookback = lookback
        self.ratio_slope_lookback = ratio_slope_lookback
        self.excellent_threshold = excellent_threshold
        self.good_threshold = good_threshold
        self.weak_threshold = weak_threshold
        self.underperform_threshold = underperform_threshold

    def calculate(self, coin_df: pd.DataFrame, btc_df: pd.DataFrame) -> pd.DataFrame:
        df = coin_df.copy()

        btc_slim = btc_df[["Time", "Close"]].rename(columns={"Close": "BTC_Close"})
        df = df.merge(btc_slim, on="Time", how="left")

        # forward-fill any tiny gaps from slightly misaligned candle timestamps
        df["BTC_Close"] = df["BTC_Close"].ffill()

        df["Coin_Return"] = df["Close"].pct_change(periods=self.lookback) * 100
        df["BTC_Return"] = df["BTC_Close"].pct_change(periods=self.lookback) * 100

        df["RS_Excess"] = df["Coin_Return"] - df["BTC_Return"]

        df["RS_Ratio"] = df["Close"] / df["BTC_Close"].replace(0, np.nan)
        df["RS_Ratio_Slope"] = df["RS_Ratio"].pct_change(periods=self.ratio_slope_lookback) * 100

        df["RS_Label"] = df["RS_Excess"].apply(self._label)

        return df

    def _label(self, excess):
        if pd.isna(excess):
            return "Unknown"
        if excess >= self.excellent_threshold:
            return "Excellent"
        if excess >= self.good_threshold:
            return "Good"
        if excess > self.weak_threshold:
            return "Neutral"
        if excess > self.underperform_threshold:
            return "Weak"
        return "Underperforming"


# ---------- Helper Function (matches your existing convention) ----------
def calculate_relative_strength(coin_df, btc_df, **kwargs):
    return RelativeStrength(**kwargs).calculate(coin_df, btc_df)
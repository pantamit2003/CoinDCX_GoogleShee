"""
strategy/trend_lifecycle.py

Layer 2 — Structural Analysis: Breakout Detection + Trend Lifecycle Classifier.

SYMMETRIC for both directions — this scanner is for futures trading, so
bullish momentum and bearish momentum are treated as equally valid setups.

Raw states (per bar, before persistence filtering):
    BASING
    EARLY_BREAKOUT_UP    / EARLY_BREAKOUT_DOWN
    FRESH_MOMENTUM_UP    / FRESH_MOMENTUM_DOWN
    EXTENDED_UP          / EXTENDED_DOWN
    EXHAUSTION_UP        / EXHAUSTION_DOWN
    REVERSAL_UP          / REVERSAL_DOWN   (trend just flipped direction)

Confirmed state (Trend_Stage column) only changes once the raw state has
persisted for `min_confirm_bars` consecutive bars. This is what fixes the
single-bar flicker problem — a momentum state has to actually hold for a
bit before we call it real, matching the goal of catching momentum that
lasts, not one-bar noise.

Requires these columns to already exist on df (run Layer 0/1 first):
    Close, EMA9, EMA20, EMA50, ATR,
    RVOL_20, ATR_Expansion, Candle_Streak, Upper_Wick_Ratio, Lower_Wick_Ratio,
    Acceleration
"""

import numpy as np
import pandas as pd


class TrendLifecycle:

    def __init__(
        self,
        breakout_lookback=20,
        rvol_threshold=1.5,
        atr_expansion_threshold=1.2,
        extended_atr_multiple=3.0,
        exhaustion_wick_ratio=0.4,
        min_confirm_bars=2,
        min_breakout_atr=0.3,           # break must clear level by >= 0.3x ATR
        min_breakout_body_ratio=0.5,     # breakout candle must have a real body
        max_breakout_opposing_wick=0.3,   # reject candles with big rejection wicks
    ):
        self.breakout_lookback = breakout_lookback
        self.rvol_threshold = rvol_threshold
        self.atr_expansion_threshold = atr_expansion_threshold
        self.extended_atr_multiple = extended_atr_multiple
        self.exhaustion_wick_ratio = exhaustion_wick_ratio
        self.min_confirm_bars = min_confirm_bars
        self.min_breakout_atr = min_breakout_atr
        self.min_breakout_body_ratio = min_breakout_body_ratio
        self.max_breakout_opposing_wick = max_breakout_opposing_wick

    # -----------------------------------------------------------------
    def _raw_state(self, df: pd.DataFrame) -> list:
        rolling_high = (
            df["Close"].rolling(window=self.breakout_lookback, min_periods=1)
            .max().shift(1)
        )
        rolling_low = (
            df["Close"].rolling(window=self.breakout_lookback, min_periods=1)
            .min().shift(1)
        )

        # ---- Raw single-bar break (kept for reference/debugging) ----
        df["Breakout_Up_Raw"] = df["Close"] > rolling_high
        df["Breakout_Down_Raw"] = df["Close"] < rolling_low

        # ---- 2-bar CLOSE confirmation: kills single-wick fakeouts ----
        # A real breakout holds beyond the level for at least 2 closes,
        # not just one bar poking through and reverting.
        df["Breakout_Up_Confirmed"] = df["Breakout_Up_Raw"] & df["Breakout_Up_Raw"].shift(1).fillna(False)
        df["Breakout_Down_Confirmed"] = df["Breakout_Down_Raw"] & df["Breakout_Down_Raw"].shift(1).fillna(False)

        # ---- Minimum decisive distance: break must clear the level by
        # at least `min_breakout_atr` of ATR, not just a marginal poke ----
        distance_up_atr = (df["Close"] - rolling_high) / df["ATR"].replace(0, np.nan)
        distance_down_atr = (rolling_low - df["Close"]) / df["ATR"].replace(0, np.nan)
        decisive_up = distance_up_atr >= self.min_breakout_atr
        decisive_down = distance_down_atr >= self.min_breakout_atr

        # ---- Candle quality at the breakout bar: strong body, small
        # opposing wick (no rejection) ----
        strong_body = df["Body_Ratio"] >= self.min_breakout_body_ratio
        clean_up_wick = df["Upper_Wick_Ratio"] <= self.max_breakout_opposing_wick
        clean_down_wick = df["Lower_Wick_Ratio"] <= self.max_breakout_opposing_wick

        df["Breakout_Up"] = (
            df["Breakout_Up_Confirmed"] & decisive_up & strong_body & clean_up_wick
        )
        df["Breakout_Down"] = (
            df["Breakout_Down_Confirmed"] & decisive_down & strong_body & clean_down_wick
        )

        bullish_aligned = (df["EMA9"] > df["EMA20"]) & (df["EMA20"] > df["EMA50"])
        bearish_aligned = (df["EMA9"] < df["EMA20"]) & (df["EMA20"] < df["EMA50"])

        breakout_up_price = df["Close"].where(df["Breakout_Up"]).ffill()
        breakout_down_price = df["Close"].where(df["Breakout_Down"]).ffill()

        move_up_atr = (df["Close"] - breakout_up_price) / df["ATR"].replace(0, np.nan)
        move_down_atr = (breakout_down_price - df["Close"]) / df["ATR"].replace(0, np.nan)

        states = []
        for i in range(len(df)):
            row = df.iloc[i]
            rvol = row.get("RVOL_20", np.nan)
            atr_exp = row.get("ATR_Expansion", np.nan)
            streak = row.get("Candle_Streak", 0)
            upper_wick = row.get("Upper_Wick_Ratio", 0)
            lower_wick = row.get("Lower_Wick_Ratio", 0)
            accel = row.get("Acceleration", 0)

            prev_state = states[i - 1] if i > 0 else None
            was_up_trend = prev_state is not None and prev_state.endswith("_UP")
            was_down_trend = prev_state is not None and prev_state.endswith("_DOWN")

            # ---------------- BULLISH SIDE ----------------
            if bullish_aligned.iloc[i]:
                if was_down_trend:
                    states.append("REVERSAL_UP")
                    continue

                if row["Breakout_Up"] and rvol >= self.rvol_threshold and atr_exp >= self.atr_expansion_threshold:
                    states.append("EARLY_BREAKOUT_UP")
                    continue

                if streak > 0 and upper_wick >= self.exhaustion_wick_ratio and (pd.isna(rvol) or rvol < 1.0):
                    states.append("EXHAUSTION_UP")
                    continue

                mv = move_up_atr.iloc[i]
                if not pd.isna(mv) and mv >= self.extended_atr_multiple:
                    states.append("EXTENDED_UP")
                    continue

                if not pd.isna(rvol) and rvol >= 1.0 and (pd.isna(accel) or accel >= 0):
                    states.append("FRESH_MOMENTUM_UP")
                    continue

                states.append("BASING")
                continue

            # ---------------- BEARISH SIDE ----------------
            if bearish_aligned.iloc[i]:
                if was_up_trend:
                    states.append("REVERSAL_DOWN")
                    continue

                if row["Breakout_Down"] and rvol >= self.rvol_threshold and atr_exp >= self.atr_expansion_threshold:
                    states.append("EARLY_BREAKOUT_DOWN")
                    continue

                if streak < 0 and lower_wick >= self.exhaustion_wick_ratio and (pd.isna(rvol) or rvol < 1.0):
                    states.append("EXHAUSTION_DOWN")
                    continue

                mv = move_down_atr.iloc[i]
                if not pd.isna(mv) and mv >= self.extended_atr_multiple:
                    states.append("EXTENDED_DOWN")
                    continue

                if not pd.isna(rvol) and rvol >= 1.0 and (pd.isna(accel) or accel <= 0):
                    states.append("FRESH_MOMENTUM_DOWN")
                    continue

                states.append("BASING")
                continue

            # Neither aligned — no clear trend
            states.append("BASING")

        return states

    # -----------------------------------------------------------------
    def _apply_persistence(self, raw_states: list) -> list:
        """
        A raw state only becomes the CONFIRMED state once it has appeared
        for `min_confirm_bars` consecutive bars. Until confirmed, the bar
        keeps the previous confirmed state (or BASING at the very start).
        This kills single-bar flicker.
        """
        confirmed = []
        run_state = None
        run_length = 0
        current_confirmed = "BASING"

        for raw in raw_states:
            if raw == run_state:
                run_length += 1
            else:
                run_state = raw
                run_length = 1

            if run_length >= self.min_confirm_bars:
                current_confirmed = run_state

            confirmed.append(current_confirmed)

        return confirmed

    # -----------------------------------------------------------------
    def classify(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        raw_states = self._raw_state(df)
        df["Trend_Stage_Raw"] = raw_states
        df["Trend_Stage"] = self._apply_persistence(raw_states)

        # Trend Age = bars since CONFIRMED state last changed
        age = []
        current_age = 0
        confirmed = df["Trend_Stage"].tolist()
        for i in range(len(confirmed)):
            if i == 0 or confirmed[i] != confirmed[i - 1]:
                current_age = 0
            else:
                current_age += 1
            age.append(current_age)
        df["Trend_Age"] = age

        return df


# ---------- Helper Function (matches your existing convention) ----------
def classify_trend_lifecycle(df, **kwargs):
    return TrendLifecycle(**kwargs).classify(df)
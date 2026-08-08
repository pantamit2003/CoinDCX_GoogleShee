"""
strategy/trade_plan.py

Layer 7 — Trade Plan Generator.

Converts Confidence + Trend_Stage + ATR into concrete, actionable levels:
    Entry_Trigger      -> NOW / WAIT / NO_ENTRY
    Entry_Price         -> current Close (the price this plan is based on)
    Entry_Quality        -> reuses Confidence, 0-100
    Stop_Loss_Price       -> ATR-based, direction-aware
    Stop_Loss_Percent      -> SL distance as %
    Take_Profit_Price       -> ATR-based, direction-aware, capped to your
                                stated target range
    Take_Profit_Percent      -> TP distance as %
    Expected_Move_Percent     -> same as TP_Percent for now (single-target
                                  system); kept separate so a future
                                  multi-target version can diverge them
    Risk_Reward                 -> TP_Percent / SL_Percent

WHY ATR-BASED, NOT A FIXED %:
A fixed "always risk 0.5%, target 1.5%" rule breaks the moment you scan a
coin with very different volatility than what you tuned the rule on. ATR
adapts the SL/TP distance to how much THIS coin actually moves per bar
right now, so a calm coin gets tight levels and a wild coin gets wider
ones — both proportional to their own noise, not a one-size-fits-all number.

TP CAP NOTE (updated after B-TUT_USDT case, 8 Aug 2026):
max_target_percent was originally 3.0. For genuinely explosive coins (very
high ATR%, e.g. a fresh breakout day), Stop_Loss scales UP with ATR with
no cap, but Take_Profit was hitting the 3.0 ceiling almost immediately —
so Risk_Reward looked artificially bad (e.g. 0.29) for exactly the coins
this system is trying to catch. Raised to 15.0 so the natural
tp_atr_multiple/sl_atr_multiple ratio (2.5x) can play out for high-ATR
breakout coins, while calm coins are unaffected (their natural TP is
already well under the old 3% cap).

Requires: Close, ATR_Percent, Direction, Confidence, Trend_Stage
(ATR_Percent comes from your existing indicators/atr.py module)
"""

import numpy as np
import pandas as pd


class TradePlanGenerator:

    def __init__(
        self,
        entry_confidence_threshold=65,
        wait_confidence_threshold=40,
        sl_atr_multiple=1.0,
        tp_atr_multiple=2.5,
        min_target_percent=1.0,
        max_target_percent=15.0,
        min_sl_percent=0.3,
        min_risk_reward=1.5,
    ):
        self.entry_confidence_threshold = entry_confidence_threshold
        self.wait_confidence_threshold = wait_confidence_threshold
        self.sl_atr_multiple = sl_atr_multiple
        self.tp_atr_multiple = tp_atr_multiple
        self.min_target_percent = min_target_percent
        self.max_target_percent = max_target_percent
        self.min_sl_percent = min_sl_percent
        self.min_risk_reward = min_risk_reward

    # -----------------------------------------------------------------
    def _entry_trigger(self, direction: str, confidence: float, stage: str) -> str:
        if direction == "NONE":
            return "NO_ENTRY"

        # Never chase an already-extended or exhausted move
        if "EXTENDED" in stage or "EXHAUSTION" in stage:
            return "NO_ENTRY"

        if confidence >= self.entry_confidence_threshold:
            return "NOW"
        if confidence >= self.wait_confidence_threshold:
            return "WAIT"
        return "NO_ENTRY"

    # -----------------------------------------------------------------
    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        entry_triggers = []
        entry_prices = []
        entry_qualities = []
        sl_prices = []
        sl_percents = []
        tp_prices = []
        tp_percents = []
        expected_moves = []
        risk_rewards = []

        for i in range(len(df)):
            row = df.iloc[i]
            direction = row.get("Direction", "NONE")
            confidence = row.get("Confidence", 0.0)
            stage = row.get("Trend_Stage", "BASING")
            close = row.get("Close", np.nan)
            atr_percent = row.get("ATR_Percent", np.nan)

            trigger = self._entry_trigger(direction, confidence, stage)
            entry_triggers.append(trigger)
            entry_qualities.append(confidence)

            if trigger == "NO_ENTRY" or pd.isna(atr_percent) or pd.isna(close):
                entry_prices.append(np.nan)
                sl_prices.append(np.nan)
                sl_percents.append(np.nan)
                tp_prices.append(np.nan)
                tp_percents.append(np.nan)
                expected_moves.append(np.nan)
                risk_rewards.append(np.nan)
                continue

            sl_percent = max(atr_percent * self.sl_atr_multiple, self.min_sl_percent)
            tp_percent = atr_percent * self.tp_atr_multiple
            tp_percent = float(np.clip(tp_percent, self.min_target_percent, self.max_target_percent))

            if direction == "LONG":
                sl_price = close * (1 - sl_percent / 100)
                tp_price = close * (1 + tp_percent / 100)
            else:  # SHORT
                sl_price = close * (1 + sl_percent / 100)
                tp_price = close * (1 - tp_percent / 100)

            rr = tp_percent / sl_percent if sl_percent > 0 else np.nan

            # If risk:reward doesn't meet the minimum bar, downgrade the
            # entry trigger — a "good" setup with bad RR isn't tradable.
            if not pd.isna(rr) and rr < self.min_risk_reward and trigger == "NOW":
                trigger = "WAIT"
                entry_triggers[-1] = trigger

            entry_prices.append(close)
            sl_prices.append(sl_price)
            sl_percents.append(sl_percent)
            tp_prices.append(tp_price)
            tp_percents.append(tp_percent)
            expected_moves.append(tp_percent)
            risk_rewards.append(rr)

        df["Entry_Trigger"] = entry_triggers
        df["Entry_Price"] = entry_prices
        df["Entry_Quality"] = entry_qualities
        df["Stop_Loss_Price"] = sl_prices
        df["Stop_Loss_Percent"] = sl_percents
        df["Take_Profit_Price"] = tp_prices
        df["Take_Profit_Percent"] = tp_percents
        df["Expected_Move_Percent"] = expected_moves
        df["Risk_Reward"] = risk_rewards

        return df


# ---------- Helper Function (matches your existing convention) ----------
def generate_trade_plan(df, **kwargs):
    return TradePlanGenerator(**kwargs).generate(df)

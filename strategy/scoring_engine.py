"""
strategy/scoring_engine.py

Layer 5 — Scoring Engine.

Combines everything from Layer 1 (features), Layer 2 (trend lifecycle),
and Layer 3 (relative strength) into transparent 0-100 sub-scores, then
a final Confidence (Momentum Continuation Probability).

This is the layer that turns raw signals into a single comparable number
per coin, which is what makes ranking (Layer 8) possible.

WHY SUB-SCORES INSTEAD OF ONE BLACK-BOX NUMBER:
Each sub-score is independently readable and auditable — you can see
WHY a coin scored 91% (fresh breakout + high RVOL + strong RS) vs a coin
that scored 40% (extended move + fading volume), instead of trusting a
single opaque number. This also makes it backtestable later: you can
check which sub-score combinations actually predicted profitable moves.

Requires these columns on df (run Layers 1-3 first):
    Trend_Stage, Trend_Age,
    RVOL_20, RVOL_50, ATR_Expansion,
    Velocity, Acceleration, EMA9_Slope,
    Candle_Streak, Body_Ratio, Upper_Wick_Ratio, Lower_Wick_Ratio,
    Breakout_Up, Breakout_Down,
    RS_Excess, RS_Label, RS_Ratio_Slope
"""

import numpy as np
import pandas as pd


BULLISH_STAGES = {
    "EARLY_BREAKOUT_UP", "FRESH_MOMENTUM_UP", "EXTENDED_UP",
    "EXHAUSTION_UP", "REVERSAL_UP",
}
BEARISH_STAGES = {
    "EARLY_BREAKOUT_DOWN", "FRESH_MOMENTUM_DOWN", "EXTENDED_DOWN",
    "EXHAUSTION_DOWN", "REVERSAL_DOWN",
}


class ScoringEngine:

    def __init__(
        self,
        # weights must sum to 1.0 across the 5 sub-scores
        weight_trend=0.25,
        weight_momentum=0.25,
        weight_volume=0.20,
        weight_breakout=0.15,
        weight_rs=0.15,
        # confidence penalty caps
        exhaustion_cap=35,
        basing_cap=15,
    ):
        self.weight_trend = weight_trend
        self.weight_momentum = weight_momentum
        self.weight_volume = weight_volume
        self.weight_breakout = weight_breakout
        self.weight_rs = weight_rs
        self.exhaustion_cap = exhaustion_cap
        self.basing_cap = basing_cap

    # -----------------------------------------------------------------
    def _direction(self, stage: str) -> str:
        if stage in BULLISH_STAGES:
            return "LONG"
        if stage in BEARISH_STAGES:
            return "SHORT"
        return "NONE"

    # -----------------------------------------------------------------
    def _trend_score(self, stage: str, trend_age: int) -> float:
        """
        Rewards fresh breakouts and young momentum highest.
        Penalizes extended/exhausted/basing states.
        """
        base_by_stage = {
            "EARLY_BREAKOUT_UP": 95, "EARLY_BREAKOUT_DOWN": 95,
            "FRESH_MOMENTUM_UP": 80, "FRESH_MOMENTUM_DOWN": 80,
            "REVERSAL_UP": 70, "REVERSAL_DOWN": 70,
            "EXTENDED_UP": 50, "EXTENDED_DOWN": 50,
            "EXHAUSTION_UP": 20, "EXHAUSTION_DOWN": 20,
            "BASING": 5,
        }
        score = base_by_stage.get(stage, 5)

        # Freshness bonus/penalty: younger confirmed trend = better,
        # but decay it gently so a 1-bar-old trend isn't overweighted
        # vs a 3-bar-old one that's already proven itself a bit.
        if stage in ("FRESH_MOMENTUM_UP", "FRESH_MOMENTUM_DOWN"):
            if trend_age <= 5:
                score += 5
            elif trend_age > 20:
                score -= 15  # been running a while, getting stale

        return float(np.clip(score, 0, 100))

    # -----------------------------------------------------------------
    def _momentum_score(self, row, direction: str) -> float:
        """
        Combines velocity, acceleration, EMA slope, and candle streak.
        Direction-aware: for SHORT setups, negative velocity/slope is good.
        """
        velocity = row.get("Velocity", 0) or 0
        acceleration = row.get("Acceleration", 0) or 0
        ema_slope = row.get("EMA9_Slope", 0) or 0
        streak = row.get("Candle_Streak", 0) or 0

        if direction == "SHORT":
            velocity, acceleration, ema_slope, streak = (
                -velocity, -acceleration, -ema_slope, -streak
            )

        score = 50.0
        score += np.clip(velocity * 10, -20, 20)
        score += np.clip(acceleration * 5, -10, 10)
        score += np.clip(ema_slope * 10, -10, 10)
        score += np.clip(streak * 3, -10, 10)

        return float(np.clip(score, 0, 100))

    # -----------------------------------------------------------------
    def _volume_score(self, row) -> float:
        """
        High RVOL + expanding ATR = real participation behind the move.
        """
        rvol_20 = row.get("RVOL_20", np.nan)
        atr_exp = row.get("ATR_Expansion", np.nan)

        score = 30.0  # baseline for "average, nothing special"

        if not pd.isna(rvol_20):
            if rvol_20 >= 3.0:
                score += 40
            elif rvol_20 >= 1.5:
                score += 25
            elif rvol_20 >= 1.0:
                score += 10
            else:
                score -= 15  # below-average volume = weak conviction

        if not pd.isna(atr_exp):
            if atr_exp >= 1.5:
                score += 20
            elif atr_exp >= 1.2:
                score += 10
            elif atr_exp < 0.8:
                score -= 10  # volatility contracting, not a live move

        return float(np.clip(score, 0, 100))

    # -----------------------------------------------------------------
    def _breakout_score(self, row, direction: str) -> float:
        """
        Rewards clean breakouts: strong candle body, low opposing wick,
        volume support. A breakout with a big opposing wick is a
        fakeout-prone breakout even if price technically broke the level.
        """
        is_breakout = bool(row.get("Breakout_Up", False)) if direction == "LONG" \
            else bool(row.get("Breakout_Down", False))

        if not is_breakout:
            return 0.0  # no breakout right now = not applicable, score 0

        body_ratio = row.get("Body_Ratio", 0) or 0
        opposing_wick = row.get("Lower_Wick_Ratio", 0) if direction == "LONG" \
            else row.get("Upper_Wick_Ratio", 0)
        opposing_wick = opposing_wick or 0
        rvol_20 = row.get("RVOL_20", 0) or 0

        score = 40.0
        score += body_ratio * 30          # strong-bodied candle = conviction
        score -= opposing_wick * 30       # rejection wick = weak breakout
        score += min(rvol_20, 5) * 6      # volume support, capped contribution

        return float(np.clip(score, 0, 100))

    # -----------------------------------------------------------------
    def _rs_score(self, row, direction: str) -> float:
        """
        Direction-aware: LONG setups want positive RS_Excess (coin beating
        BTC). SHORT setups want negative RS_Excess (coin weaker than BTC —
        i.e. it's a worse asset right now, good short candidate).
        """
        rs_excess = row.get("RS_Excess", np.nan)
        if pd.isna(rs_excess):
            return 50.0  # unknown / not enough data yet, stay neutral

        effective = rs_excess if direction == "LONG" else -rs_excess

        if effective >= 1.5:
            return 95.0
        if effective >= 0.5:
            return 75.0
        if effective > -0.5:
            return 50.0
        if effective > -1.5:
            return 25.0
        return 10.0

    # -----------------------------------------------------------------
    def score(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        directions = []
        trend_scores = []
        momentum_scores = []
        volume_scores = []
        breakout_scores = []
        rs_scores = []
        confidences = []
        reason_codes = []

        for i in range(len(df)):
            row = df.iloc[i]
            stage = row.get("Trend_Stage", "BASING")
            trend_age = row.get("Trend_Age", 0)

            direction = self._direction(stage)
            directions.append(direction)

            t_score = self._trend_score(stage, trend_age)
            m_score = self._momentum_score(row, direction) if direction != "NONE" else 0.0
            v_score = self._volume_score(row)
            b_score = self._breakout_score(row, direction) if direction != "NONE" else 0.0
            r_score = self._rs_score(row, direction) if direction != "NONE" else 50.0

            trend_scores.append(t_score)
            momentum_scores.append(m_score)
            volume_scores.append(v_score)
            breakout_scores.append(b_score)
            rs_scores.append(r_score)

            if direction == "NONE":
                confidence = 0.0
            else:
                confidence = (
                    t_score * self.weight_trend
                    + m_score * self.weight_momentum
                    + v_score * self.weight_volume
                    + b_score * self.weight_breakout
                    + r_score * self.weight_rs
                )

                # Hard caps — no matter how good other sub-scores look,
                # an exhausted or basing coin should never rank as high
                # confidence. This is a deliberate rule, not a blend.
                if stage in ("EXHAUSTION_UP", "EXHAUSTION_DOWN"):
                    confidence = min(confidence, self.exhaustion_cap)
                if stage == "BASING":
                    confidence = min(confidence, self.basing_cap)

            confidences.append(float(np.clip(confidence, 0, 100)))

            # ---- reason codes (for auditability) ----
            codes = [stage]
            if row.get("RVOL_20", 0) and row.get("RVOL_20", 0) >= 1.5:
                codes.append("RVOL_HIGH")
            if row.get("ATR_Expansion", 0) and row.get("ATR_Expansion", 0) >= 1.2:
                codes.append("ATR_EXPANDING")
            if direction != "NONE":
                rs_label = row.get("RS_Label", "Unknown")
                codes.append(f"RS_{rs_label.upper()}")
            reason_codes.append(",".join(codes))

        df["Direction"] = directions
        df["Trend_Score"] = trend_scores
        df["Momentum_Score"] = momentum_scores
        df["Volume_Score"] = volume_scores
        df["Breakout_Score"] = breakout_scores
        df["RS_Score"] = rs_scores
        df["Confidence"] = confidences
        df["Reason_Codes"] = reason_codes

        return df


# ---------- Helper Function (matches your existing convention) ----------
def calculate_scores(df, **kwargs):
    return ScoringEngine(**kwargs).score(df)
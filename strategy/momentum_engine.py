class MomentumEngine:

    @staticmethod
    def calculate(
        ema,
        macd,
        rsi,
        volume_ratio,
        atr_percent,
        structure_score,
        velocity_score
    ):

        score = 0

        # -------------------------
        # EMA (20)
        # -------------------------
        if ema == "Bullish":
            score += 20

        # -------------------------
        # MACD (20)
        # -------------------------
        if macd == "Bullish":
            score += 20

        # -------------------------
        # RSI (15)
        # -------------------------
        if 55 <= rsi <= 70:
            score += 15

        elif 50 <= rsi < 55:
            score += 8

        # -------------------------
        # Volume (15)
        # -------------------------
        if volume_ratio >= 2:
            score += 15

        elif volume_ratio >= 1.5:
            score += 10

        elif volume_ratio >= 1.2:
            score += 7

        elif volume_ratio >= 1:
            score += 3

        # -------------------------
        # ATR (10)
        # -------------------------
        if atr_percent >= 0.20:
            score += 10

        elif atr_percent >= 0.10:
            score += 5

        # -------------------------
        # Price Action (10)
        # -------------------------
        score += round(structure_score * 0.10)

        # -------------------------
        # Velocity (10)
        # -------------------------
        score += round(velocity_score * 0.10)

        # -------------------------
        # Final Signal
        # -------------------------
        if score >= 85:
            signal = "STRONG BUY"

        elif score >= 70:
            signal = "BUY"

        elif score >= 55:
            signal = "WATCH"

        elif score >= 40:
            signal = "NEUTRAL"

        else:
            signal = "IGNORE"

        return score, signal
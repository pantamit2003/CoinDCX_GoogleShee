import pandas as pd


class PriceAction:

    @staticmethod
    def analyze(df: pd.DataFrame):

        last = df.iloc[-1]

        # Previous candles
        high_5 = df["High"].tail(5).max()
        low_5 = df["Low"].tail(5).min()

        high_20 = df["High"].tail(20).max()
        low_20 = df["Low"].tail(20).min()

        # --------------------------------
        # Higher High
        # --------------------------------
        higher_high = (
            df.iloc[-1]["High"] >
            df.iloc[-2]["High"] >
            df.iloc[-3]["High"]
        )

        # --------------------------------
        # Higher Low
        # --------------------------------
        higher_low = (
            df.iloc[-1]["Low"] >
            df.iloc[-2]["Low"] >
            df.iloc[-3]["Low"]
        )

        # --------------------------------
        # Breakout
        # --------------------------------
        breakout = last["Close"] > high_20

        # --------------------------------
        # Breakdown
        # --------------------------------
        breakdown = last["Close"] < low_20

        # --------------------------------
        # Consolidation
        # --------------------------------
        range_percent = ((high_20 - low_20) / low_20) * 100

        consolidation = range_percent < 2

        # --------------------------------
        # Trend Strength
        # --------------------------------
        strength = 0

        if higher_high:
            strength += 25

        if higher_low:
            strength += 25

        if breakout:
            strength += 30

        if consolidation:
            strength += 20

        return {
            "HigherHigh": higher_high,
            "HigherLow": higher_low,
            "Breakout": breakout,
            "Breakdown": breakdown,
            "Consolidation": consolidation,
            "StructureScore": strength
        }
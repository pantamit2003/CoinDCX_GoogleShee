import pandas as pd


class EMACross:

    @staticmethod
    def check(df: pd.DataFrame):

        last = df.iloc[-1]
        previous = df.iloc[-2]

        # Bullish Cross
        if previous["EMA9"] <= previous["EMA20"] and last["EMA9"] > last["EMA20"]:
            return "BUY"

        # Bearish Cross
        elif previous["EMA9"] >= previous["EMA20"] and last["EMA9"] < last["EMA20"]:
            return "SELL"

        # Trend
        elif last["EMA9"] > last["EMA20"]:
            return "BULLISH"

        else:
            return "BEARISH"
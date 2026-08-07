import pandas as pd


class MACDIndicator:

    def __init__(
        self,
        fast=12,
        slow=26,
        signal=9
    ):
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def calculate(self, df: pd.DataFrame):

        df = df.copy()

        ema_fast = (
            df["Close"]
            .ewm(span=self.fast, adjust=False)
            .mean()
        )

        ema_slow = (
            df["Close"]
            .ewm(span=self.slow, adjust=False)
            .mean()
        )

        df["MACD"] = ema_fast - ema_slow

        df["Signal"] = (
            df["MACD"]
            .ewm(span=self.signal, adjust=False)
            .mean()
        )

        df["Histogram"] = (
            df["MACD"] - df["Signal"]
        )

        return df


# ---------- Helper Function ----------
def add_macd(
    df,
    fast=12,
    slow=26,
    signal=9
):
    """
    Shortcut function used by other files.
    """
    return MACDIndicator(
        fast,
        slow,
        signal
    ).calculate(df)
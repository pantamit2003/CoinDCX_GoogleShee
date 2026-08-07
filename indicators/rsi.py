import pandas as pd


class RSIIndicator:

    def __init__(self, period=14):
        self.period = period

    def calculate(self, df: pd.DataFrame):

        df = df.copy()

        delta = df["Close"].diff()

        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

        avg_gain = gain.ewm(
            alpha=1 / self.period,
            adjust=False
        ).mean()

        avg_loss = loss.ewm(
            alpha=1 / self.period,
            adjust=False
        ).mean()

        rs = avg_gain / avg_loss

        df["RSI"] = 100 - (100 / (1 + rs))

        return df


# ---------- Helper Function ----------
def add_rsi(df, period=14):
    """
    Shortcut function used by other files.
    """
    return RSIIndicator(period).calculate(df)
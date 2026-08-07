import pandas as pd


class EMAIndicator:

    def __init__(self, periods=None):

        if periods is None:
            periods = [9, 20, 50, 200]

        self.periods = periods

    def calculate(self, df: pd.DataFrame):

        df = df.copy()

        for period in self.periods:
            df[f"EMA{period}"] = (
                df["Close"]
                .ewm(span=period, adjust=False)
                .mean()
            )

        return df


# ---------- Helper Function ----------
def add_ema(df, periods=None):
    """
    Shortcut function used by other files.
    """
    return EMAIndicator(periods).calculate(df)
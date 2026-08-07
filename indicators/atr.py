import pandas as pd


def add_atr(df, period=14):

    df = df.copy()

    # Previous Close
    df["PrevClose"] = df["Close"].shift(1)

    # True Range
    high_low = df["High"] - df["Low"]
    high_prev = (df["High"] - df["PrevClose"]).abs()
    low_prev = (df["Low"] - df["PrevClose"]).abs()

    df["TR"] = pd.concat(
        [high_low, high_prev, low_prev],
        axis=1
    ).max(axis=1)

    # ATR
    df["ATR"] = df["TR"].rolling(period).mean()

    # ATR Percentage
    df["ATR_Percent"] = (
        df["ATR"] / df["Close"]
    ) * 100

    # Cleanup
    df.drop(
        columns=[
            "PrevClose",
            "TR"
        ],
        inplace=True
    )

    return df
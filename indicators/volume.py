import pandas as pd


def add_volume(df, period=20):

    df = df.copy()

    # Average Volume
    df["AvgVolume"] = (
        df["Volume"]
        .rolling(period)
        .mean()
    )

    # Volume Ratio
    df["VolumeRatio"] = (
        df["Volume"] /
        df["AvgVolume"]
    )

    # Volume Spike

    conditions = [
        df["VolumeRatio"] >= 3,
        df["VolumeRatio"] >= 2,
        df["VolumeRatio"] >= 1.5
    ]

    choices = [
        "HIGH",
        "MEDIUM",
        "LOW"
    ]

    df["VolumeSpike"] = "NO"

    df.loc[df["VolumeRatio"] >= 1.5, "VolumeSpike"] = "LOW"
    df.loc[df["VolumeRatio"] >= 2, "VolumeSpike"] = "MEDIUM"
    df.loc[df["VolumeRatio"] >= 3, "VolumeSpike"] = "HIGH"

    return df
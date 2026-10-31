import time
from datetime import datetime, timezone

import pandas as pd
import requests


BASE_URL = "https://public.coindcx.com/market_data/candlesticks"


def get_candles_2h(pair="B-BTC_USDT", days=10):
    """
    Fetch CoinDCX futures 1-hour candles and aggregate them into
    complete, UTC-aligned 2-hour candles.

    Returns columns: Time, Open, High, Low, Close, Volume
    """
    now_seconds = int(time.time())
    from_seconds = now_seconds - (days * 24 * 60 * 60)

    params = {
        "pair": pair,
        "from": from_seconds,
        "to": now_seconds,
        "resolution": "60",
        "pcode": "f",
    }

    response = requests.get(BASE_URL, params=params, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(
            f"CoinDCX 1h candle API Error {response.status_code}: "
            f"{response.text[:500]}"
        )

    payload = response.json()
    if payload.get("s") != "ok":
        raise RuntimeError(
            f"CoinDCX returned unexpected candle response: "
            f"{str(payload)[:500]}"
        )

    data = payload.get("data", [])
    if not data:
        return pd.DataFrame(
            columns=["Time", "Open", "High", "Low", "Close", "Volume"]
        )

    df = pd.DataFrame(data).rename(
        columns={
            "time": "Time",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )

    required = ["Time", "Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise RuntimeError(f"Candle response missing columns: {missing}")

    # CoinDCX returns candle open time in milliseconds.
    df["Time"] = pd.to_datetime(df["Time"], unit="ms", utc=True)
    for column in ["Open", "High", "Low", "Close", "Volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = (
        df.dropna(subset=required)
        .sort_values("Time")
        .drop_duplicates(subset=["Time"], keep="last")
        .reset_index(drop=True)
    )

    if df.empty:
        return pd.DataFrame(columns=required)

    # Remove a currently forming hourly candle.
    now_utc = pd.Timestamp.now(tz="UTC")
    last_hour_open = df["Time"].iloc[-1]
    if last_hour_open + pd.Timedelta(hours=1) > now_utc:
        df = df.iloc[:-1].copy()

    if df.empty:
        return pd.DataFrame(columns=required)

    # Align groups to UTC 00:00, 02:00, 04:00, etc.
    df["_group"] = df["Time"].dt.floor("2h")

    rows = []
    for group_time, group in df.groupby("_group", sort=True):
        group = group.sort_values("Time")

        # Keep only complete pairs of hourly candles exactly at
        # group_time and group_time + 1 hour.
        expected_times = [
            group_time,
            group_time + pd.Timedelta(hours=1),
        ]
        if len(group) != 2 or list(group["Time"]) != expected_times:
            continue

        # Exclude any 2-hour candle that has not fully closed yet.
        if group_time + pd.Timedelta(hours=2) > now_utc:
            continue

        rows.append(
            {
                "Time": group_time.tz_localize(None),
                "Open": float(group["Open"].iloc[0]),
                "High": float(group["High"].max()),
                "Low": float(group["Low"].min()),
                "Close": float(group["Close"].iloc[-1]),
                "Volume": float(group["Volume"].sum()),
            }
        )

    return pd.DataFrame(
        rows,
        columns=["Time", "Open", "High", "Low", "Close", "Volume"],
    ).reset_index(drop=True)

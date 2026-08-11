import requests
import pandas as pd
import time
from datetime import datetime, timezone


class CandleData:

    BASE_URL = "https://public.coindcx.com/market_data/candlesticks"

    def get_candles(
            self,
            pair="B-BTC_USDT",
            resolution="1",
            days=2
    ):

        # CoinDCX expects Unix timestamp in seconds
        to_time = int(time.time())
        from_time = to_time - (days * 24 * 60 * 60)

        params = {
            "pair": pair,
            "from": from_time,
            "to": to_time,
            "resolution": resolution,
            "pcode": "f"
        }

        response = requests.get(self.BASE_URL, params=params)

        if response.status_code != 200:
            raise Exception(
                f"API Error {response.status_code}\n{response.text}"
            )

        data = response.json()

        if data["s"] != "ok":
            raise Exception("No candle data received.")

        df = pd.DataFrame(data["data"])

        df.rename(columns={
            "time": "Time",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        }, inplace=True)

        df["Time"] = pd.to_datetime(df["Time"], unit="ms")

        numeric_columns = [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]

        for col in numeric_columns:
            df[col] = pd.to_numeric(df[col])

        df = df.sort_values("Time").reset_index(drop=True)

        # ---- AGAR daily candle hai aur last candle AAJ (UTC) ki hai,
        # matlab abhi bann rahi hai (incomplete) — usko hata do. Sirf
        # poori ban chuki (closed) candle hi "current" data maana
        # jayega, taaki RVOL/High/Close sab sahi (complete) candle pe
        # calculate hon. ----
        if resolution.upper() in ("1D", "D") and not df.empty:
            last_candle_date = df["Time"].iloc[-1].date()
            today_utc = datetime.now(timezone.utc).date()
            if last_candle_date == today_utc:
                df = df.iloc[:-1].reset_index(drop=True)

        # ---- INTRADAY resolutions (15, 5, 60, etc.) ke liye bhi wahi
        # cheez — pehle yeh check sirf daily ke liye tha, isliye 15-min
        # jaisi resolutions mein last row abhi-abhi ban rahi (incomplete)
        # candle ho sakti thi. Ab yahan bhi: last candle ka expected
        # close-time (Time + resolution minutes) nikalo, agar abhi tak
        # woh time nahi aaya, matlab candle abhi live/forming hai -> hata
        # do. Isse .iloc[-1] hamesha ek guaranteed CLOSED candle hoga,
        # chahe koi bhi resolution use ho rahi ho. ----
        elif not df.empty:
            try:
                resolution_minutes = int(resolution)
            except ValueError:
                resolution_minutes = None  # koi anjaan resolution string, skip

            if resolution_minutes:
                last_candle_time = df["Time"].iloc[-1]
                if last_candle_time.tzinfo is None:
                    last_candle_time = last_candle_time.tz_localize("UTC")
                expected_close = last_candle_time + pd.Timedelta(minutes=resolution_minutes)
                now_utc = datetime.now(timezone.utc)
                if now_utc < expected_close:
                    df = df.iloc[:-1].reset_index(drop=True)

        return df


# ---------- Helper Function ----------
def get_candles(pair="B-BTC_USDT", resolution="1", days=2):
    return CandleData().get_candles(pair, resolution, days)

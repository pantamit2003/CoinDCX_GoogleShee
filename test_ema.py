from data.candles import CandleData
from indicators.ema import EMAIndicator

# Candle Data
candles = CandleData()

df = candles.get_candles(
    pair="B-BTC_USDT",
    resolution="1"
)

# EMA
ema = EMAIndicator()

df = ema.calculate(df)

# Last 10 Rows
print(
    df[
        [
            "Time",
            "Close",
            "EMA9",
            "EMA20",
            "EMA50",
            "EMA200"
        ]
    ].tail(10)
)
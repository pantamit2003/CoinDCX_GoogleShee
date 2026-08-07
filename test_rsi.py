from data.candles import CandleData
from indicators.rsi import RSIIndicator

candles = CandleData()

df = candles.get_candles(
    pair="B-BTC_USDT",
    resolution="1"
)

rsi = RSIIndicator()

df = rsi.calculate(df)

print(
    df[
        [
            "Time",
            "Close",
            "RSI"
        ]
    ].tail(15)
)
from data.candles import CandleData
from indicators.macd import MACDIndicator

candles = CandleData()

df = candles.get_candles(
    pair="B-BTC_USDT",
    resolution="1"
)

macd = MACDIndicator()

df = macd.calculate(df)

print(
    df[
        [
            "Time",
            "Close",
            "MACD",
            "Signal",
            "Histogram"
        ]
    ].tail(15)
)

last = df.iloc[-1]

print("\nCurrent Status")

if last["MACD"] > last["Signal"]:
    print("MACD : BULLISH")
else:
    print("MACD : BEARISH")
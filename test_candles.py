from data.candles import CandleData

cd = CandleData()

df = cd.get_candles(
    pair="B-BTC_USDT",
    resolution="1"
)

print(df.head())

print()

print(df.tail())

print()

print(df.shape)
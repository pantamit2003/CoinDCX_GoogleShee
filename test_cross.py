from data.candles import CandleData
from indicators.ema import EMAIndicator
from strategy.ema_cross import EMACross

candles = CandleData()

df = candles.get_candles(
    pair="B-BTC_USDT",
    resolution="1"
)

ema = EMAIndicator()

df = ema.calculate(df)

signal = EMACross.check(df)

print()

print("Current Signal :", signal)

print()

print(df[["Close","EMA9","EMA20"]].tail())